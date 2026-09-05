"""Watch capacity in the background and speak up only when it matters.

The point of a watcher is to reach someone *before* they lose an afternoon to a
limit. That value collapses the moment it becomes noise: a developer who gets
notified every time their editor starts silences the app within a day, and then
the one notification that would have saved them never arrives.

So the rules here are deliberately conservative.

* **Earned interruptions only.** Capacity warnings default on because they
  prevent a concrete loss. "A tool started running" defaults off, because
  knowing an editor opened tells the person something they already know.
* **Never twice for the same thing.** Each alert is keyed to the window it
  describes, so a five-hour window can warn once about eighty percent and once
  about exhaustion, and then stay quiet no matter how often the loop runs.
* **Failure is silent, not fatal.** A missing notifier, a locked screen, or an
  unreadable log ends one cycle, never the watcher.

What it reads is the same content-free metadata as the rest of Poliora, plus
the *names* of supported AI tools that are running. Nothing is uploaded.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from poliora import notify
from poliora.cost.capacity import FIVE_HOUR, forecast_runway, read_throttle_events
from poliora.cost.local_usage import read_claude_code_usage, read_codex_usage
from poliora.cost.processes import running_tools

DEFAULT_INTERVAL = timedelta(seconds=120)

# Alert kinds. The key each one is deduplicated against appears beside it.
APPROACHING = "approaching"       # once per window, at the warning threshold
EXHAUSTED = "exhausted"           # once per window, when capacity runs out
SPARE_CAPACITY = "spare-capacity"  # once per window, when another plan is idle
TOOL_STARTED = "tool-started"     # opt-in, once per tool per session

WARNING_THRESHOLD_PCT = 80.0


@dataclass
class WatchSettings:
    """What the watcher is allowed to interrupt someone about."""

    warn_approaching: bool = True
    warn_exhausted: bool = True
    suggest_spare_capacity: bool = True
    announce_tools: bool = False
    threshold_pct: float = WARNING_THRESHOLD_PCT

    def to_dict(self) -> dict[str, object]:
        """Serialize the settings."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WatchSettings":
        """Deserialize settings, ignoring anything unrecognized."""
        if not isinstance(data, dict):
            return cls()
        known = {field_name for field_name in cls().to_dict()}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class WatchState:
    """What has already been said, so it is not said again."""

    sent: dict[str, str] = field(default_factory=dict)

    def already_sent(self, kind: str, key: str) -> bool:
        """Whether this exact alert has been delivered."""
        return self.sent.get(kind) == key

    def record(self, kind: str, key: str) -> None:
        """Remember that this alert was delivered."""
        self.sent[kind] = key

    def to_dict(self) -> dict[str, object]:
        """Serialize the state."""
        return {"sent": dict(self.sent)}

    @classmethod
    def from_dict(cls, data: dict) -> "WatchState":
        """Deserialize state, tolerating a damaged file."""
        if not isinstance(data, dict):
            return cls()
        sent = data.get("sent")
        if not isinstance(sent, dict):
            return cls()
        return cls(sent={str(k): str(v) for k, v in sent.items()})


@dataclass(frozen=True)
class Alert:
    """One thing worth interrupting someone about."""

    kind: str
    key: str
    title: str
    body: str
    urgent: bool = False

    def to_dict(self) -> dict[str, object]:
        """Serialize the alert."""
        return asdict(self)


def load_state(path: str | Path) -> WatchState:
    """Read what has already been announced, tolerating a damaged file."""
    target = Path(path)
    if not target.exists():
        return WatchState()
    try:
        return WatchState.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return WatchState()


def save_state(path: str | Path, state: WatchState) -> Path:
    """Write the announcement history atomically."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def window_key(moment: datetime, window: str = FIVE_HOUR) -> str:
    """A stable identifier for the limit window a moment falls in.

    Alerts are deduplicated against this, so a warning fires once per window
    rather than once per polling cycle.
    """
    if window == FIVE_HOUR:
        block = moment.replace(minute=0, second=0, microsecond=0)
        return f"{window}:{block.strftime('%Y-%m-%dT%H')}"
    iso = moment.isocalendar()
    return f"{window}:{iso.year}-W{iso.week:02d}"


def evaluate(
    *,
    settings: WatchSettings | None = None,
    state: WatchState | None = None,
    now: datetime | None = None,
    home: Path | None = None,
) -> list[Alert]:
    """Decide what, if anything, is worth saying right now."""
    active_settings = settings or WatchSettings()
    active_state = state or WatchState()
    moment = now or datetime.now(timezone.utc)

    scan = read_claude_code_usage(home=home, since=moment - timedelta(hours=6))
    if not scan.available:
        return []

    forecast = forecast_runway(
        list(scan.events), read_throttle_events(home=home), window=FIVE_HOUR, now=moment
    )
    key = window_key(moment)
    alerts: list[Alert] = []

    used_pct = forecast.used_pct
    remaining = forecast.remaining_tokens

    if active_settings.warn_exhausted and forecast.ceiling.is_known and remaining is not None and remaining <= 0:
        if not active_state.already_sent(EXHAUSTED, key):
            when = f" Resets at {forecast.resets_at.astimezone():%H:%M}." if forecast.resets_at else ""
            alerts.append(
                Alert(
                    kind=EXHAUSTED,
                    key=key,
                    title="Claude limit reached",
                    body=f"This 5-hour window is spent.{when}",
                    urgent=True,
                )
            )
    elif (
        active_settings.warn_approaching
        and used_pct is not None
        and used_pct >= active_settings.threshold_pct
        and not active_state.already_sent(APPROACHING, key)
    ):
        left = forecast.time_remaining(now=moment)
        minutes = int(left.total_seconds() // 60) if left else None
        tail = f" About {minutes} min left at this pace." if minutes else ""
        alerts.append(
            Alert(
                kind=APPROACHING,
                key=key,
                title=f"Claude capacity {used_pct:.0f}% used",
                body=f"You are approaching the 5-hour limit.{tail}",
                urgent=True,
            )
        )

    if active_settings.suggest_spare_capacity and used_pct is not None and used_pct >= 60:
        codex = read_codex_usage(home=home, since=moment - timedelta(days=1))
        spare = codex.plan.quota_used_pct if codex.available and codex.plan else None
        if spare is not None and spare <= 40 and not active_state.already_sent(SPARE_CAPACITY, key):
            alerts.append(
                Alert(
                    kind=SPARE_CAPACITY,
                    key=key,
                    title="Codex has spare capacity",
                    body=f"Claude is {used_pct:.0f}% used and Codex is at {spare:.0f}%. "
                    "Mechanical work could go there.",
                )
            )

    if active_settings.announce_tools:
        for tool in running_tools():
            tool_key = f"{key}:{tool.id}"
            if not active_state.already_sent(TOOL_STARTED, tool_key):
                alerts.append(
                    Alert(
                        kind=TOOL_STARTED,
                        key=tool_key,
                        title=f"{tool.display_name} is running",
                        body="Poliora is tracking this session's capacity.",
                    )
                )
                break  # One announcement per cycle; a burst is not information.

    return alerts


def run_once(
    *,
    state_path: Path,
    settings: WatchSettings | None = None,
    now: datetime | None = None,
    home: Path | None = None,
    sender: Callable[..., object] | None = None,
) -> list[Alert]:
    """Evaluate once, deliver what is due, and remember it. Never raises."""
    deliver = sender or notify.send
    try:
        state = load_state(state_path)
        alerts = evaluate(settings=settings, state=state, now=now, home=home)
        delivered: list[Alert] = []
        for alert in alerts:
            result = deliver(alert.title, alert.body, urgent=alert.urgent)
            # Only remember alerts that actually reached the desktop, so a
            # muted session does not silently consume the one warning.
            if getattr(result, "delivered", bool(result)):
                state.record(alert.kind, alert.key)
                delivered.append(alert)
        if delivered:
            save_state(state_path, state)
        return delivered
    except Exception:  # noqa: BLE001 - a watcher must outlive any single cycle
        return []


def watch(
    *,
    state_path: Path,
    settings: WatchSettings | None = None,
    interval: timedelta = DEFAULT_INTERVAL,
    iterations: int | None = None,
    home: Path | None = None,
    sender: Callable[..., object] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Poll on an interval, returning how many alerts were delivered.

    ``iterations`` bounds the loop so the behaviour can be tested without a
    background process; left as None it runs until interrupted.
    """
    seconds = max(interval.total_seconds(), 15)
    delivered = 0
    completed = 0
    while iterations is None or completed < iterations:
        delivered += len(run_once(state_path=state_path, settings=settings, home=home, sender=sender))
        completed += 1
        if iterations is not None and completed >= iterations:
            break
        sleeper(seconds)
    return delivered
