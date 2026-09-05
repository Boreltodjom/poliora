"""Standalone native desktop application launcher for Poliora."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from threading import Thread

from poliora.cost import init_workspace, load_workspace
from poliora.web import create_dashboard_server


def _available_port(preferred_port: int) -> int:
    """Return the preferred loopback port, or the next available one."""
    for port in range(preferred_port, preferred_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No local dashboard port was available in the configured range.")


def _running_dashboard_port(preferred_port: int) -> int | None:
    """Return an occupied local dashboard port, if Poliora is already open."""
    for port in range(preferred_port, preferred_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return port
    return None


def _desktop_workspace_root() -> Path:
    """Choose the normal per-user application-data location for the desktop app."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Poliora" / "workspace"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Poliora" / "workspace"
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "poliora" / "workspace"


def _run_antigravity_hook() -> None:
    """Receive an Antigravity hook without starting the desktop window."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("antigravity-hook")
    parser.add_argument("--event", required=True)
    parser.add_argument("--root", required=True)
    try:
        arguments = parser.parse_args()
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Hook input must be a JSON object.")
        from poliora.cost import record_antigravity_hook_event

        record_antigravity_hook_event(
            payload,
            event_name=arguments.event,
            root=Path(arguments.root),
        )
    except Exception as error:  # Hooks must never interrupt the user's task.
        print(f"Poliora hook skipped: {error}", file=sys.stderr)
    print("{}")


def _run_native_window(url: str) -> None:
    """Display the local dashboard inside Poliora's own desktop window."""
    import webview

    webview.create_window(
        "Poliora",
        url=url,
        width=1480,
        height=960,
        min_size=(960, 680),
        background_color="#f6f6f2",
    )
    webview.start()


def main() -> None:
    """Launch Poliora as a local native desktop application."""
    if len(sys.argv) > 1 and sys.argv[1] == "antigravity-hook":
        _run_antigravity_hook()
        return

    parser = argparse.ArgumentParser(description="Launch the Poliora local desktop application.")
    parser.add_argument("--version", action="store_true", help="Show the standalone app version and exit.")
    parser.add_argument("--port", type=int, default=8787, help="Preferred local dashboard port.")
    parser.add_argument("--no-open", action="store_true", help="Run only the local engine for support and smoke tests.")
    arguments = parser.parse_args()
    if arguments.version:
        from poliora import __version__

        print(f"poliora {__version__}")
        return

    existing_port = _running_dashboard_port(arguments.port)
    if existing_port is not None:
        return

    workspace_root = _desktop_workspace_root()
    workspace = load_workspace(workspace_root)
    if not workspace.config_path.exists():
        init_workspace(workspace_root, project="Desktop", monthly_budget_usd=1000.0)

    port = _available_port(arguments.port)
    server = create_dashboard_server(workspace_root, host="127.0.0.1", port=port)
    if arguments.no_open:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return

    server_thread = Thread(target=server.serve_forever, daemon=True, name="poliora-local-engine")
    server_thread.start()
    url = f"http://127.0.0.1:{port}/?desktop-first-run=1"
    try:
        _run_native_window(url)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


if __name__ == "__main__":
    main()
