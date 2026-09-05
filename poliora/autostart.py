"""Start the Poliora watcher when the person logs in, on any platform.

Each operating system has one mechanism that works without administrator
rights, and this uses that one rather than a service or a daemon:

* Windows: a shortcut-free ``.cmd`` in the per-user Startup folder.
* macOS: a LaunchAgent plist in ``~/Library/LaunchAgents``.
* Linux: a ``.desktop`` entry in ``~/.config/autostart``.

All three are ordinary files in the user's own home directory. That is
deliberate: something which starts itself at login should be inspectable and
removable with a text editor and a delete, not buried in a registry hive or a
root-owned service. :func:`remove` undoes it completely, and :func:`status`
says exactly which file is responsible.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

APP_LABEL = "com.poliora.watcher"
FILE_STEM = "poliora-watch"


@dataclass(frozen=True)
class AutostartStatus:
    """Whether login startup is configured, and by which file."""

    installed: bool
    path: Path | None
    platform_name: str
    supported: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize the status."""
        return {
            "installed": self.installed,
            "path": str(self.path) if self.path else None,
            "platform": self.platform_name,
            "supported": self.supported,
            "detail": self.detail,
        }


def entry_path(*, home: Path | None = None) -> Path | None:
    """Where this platform's login entry lives, or None when unsupported."""
    # An explicit `home` must win over the environment. Otherwise a caller that
    # passes a temporary directory -- a test, or a sandboxed run -- would still
    # resolve to the real Startup folder and could delete somebody's actual
    # login entry.
    explicit = home is not None
    base = home or Path.home()
    system = platform.system()
    if system == "Windows":
        appdata = None if explicit else os.environ.get("APPDATA")
        root = Path(appdata) if appdata else base / "AppData" / "Roaming"
        return root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{FILE_STEM}.cmd"
    if system == "Darwin":
        return base / "Library" / "LaunchAgents" / f"{APP_LABEL}.plist"
    if system == "Linux":
        config = None if explicit else os.environ.get("XDG_CONFIG_HOME")
        root = Path(config) if config else base / ".config"
        return root / "autostart" / f"{FILE_STEM}.desktop"
    return None


def status(*, home: Path | None = None) -> AutostartStatus:
    """Report whether the watcher is configured to start at login."""
    system = platform.system()
    path = entry_path(home=home)
    if path is None:
        return AutostartStatus(
            installed=False,
            path=None,
            platform_name=system,
            supported=False,
            detail=f"Poliora does not know how to start at login on {system or 'this system'}.",
        )
    return AutostartStatus(installed=path.exists(), path=path, platform_name=system)


def install(*, home: Path | None = None, command: str | None = None) -> AutostartStatus:
    """Write the login entry for this platform."""
    system = platform.system()
    path = entry_path(home=home)
    if path is None:
        return AutostartStatus(
            installed=False,
            path=None,
            platform_name=system,
            supported=False,
            detail=f"Starting at login is not supported on {system or 'this system'}.",
        )

    launcher = command or _default_command()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_entry_body(system, launcher), encoding="utf-8")
    if system == "Linux":
        # A .desktop entry does not need the executable bit, but some session
        # managers historically expected it and it costs nothing to set.
        try:
            path.chmod(0o644)
        except OSError:
            pass
    return AutostartStatus(installed=True, path=path, platform_name=system)


def remove(*, home: Path | None = None) -> AutostartStatus:
    """Delete the login entry, reporting whether one was there."""
    system = platform.system()
    path = entry_path(home=home)
    if path is None:
        return AutostartStatus(False, None, system, supported=False)
    existed = path.exists()
    if existed:
        try:
            path.unlink()
        except OSError as error:
            return AutostartStatus(True, path, system, detail=str(error))
    return AutostartStatus(
        installed=False,
        path=path,
        platform_name=system,
        detail="" if existed else "Poliora was not set to start at login.",
    )


def _default_command() -> str:
    """The command a login entry should run.

    Uses the interpreter running right now rather than a bare "poliora", so an
    install inside a virtual environment keeps working after login when that
    environment is not on PATH.
    """
    return f'"{sys.executable}" -m poliora.main watch'


def _entry_body(system: str, command: str) -> str:
    """Render the platform's login entry."""
    if system == "Windows":
        # `start "" /min` detaches so the console window does not linger.
        return (
            "@echo off\r\n"
            "rem Poliora capacity watcher. Delete this file to stop it starting at login.\r\n"
            f'start "" /min {command}\r\n'
        )
    if system == "Darwin":
        program = command.strip()
        parts = _split_command(program)
        arguments = "".join(f"        <string>{part}</string>\n" for part in parts)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "    <key>Label</key>\n"
            f"    <string>{APP_LABEL}</string>\n"
            "    <key>ProgramArguments</key>\n"
            "    <array>\n"
            f"{arguments}"
            "    </array>\n"
            "    <key>RunAtLoad</key>\n"
            "    <true/>\n"
            "    <key>KeepAlive</key>\n"
            "    <false/>\n"
            "</dict>\n"
            "</plist>\n"
        )
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Poliora capacity watcher\n"
        f"Exec={command}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Comment=Warns before an AI plan limit is reached. Delete this file to disable.\n"
    )


def _split_command(command: str) -> list[str]:
    """Split a command into arguments, honouring simple quoting."""
    parts: list[str] = []
    current = ""
    quoted = False
    for char in command:
        if char == '"':
            quoted = not quoted
            continue
        if char == " " and not quoted:
            if current:
                parts.append(current)
                current = ""
            continue
        current += char
    if current:
        parts.append(current)
    return parts
