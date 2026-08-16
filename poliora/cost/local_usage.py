"""Read usage that AI coding tools already record on this computer.

Claude Code and Codex both keep a local session log containing, per turn, the
model that served it and the token counts it consumed. Some logs also carry the
account's plan type and how much of its quota the window has used. That is
enough to answer "what did I actually use last month, on which models, on which
plan" without an account credential and without contacting any provider.

What this module extracts, and nothing else:

* model id, token counts, timestamps
* plan type and quota utilization where the tool records it
* the session file's own identifier, hashed

It never reads prompts, responses, file contents, command history, or
credentials. Those live in the same files; the parsers below step around them
deliberately, and :func:`_usage_from_claude_record` is written to pull named
numeric fields rather than to copy records wholesale.

**Subscription turns are not spend.** A Claude Max or ChatGPT Pro turn has
already been paid for by the flat fee, so it is recorded at zero cost. What the
turn *would* have cost at list API rates is recorded separately as
``equivalent_api_cost_usd``. The gap between the flat fee and that number is the
honest basis for a right-sizing decision: a $200 plan returning $40 of
equivalent value is a plan to downgrade, and one returning $3,000 is a bargain.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from poliora.cost.pricing import PricingRegistry
from poliora.cost.usage import UsageEvent, parse_timestamp

# A turn's cost basis. Subscription turns carry no token-denominated charge.
SUBSCRIPTION = "subscription-included"
API_BILLED = "api-billed"


@dataclass(frozen=True)
class DetectedPlan:
    """A tool's plan and quota, as the tool itself recorded it locally."""

    tool: str
    plan_type: str | None = None
    quota_used_pct: float | None = None
    quota_window_minutes: int | None = None
    quota_resets_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the detected plan."""
        return asdict(self)


@dataclass(frozen=True)
class LocalUsageScan:
    """Everything one tool's local logs could tell us, plus what they could not."""

    tool: str
    display_name: str
    available: bool
    events: tuple[UsageEvent, ...] = ()
    plan: DetectedPlan | None = None
    sessions: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    equivalent_api_cost_usd: float = 0.0
    unpriced_models: tuple[str, ...] = ()
    note: str = ""

    @property
    def total_tokens(self) -> int:
        """Total tokens observed across this tool's sessions."""
        return sum(event.total_tokens for event in self.events)

    def to_dict(self) -> dict[str, object]:
        """Serialize a scan for the CLI and the dashboard."""
        return {
            "tool": self.tool,
            "display_name": self.display_name,
            "available": self.available,
            "requests": len(self.events),
            "sessions": self.sessions,
            "total_tokens": self.total_tokens,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "equivalent_api_cost_usd": round(self.equivalent_api_cost_usd, 4),
            "plan": self.plan.to_dict() if self.plan else None,
            "models": [
                {"model": model, "requests": count}
                for model, count in sorted(
                    self.model_mix().items(), key=lambda item: item[1], reverse=True
                )
            ],
            "unpriced_models": list(self.unpriced_models),
            "note": self.note,
        }

    def model_mix(self) -> dict[str, int]:
        """Return request counts per model, most-used first when sorted."""
        mix: dict[str, int] = {}
        for event in self.events:
            mix[event.model] = mix.get(event.model, 0) + 1
        return mix


def scan_local_usage(
    *,
    home: Path | None = None,
    since: datetime | None = None,
    registry: PricingRegistry | None = None,
) -> list[LocalUsageScan]:
    """Read every supported tool's local usage logs on this computer."""
    base = home or Path.home()
    active_registry = registry or PricingRegistry()
    return [
        read_claude_code_usage(home=base, since=since, registry=active_registry),
        read_codex_usage(home=base, since=since, registry=active_registry),
    ]


def read_claude_code_usage(
    *,
    home: Path | None = None,
    since: datetime | None = None,
    registry: PricingRegistry | None = None,
) -> LocalUsageScan:
    """Read Claude Code's per-session JSONL logs."""
    root = (home or Path.home()) / ".claude" / "projects"
    if not root.is_dir():
        return LocalUsageScan(
            tool="claude-code",
            display_name="Claude Code",
            available=False,
            note="No Claude Code session logs were found on this computer.",
        )

    active_registry = registry or PricingRegistry()
    events: list[UsageEvent] = []
    plan_type: str | None = None
    sessions = 0

    for path in sorted(root.glob("*/*.jsonl")):
        sessions += 1
        trace = _anonymous_trace("claude-code", path)
        for record in _iter_json_lines(path):
            plan_type = plan_type or _find_plan_type(record)
            event = _usage_from_claude_record(record, trace_id=trace)
            if event is not None and _within(event, since):
                events.append(event)

    return _finish(
        tool="claude-code",
        display_name="Claude Code",
        events=events,
        sessions=sessions,
        plan=DetectedPlan(tool="claude-code", plan_type=plan_type),
        registry=active_registry,
        note=(
            "Token counts come from Claude Code's own session logs. Subscription "
            "turns are recorded at zero cost; the equivalent API value is shown "
            "separately."
        ),
    )


def read_codex_usage(
    *,
    home: Path | None = None,
    since: datetime | None = None,
    registry: PricingRegistry | None = None,
) -> LocalUsageScan:
    """Read the Codex CLI's rollout logs, including plan and quota."""
    base = (home or Path.home()) / ".codex"
    if not base.is_dir():
        return LocalUsageScan(
            tool="codex",
            display_name="Codex CLI",
            available=False,
            note="No Codex session logs were found on this computer.",
        )

    active_registry = registry or PricingRegistry()
    events: list[UsageEvent] = []
    plan: DetectedPlan | None = None
    sessions = 0

    for path in sorted(_codex_session_files(base)):
        sessions += 1
        trace = _anonymous_trace("codex", path)
        model = "unknown"
        for record in _iter_json_lines(path):
            model = _find_model(record) or model
            plan = _codex_plan(record) or plan
            event = _usage_from_codex_record(record, model=model, trace_id=trace)
            if event is not None and _within(event, since):
                events.append(event)

    return _finish(
        tool="codex",
        display_name="Codex CLI",
        events=events,
        sessions=sessions,
        plan=plan or DetectedPlan(tool="codex"),
        registry=active_registry,
        note=(
            "Token counts and plan come from the Codex CLI's own rollout logs. "
            "ChatGPT-subscription turns are recorded at zero cost."
        ),
    )


# --- record parsing --------------------------------------------------------


def _usage_from_claude_record(record: dict[str, Any], *, trace_id: str) -> UsageEvent | None:
    """Build an event from one Claude Code assistant record, if it has usage."""
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None

    # Cache creation is billed as input; cache reads are billed at the cached
    # rate. Both are input tokens, so the total must include them or the
    # equivalent-cost figure understates real consumption badly.
    cache_read = _count(usage, "cache_read_input_tokens")
    cache_creation = _count(usage, "cache_creation_input_tokens")
    input_tokens = _count(usage, "input_tokens") + cache_creation + cache_read
    output_tokens = _count(usage, "output_tokens")
    if input_tokens <= 0 and output_tokens <= 0:
        return None

    details = usage.get("output_tokens_details")
    reasoning = _count(details, "thinking_tokens") if isinstance(details, dict) else 0

    return UsageEvent(
        provider="anthropic",
        model=str(message.get("model") or "unknown"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cache_read,
        reasoning_tokens=reasoning,
        cost_usd=0.0,
        operation="claude-code",
        trace_id=trace_id,
        timestamp=_timestamp(record),
        metadata={"source": "claude-code-local-log", "content_collected": False},
    )


def _usage_from_codex_record(
    record: dict[str, Any], *, model: str, trace_id: str
) -> UsageEvent | None:
    """Build an event from one Codex ``token_count`` event."""
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    # last_token_usage is this turn; total_token_usage is cumulative and would
    # multiply-count if summed across a session.
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = _count(usage, "input_tokens")
    output_tokens = _count(usage, "output_tokens")
    if input_tokens <= 0 and output_tokens <= 0:
        return None

    return UsageEvent(
        provider="openai",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=min(_count(usage, "cached_input_tokens"), input_tokens),
        reasoning_tokens=_count(usage, "reasoning_output_tokens"),
        cost_usd=0.0,
        operation="codex",
        trace_id=trace_id,
        timestamp=_timestamp(record),
        metadata={"source": "codex-local-log", "content_collected": False},
    )


def _codex_plan(record: dict[str, Any]) -> DetectedPlan | None:
    """Extract plan type and quota utilization from a Codex rate-limit block."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return None
    primary = limits.get("primary") if isinstance(limits.get("primary"), dict) else {}
    resets_at = primary.get("resets_at")
    return DetectedPlan(
        tool="codex",
        plan_type=_text(limits.get("plan_type")),
        quota_used_pct=_number(primary.get("used_percent")),
        quota_window_minutes=int(primary["window_minutes"])
        if isinstance(primary.get("window_minutes"), (int, float))
        else None,
        quota_resets_at=datetime.fromtimestamp(resets_at, tz=timezone.utc).isoformat()
        if isinstance(resets_at, (int, float))
        else None,
    )


# --- shared helpers --------------------------------------------------------


def _finish(
    *,
    tool: str,
    display_name: str,
    events: list[UsageEvent],
    sessions: int,
    plan: DetectedPlan | None,
    registry: PricingRegistry,
    note: str,
) -> LocalUsageScan:
    """Price the observed usage at list API rates and assemble the scan."""
    equivalent = 0.0
    unpriced: set[str] = set()
    priced: list[UsageEvent] = []

    for event in events:
        occurred_at = _safe_timestamp(event.timestamp)
        pricing = registry.get(event.provider, event.model, at=occurred_at)
        if pricing is None:
            unpriced.add(f"{event.provider}/{event.model}")
            value = 0.0
        else:
            value = pricing.estimate(
                event.input_tokens, event.output_tokens, cached_input_tokens=event.cached_input_tokens
            )
        equivalent += value
        priced.append(
            replace(
                event,
                metadata={
                    **event.metadata,
                    "billing_basis": SUBSCRIPTION,
                    "equivalent_api_cost_usd": round(value, 6),
                },
            )
        )

    stamps = sorted(event.timestamp for event in priced)
    return LocalUsageScan(
        tool=tool,
        display_name=display_name,
        available=True,
        events=tuple(priced),
        plan=plan,
        sessions=sessions,
        first_seen=stamps[0] if stamps else None,
        last_seen=stamps[-1] if stamps else None,
        equivalent_api_cost_usd=round(equivalent, 6),
        unpriced_models=tuple(sorted(unpriced)),
        note=note,
    )


def _codex_session_files(base: Path) -> Iterator[Path]:
    for directory in ("sessions", "archived_sessions"):
        target = base / directory
        if target.is_dir():
            yield from target.glob("rollout-*.jsonl")


def _iter_json_lines(path: Path) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from a log, skipping anything unreadable.

    Session logs are written by another process and may be mid-write, so a
    truncated final line is normal rather than exceptional.
    """
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except (ValueError, RecursionError):
                continue
            if isinstance(record, dict):
                yield record


def _find_plan_type(record: dict[str, Any], *, depth: int = 0) -> str | None:
    """Search a record for a plan identifier without assuming its exact path."""
    if depth > 6:
        return None
    for key, value in record.items():
        if key in {"plan_type", "planType", "plan_tier", "planTier"}:
            text = _text(value)
            if text:
                return text
        if isinstance(value, dict):
            found = _find_plan_type(value, depth=depth + 1)
            if found:
                return found
    return None


def _find_model(record: dict[str, Any]) -> str | None:
    for key in ("model", "model_id"):
        text = _text(record.get(key))
        if text:
            return text
    for value in record.values():
        if isinstance(value, dict):
            found = _find_model(value)
            if found:
                return found
    return None


def _timestamp(record: dict[str, Any]) -> str:
    for key in ("timestamp", "created_at", "time"):
        text = _text(record.get(key))
        if text:
            try:
                return parse_timestamp(text).isoformat()
            except ValueError:
                continue
    return datetime.now(timezone.utc).isoformat()


def _safe_timestamp(value: str) -> datetime | None:
    try:
        return parse_timestamp(value)
    except ValueError:
        return None


def _within(event: UsageEvent, since: datetime | None) -> bool:
    if since is None:
        return True
    occurred_at = _safe_timestamp(event.timestamp)
    return occurred_at is None or occurred_at >= since


def _count(source: Any, key: str) -> int:
    if not isinstance(source, dict):
        return 0
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _anonymous_trace(tool: str, path: Path) -> str:
    """Identify a session without recording its filename or project path."""
    digest = hashlib.sha256(f"{tool}:{path.name}".encode("utf-8")).hexdigest()
    return f"{tool}-{digest[:16]}"
