"""Attribute local AI usage to the projects that caused it.

Knowing a window is 80% spent is only half an answer. The other half is *what
spent it* -- and on a machine where one agent session can consume more than a
day of ordinary work, the answer is usually one project rather than a diffuse
drift across all of them.

Claude Code stores sessions under a directory per project, so attribution needs
no guessing: the directory name is the grouping key. Codex records a working
directory per session for the same purpose.

**Scope of what is read.** Only the project directory's *name* and the token
counts already gathered elsewhere. Never file contents, never the code inside a
project, never prompts. The name never leaves the machine; it exists so the
person can recognize their own work in their own report.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from poliora.cost.local_usage import _iter_json_lines, _safe_timestamp, _touched_since
from poliora.cost.usage import UsageEvent

# Claude Code encodes a filesystem path into one directory name by replacing
# separators. The two encodings differ, and conflating them corrupts names:
#
#   Windows  C:\EcoTune      -> "C--EcoTune"     (":\\" becomes "--")
#   POSIX    /home/dana/api  -> "-home-dana-api" ("/" becomes "-")
#
# So a single "-" is a path separator on POSIX but an ordinary character in a
# Windows project name. Splitting Windows names on single hyphens turns
# "auto-doc" into "doc", which is worse than showing the raw directory.
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]--")


@dataclass(frozen=True)
class WorkflowUsage:
    """One project's share of the usage observed in a period."""

    project: str
    requests: int
    tokens: int
    share_pct: float
    first_seen: str | None = None
    last_seen: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize one project's attribution."""
        return asdict(self)


@dataclass(frozen=True)
class WorkflowReport:
    """How observed usage divides across projects."""

    projects: tuple[WorkflowUsage, ...]
    total_tokens: int
    period_days: int
    unattributed_tokens: int = 0

    @property
    def dominant(self) -> WorkflowUsage | None:
        """The project consuming the largest share, when one stands out."""
        return self.projects[0] if self.projects else None

    def describe(self) -> str:
        """State the finding, or say plainly that there is none."""
        if not self.projects:
            return "No project-level usage was found in this period."
        top = self.projects[0]
        if len(self.projects) == 1:
            return f"All observed usage in the last {self.period_days} days came from {top.project}."
        return (
            f"{top.project} used {top.share_pct:.0f}% of your tracked tokens over the last "
            f"{self.period_days} days, across {len(self.projects)} projects."
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the attribution report."""
        return {
            "projects": [project.to_dict() for project in self.projects],
            "total_tokens": self.total_tokens,
            "period_days": self.period_days,
            "unattributed_tokens": self.unattributed_tokens,
            "description": self.describe(),
        }


def read_workflow_usage(
    *,
    home: Path | None = None,
    since: datetime | None = None,
    period_days: int = 30,
    limit: int = 10,
) -> WorkflowReport:
    """Group Claude Code usage by the project directory that produced it."""
    root = (home or Path.home()) / ".claude" / "projects"
    cutoff = since if since is not None else datetime.now(timezone.utc) - timedelta(days=period_days)
    if not root.is_dir():
        return WorkflowReport(projects=(), total_tokens=0, period_days=period_days)

    totals: dict[str, list[int]] = {}
    seen: dict[str, list[str]] = {}

    for project_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        label = readable_project_name(project_dir.name)
        for session in sorted(project_dir.glob("*.jsonl")):
            if not _touched_since(session, cutoff):
                continue
            for record in _iter_json_lines(session, must_contain=('"usage"',)):
                tokens, occurred_at = _tokens_and_time(record)
                if tokens <= 0 or occurred_at is None or occurred_at < cutoff:
                    continue
                bucket = totals.setdefault(label, [0, 0])
                bucket[0] += 1
                bucket[1] += tokens
                stamps = seen.setdefault(label, [])
                stamps.append(occurred_at.isoformat())

    total_tokens = sum(bucket[1] for bucket in totals.values())
    projects = [
        WorkflowUsage(
            project=label,
            requests=bucket[0],
            tokens=bucket[1],
            share_pct=round(bucket[1] / total_tokens * 100, 1) if total_tokens else 0.0,
            first_seen=min(seen[label]) if seen.get(label) else None,
            last_seen=max(seen[label]) if seen.get(label) else None,
        )
        for label, bucket in totals.items()
    ]
    projects.sort(key=lambda item: item.tokens, reverse=True)
    return WorkflowReport(
        projects=tuple(projects[:limit]),
        total_tokens=total_tokens,
        period_days=period_days,
        unattributed_tokens=sum(item.tokens for item in projects[limit:]),
    )


def readable_project_name(directory_name: str) -> str:
    """Turn an encoded project directory name back into something recognizable.

    Falls back to the raw name rather than guessing when the encoding is not
    one we recognize: a wrong label is worse than an ugly one.
    """
    name = directory_name.strip()
    if not name:
        return directory_name

    if _WINDOWS_DRIVE.match(name):
        # Windows: only "--" separates path segments; hyphens inside a folder
        # name are part of the name and must survive.
        segments = [part for part in name[3:].split("--") if part]
    elif name.startswith("-"):
        # POSIX: "-" is the separator, so every hyphen splits.
        segments = [part for part in name.split("-") if part]
    else:
        segments = [name]

    return segments[-1] if segments else directory_name


def _tokens_and_time(record: dict) -> tuple[int, datetime | None]:
    """Pull the token total and timestamp from one assistant record."""
    message = record.get("message")
    if not isinstance(message, dict):
        return 0, None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return 0, None

    total = 0
    for field in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
        value = usage.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            total += int(value)
    return total, _safe_timestamp(str(record.get("timestamp") or ""))


def events_by_project(events: list[UsageEvent]) -> dict[str, int]:
    """Group already-loaded events by the project recorded on them."""
    grouped: dict[str, int] = {}
    for event in events:
        grouped[event.project] = grouped.get(event.project, 0) + event.total_tokens
    return grouped
