"""Opt-in connector registry for the local Poliora Companion."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class ConnectorDefinition:
    """A supported or planned source of AI usage metadata."""

    id: str
    name: str
    category: str
    availability: str
    description: str
    permissions: tuple[str, ...]
    metrics: tuple[str, ...]
    setup_hint: str

    def to_dict(self) -> dict[str, object]:
        """Serialize a connector definition for the dashboard."""
        data = asdict(self)
        data["permissions"] = list(self.permissions)
        data["metrics"] = list(self.metrics)
        return data


@dataclass(frozen=True)
class ConnectorConnection:
    """A user's local consent and setup state for one connector."""

    connector_id: str
    state: str
    consented_at: str

    def to_dict(self) -> dict[str, str]:
        """Serialize a connection state."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ConnectorConnection":
        """Deserialize a connection state."""
        return cls(
            connector_id=str(data["connector_id"]),
            state=str(data["state"]),
            consented_at=str(data["consented_at"]),
        )


def connector_catalog() -> list[ConnectorDefinition]:
    """Return the Companion connectors and their truthful availability."""
    return [
        ConnectorDefinition(
            "cursor-team",
            "Cursor Team",
            "Team analytics",
            "credential-required",
            "Supports CSV pilot imports today; direct team analytics ingestion is planned.",
            ("Cursor Team Admin API key",),
            ("model", "tokens", "cost", "user", "agent activity"),
            "Use an authorized CSV export today. The direct adapter will require a Cursor team administrator API key.",
        ),
        ConnectorDefinition(
            "claude-code",
            "Claude Code",
            "Team analytics",
            "credential-required",
            "Supports authorized CSV imports today; direct organization analytics ingestion is planned.",
            ("Anthropic organization Admin API key",),
            ("model", "tokens", "estimated cost", "sessions", "tool actions"),
            "Use an authorized organization CSV today. The direct adapter will require an Anthropic Admin API key.",
        ),
        ConnectorDefinition(
            "codex-runtime",
            "Codex CLI",
            "Local agent capture",
            "ready",
            "Runs tasks through the documented Codex JSON stream and records token metadata locally.",
            ("Permission to launch your installed Codex CLI for the task you provide",),
            ("model", "input tokens", "cached tokens", "output tokens", "reasoning tokens"),
            "Run: poliora codex --model MODEL \"your task\". Poliora ignores prompt, response, command, "
            "and file-change content when saving usage. Subscription turns are not assigned fake API cost.",
        ),
        ConnectorDefinition(
            "gemini-antigravity",
            "Gemini and Antigravity",
            "Runtime capture",
            "ready",
            "Tracks exact Gemini API response usage and Antigravity agent activity through supported surfaces.",
            ("Workspace plugin consent", "Application-level Gemini wrapper"),
            ("agent invocations", "model", "tokens", "cache", "reasoning", "tool prompts"),
            "Run: poliora antigravity-install. Antigravity hooks expose activity but not model or token totals, "
            "so product-subscription activity stays separate from Gemini API spend.",
        ),
        ConnectorDefinition(
            "openai-compatible",
            "Compatible AI Gateways",
            "Runtime capture",
            "ready",
            "Tracks OpenAI-compatible clients such as DeepSeek gateways under the correct provider label.",
            ("Application-level Poliora wrapper",),
            ("model", "tokens", "cache", "reasoning", "latency"),
            "Use the existing OpenAI-compatible wrapper and choose the provider name used by your gateway.",
        ),
        ConnectorDefinition(
            "codex-product",
            "Codex Product Usage",
            "Product analytics",
            "official-surface-needed",
            "Will report product-session usage when a trustworthy official Codex usage export "
            "or plugin surface is available.",
            ("Future official Codex permission",),
            ("verified usage or quota signals"),
            "Poliora will not scrape private Codex sessions or make up dollar spend from a ChatGPT subscription.",
        ),
    ]


class ConnectorStore:
    """Atomic local storage for Companion consent states."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> list[ConnectorConnection]:
        """Read locally recorded connection states."""
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Connector store JSON must be a list.")
        return [ConnectorConnection.from_dict(dict(item)) for item in raw]

    def consent(self, connector_id: str) -> ConnectorConnection:
        """Record consent while leaving credentials outside the workspace."""
        connection = ConnectorConnection(
            connector_id=connector_id,
            state="awaiting-setup",
            consented_at=datetime.now(timezone.utc).isoformat(),
        )
        connections = [item for item in self.read_all() if item.connector_id != connector_id]
        connections.append(connection)
        self._write(connections)
        return connection

    def disconnect(self, connector_id: str) -> bool:
        """Remove local consent and connection state."""
        connections = self.read_all()
        remaining = [item for item in connections if item.connector_id != connector_id]
        if len(remaining) == len(connections):
            return False
        self._write(remaining)
        return True

    def _write(self, connections: list[ConnectorConnection]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps([item.to_dict() for item in connections], indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
