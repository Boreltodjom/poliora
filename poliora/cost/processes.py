"""Detect which supported AI coding tools are running right now.

This exists so Poliora can speak up at the moment it is useful -- while someone
is working, not hours later when they open a dashboard.

**What is read, and what is deliberately not.** Only process *names*, matched
against a fixed list of supported tools. Never command-line arguments, which on
a coding tool routinely contain file paths, prompts, and occasionally
credentials. Never the process list of other users. Never anything about
programs that are not on the supported list -- an unrecognized process is not
recorded, not counted, and not reported.

That restraint is the point. A background watcher that enumerated everything a
person runs would be indistinguishable from spyware, whatever its intent.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass

# Process names for the tools Poliora understands. Matching is on the
# executable name only, lowercased, with any extension removed.
SUPPORTED_TOOLS: dict[str, tuple[str, ...]] = {
    "claude-code": ("claude",),
    "codex": ("codex",),
    "cursor": ("cursor",),
    "antigravity": ("antigravity",),
    "windsurf": ("windsurf",),
    "copilot": ("copilot",),
    "gemini-cli": ("gemini",),
    "aider": ("aider",),
}

DISPLAY_NAMES: dict[str, str] = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "antigravity": "Antigravity",
    "windsurf": "Windsurf",
    "copilot": "GitHub Copilot",
    "gemini-cli": "Gemini CLI",
    "aider": "Aider",
}

_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class RunningTool:
    """One supported tool observed running."""

    id: str
    display_name: str
    process_count: int

    def to_dict(self) -> dict[str, object]:
        """Serialize a running tool."""
        return asdict(self)


def running_tools() -> list[RunningTool]:
    """Return the supported AI tools currently running for this user.

    Never raises: a machine that refuses to list processes yields an empty
    result, because a watcher that dies on a permissions error is worse than
    one that quietly sees nothing.
    """
    try:
        names = _process_names()
    except (OSError, subprocess.SubprocessError):
        return []

    counts: dict[str, int] = {}
    for raw in names:
        tool = _match_tool(raw)
        if tool is not None:
            counts[tool] = counts.get(tool, 0) + 1

    return sorted(
        (
            RunningTool(id=tool, display_name=DISPLAY_NAMES.get(tool, tool), process_count=count)
            for tool, count in counts.items()
        ),
        key=lambda item: item.display_name,
    )


def is_running(tool_id: str) -> bool:
    """Whether one supported tool is currently running."""
    return any(tool.id == tool_id for tool in running_tools())


def _match_tool(process_name: str) -> str | None:
    """Map an executable name to a supported tool, or None.

    The comparison is on the whole stem rather than a substring so that an
    unrelated program does not get reported as somebody's AI tool.
    """
    stem = os.path.splitext(process_name.strip().lower())[0]
    if not stem:
        return None
    for tool, candidates in SUPPORTED_TOOLS.items():
        if stem in candidates:
            return tool
    return None


def _process_names() -> list[str]:
    """List process names, preferring psutil when it happens to be installed.

    psutil is not a dependency: it is a compiled package, and requiring it
    would make `pip install poliora` fail on machines without a build
    toolchain for the sake of one feature. The platform's own tools are enough.
    """
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return _process_names_from_shell()

    names: list[str] = []
    for process in psutil.process_iter(["name"]):
        try:
            name = process.info.get("name")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name:
            names.append(str(name))
    return names


def _process_names_from_shell() -> list[str]:
    """Fall back to the operating system's own process listing."""
    if platform.system() == "Windows":
        tasklist = shutil.which("tasklist")
        if not tasklist:
            return []
        completed = subprocess.run(  # noqa: S603
            [tasklist, "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
        return [
            line.split('","')[0].lstrip('"')
            for line in completed.stdout.splitlines()
            if line.startswith('"')
        ]

    ps = shutil.which("ps")
    if not ps:
        return []
    # -o comm= prints the executable name alone, without the arguments that
    # would otherwise expose prompts and file paths.
    completed = subprocess.run(  # noqa: S603
        [ps, "-x", "-o", "comm="],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )
    return [os.path.basename(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
