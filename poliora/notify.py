"""Send a desktop notification using whatever the operating system provides.

No dependency is added for this. Every supported platform ships a mechanism
already -- PowerShell toasts on Windows, ``osascript`` on macOS, ``notify-send``
on Linux -- and shelling out to them keeps ``pip install poliora`` small and
avoids a compiled package that can fail to build on someone's machine.

Notifications are best-effort by design. A missing notifier, a locked session,
or a user who has muted notifications must never take down the watcher that
called this; :func:`send` reports failure by returning False and never raises.
"""

from __future__ import annotations

import base64
import platform
import shutil
import subprocess
from dataclasses import dataclass

# Notifications are interrupting by nature, so the text has to earn the
# interruption. These caps keep a message glanceable rather than a wall.
MAX_TITLE = 64
MAX_BODY = 180

_TIMEOUT_SECONDS = 20

# Windows will not show a toast for an application id it does not know, and an
# ordinary pip install cannot register one. PowerShell's own id is registered on
# every Windows install, so the toast is raised under that.
_POWERSHELL_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\v1.0\\powershell.exe"


@dataclass(frozen=True)
class NotificationResult:
    """Whether a notification reached the desktop, and why not when it did not."""

    delivered: bool
    backend: str
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize the result."""
        return {"delivered": self.delivered, "backend": self.backend, "detail": self.detail}


def send(title: str, body: str, *, urgent: bool = False) -> NotificationResult:
    """Show a desktop notification, returning whether it was delivered."""
    clean_title = _clip(title, MAX_TITLE) or "Poliora"
    clean_body = _clip(body, MAX_BODY)
    system = platform.system()

    try:
        if system == "Windows":
            return _send_windows(clean_title, clean_body)
        if system == "Darwin":
            return _send_macos(clean_title, clean_body)
        return _send_linux(clean_title, clean_body, urgent=urgent)
    except (OSError, subprocess.SubprocessError) as error:
        return NotificationResult(False, system.lower() or "unknown", str(error)[:200])


def is_available() -> bool:
    """Whether this machine can show a notification at all."""
    system = platform.system()
    if system == "Windows":
        return bool(shutil.which("powershell") or shutil.which("powershell.exe"))
    if system == "Darwin":
        return bool(shutil.which("osascript"))
    return bool(shutil.which("notify-send"))


def _send_windows(title: str, body: str) -> NotificationResult:
    """Raise a toast through the Windows notification platform.

    Three details make this work reliably, each learned by watching it fail:

    * The toast is shown under PowerShell's own registered application id.
      Windows silently refuses -- or worse, blocks -- when asked to notify on
      behalf of an id that was never registered, which an ordinary pip install
      cannot do.
    * ``XmlDocument`` is loaded explicitly. Without it the WinRT projection is
      not always resolved and ``New-Object`` fails.
    * The script is passed base64-encoded rather than as an inline command, so
      no amount of quoting in a model or project name can break the script.
    """
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if not powershell:
        return NotificationResult(False, "windows", "PowerShell was not found on PATH.")

    script = (
        "$ErrorActionPreference='Stop';"
        f"$AppId='{_POWERSHELL_APP_ID}';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
        "ContentType=WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,"
        "ContentType=WindowsRuntime] > $null;"
        "$xml=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$xml.LoadXml('{_toast_xml(title, body)}');"
        "$toast=New-Object Windows.UI.Notifications.ToastNotification $xml;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")

    completed = subprocess.run(  # noqa: S603
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode == 0:
        return NotificationResult(True, "windows")
    return NotificationResult(False, "windows", (completed.stderr or "").strip()[:200])


def _toast_xml(title: str, body: str) -> str:
    """Build the toast document, escaped for both XML and PowerShell quoting."""
    document = (
        "<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{_escape_xml(title)}</text><text>{_escape_xml(body)}</text>"
        "</binding></visual></toast>"
    )
    # The document is embedded in a single-quoted PowerShell string, where a
    # literal quote is escaped by doubling it.
    return document.replace("'", "''")


def _send_macos(title: str, body: str) -> NotificationResult:
    """Show a notification through AppleScript."""
    osascript = shutil.which("osascript")
    if not osascript:
        return NotificationResult(False, "macos", "osascript was not found on PATH.")
    script = f'display notification "{_escape_applescript(body)}" with title "{_escape_applescript(title)}"'
    completed = subprocess.run(  # noqa: S603
        [osascript, "-e", script],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode == 0:
        return NotificationResult(True, "macos")
    return NotificationResult(False, "macos", (completed.stderr or "").strip()[:200])


def _send_linux(title: str, body: str, *, urgent: bool) -> NotificationResult:
    """Show a notification through the freedesktop notification daemon."""
    notify_send = shutil.which("notify-send")
    if not notify_send:
        return NotificationResult(False, "linux", "notify-send was not found on PATH.")
    command = [notify_send, "--app-name=Poliora"]
    if urgent:
        command.append("--urgency=critical")
    command.extend(["--", title, body])
    completed = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
    )
    if completed.returncode == 0:
        return NotificationResult(True, "linux")
    return NotificationResult(False, "linux", (completed.stderr or "").strip()[:200])


def _clip(text: str, limit: int) -> str:
    """Trim to a glanceable length on a word boundary where possible."""
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[: limit - 1]
    spaced = cut.rsplit(" ", 1)[0] if " " in cut[limit // 2 :] else cut
    return spaced.rstrip(" ,.;:") + "…"


def _escape_xml(text: str) -> str:
    """Escape text for the toast XML document."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _escape_applescript(text: str) -> str:
    """Escape text for an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')
