"""Usage event storage for AI token spend tracking."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


@dataclass(frozen=True)
class UsageEvent:
    """One AI request or workload usage event."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    tool_cost_usd: float = 0.0
    operation: str = "chat"
    project: str = "default"
    user: str | None = None
    latency_ms: float | None = None
    status: str = "success"
    trace_id: str | None = None
    provider_request_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        """Total tokens for the event."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        """Serialize event."""
        data = asdict(self)
        data["total_tokens"] = self.total_tokens
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UsageEvent":
        """Deserialize event."""
        return cls(
            provider=str(data["provider"]),
            model=str(data["model"]),
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            cached_input_tokens=int(data.get("cached_input_tokens", 0)),
            reasoning_tokens=int(data.get("reasoning_tokens", 0)),
            tool_cost_usd=float(data.get("tool_cost_usd", 0.0)),
            operation=str(data.get("operation", "chat")),
            project=str(data.get("project", "default")),
            user=data.get("user"),
            latency_ms=float(data["latency_ms"]) if data.get("latency_ms") is not None else None,
            status=str(data.get("status", "success")),
            trace_id=str(data["trace_id"]) if data.get("trace_id") is not None else None,
            provider_request_id=str(data["provider_request_id"])
            if data.get("provider_request_id") is not None
            else None,
            timestamp=str(data.get("timestamp") or datetime.now(timezone.utc).isoformat()),
            metadata=dict(data.get("metadata", {})),
        )


class JsonlUsageStore:
    """Append-only JSONL store for local usage events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: UsageEvent) -> None:
        """Append one event to the store."""
        if min(event.input_tokens, event.output_tokens, event.cached_input_tokens, event.reasoning_tokens) < 0:
            raise ValueError("Token counts must be non-negative.")
        if event.cached_input_tokens > event.input_tokens:
            raise ValueError("Cached input tokens cannot exceed input tokens.")
        if event.cost_usd < 0 or event.tool_cost_usd < 0:
            raise ValueError("Cost must be non-negative.")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _UsageFileLock(self.path):
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()

    def read_all(self) -> list[UsageEvent]:
        """Read all events in insertion order."""
        if not self.path.exists():
            return []

        with _UsageFileLock(self.path):
            events: list[UsageEvent] = []
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped:
                        events.append(UsageEvent.from_dict(json.loads(stripped)))
            return events

    def read_since(self, since: datetime | None = None) -> list[UsageEvent]:
        """Read events optionally filtered by timestamp."""
        events = self.read_all()
        if since is None:
            return events
        return [event for event in events if parse_timestamp(event.timestamp) >= since]


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def write_events(path: str | Path, events: Iterable[UsageEvent]) -> Path:
    """Write events to a JSONL file."""
    store = JsonlUsageStore(path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    serialized = "".join(json.dumps(event.to_dict(), ensure_ascii=False) + "\n" for event in events)
    with _UsageFileLock(store.path):
        temporary = store.path.with_name(f".{store.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(serialized, encoding="utf-8")
            os.replace(temporary, store.path)
        finally:
            if temporary.exists():
                temporary.unlink()
    return store.path


class _UsageFileLock:
    """A small cross-process lock based on exclusive lock-file creation."""

    def __init__(self, target: Path, *, timeout_seconds: float = 5.0) -> None:
        self.path = target.with_name(f".{target.name}.lock")
        self.timeout_seconds = timeout_seconds
        self._acquired = False

    def __enter__(self) -> "_UsageFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(f"pid={os.getpid()}\n")
                self._acquired = True
                return self
            except (FileExistsError, PermissionError):
                if self._is_stale():
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for usage log lock: {self.path}")
                time.sleep(0.01)

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._acquired = False

    def _is_stale(self) -> bool:
        try:
            age_seconds = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age_seconds > 60
