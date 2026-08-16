"""Consent-triggered discovery of local AI-tool launchers.

Discovery deliberately checks only command availability and Poliora's own
workspace plugin. It never reads conversations, projects, shell history,
credentials, or provider accounts.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DetectedTool:
    """A plain-language result of a narrow local tool-availability check."""

    id: str
    name: str
    detected: bool
    detail: str
    next_step: str
    history_note: str

    def to_dict(self) -> dict[str, object]:
        """Serialize without exposing local installation paths."""
        return asdict(self)


def detect_local_tools(
    root: str | Path = ".",
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> list[DetectedTool]:
    """Check supported launcher names after the person explicitly requests it.

    The result intentionally says "found" rather than asserting that an
    account is signed in or that the tool's private data is accessible.
    """
    workspace = Path(root)
    antigravity_plugin = workspace / ".agents" / "plugins" / "poliora"
    return [
        _launcher_result(
            "codex-runtime",
            "Codex CLI",
            ("codex", "codex.cmd", "codex.exe"),
            "Use Poliora's Codex command to record future, content-free token metadata.",
            "Past subscription history is not read. API-billed history needs an authorized export.",
            which,
        ),
        _launcher_result(
            "claude-code",
            "Claude Code",
            ("claude", "claude.cmd", "claude.exe"),
            "Review an authorized organization CSV, or wait for an official direct adapter.",
            "Poliora does not scrape Claude Code sessions or account history.",
            which,
        ),
        _launcher_result(
            "cursor-team",
            "Cursor",
            ("cursor", "cursor.cmd", "cursor.exe"),
            "Import an authorized Cursor Team export when available.",
            "Poliora does not inspect Cursor chats or infer spending from a subscription.",
            which,
        ),
        DetectedTool(
            id="gemini-antigravity",
            name="Antigravity workspace helper",
            detected=antigravity_plugin.is_dir(),
            detail=(
                "Poliora's workspace helper is installed."
                if antigravity_plugin.is_dir()
                else "The helper is not installed in this workspace yet."
            ),
            next_step=(
                "Open Antigravity and reload customizations to begin activity-only tracking."
                if antigravity_plugin.is_dir()
                else "Approve setup, then run poliora antigravity-install when you are ready."
            ),
            history_note="Antigravity hooks expose activity, not a complete token or billing history.",
        ),
    ]


def _launcher_result(
    tool_id: str,
    name: str,
    launcher_names: tuple[str, ...],
    next_step: str,
    history_note: str,
    which: Callable[[str], str | None],
) -> DetectedTool:
    found = next((name for name in launcher_names if which(name)), None)
    return DetectedTool(
        id=tool_id,
        name=name,
        detected=found is not None,
        detail=(
            "A command-line launcher was found. Poliora has not opened it or read any data."
            if found
            else "No command-line launcher was found in this computer's PATH."
        ),
        next_step=next_step if found else "Install or add the launcher to PATH, then scan again.",
        history_note=history_note,
    )
