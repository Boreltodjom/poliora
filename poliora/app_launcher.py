"""Standalone Desktop App launcher for Poliora."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import webbrowser
from pathlib import Path
from threading import Timer

from poliora.cost import init_workspace, load_workspace
from poliora.web import run_dashboard


def _available_port(preferred_port: int) -> int:
    """Return the preferred loopback port, or the next available one."""
    for port in range(preferred_port, preferred_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No local dashboard port was available in the configured range.")


def _running_dashboard_port(preferred_port: int) -> int | None:
    """Return an existing Poliora dashboard port so a second launch reopens it."""
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


def main() -> None:
    """Launch Poliora standalone GUI application."""
    parser = argparse.ArgumentParser(description="Launch the Poliora local dashboard.")
    parser.add_argument("--version", action="store_true", help="Show the standalone app version and exit.")
    parser.add_argument("--port", type=int, default=8787, help="Preferred local dashboard port.")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser window.")
    arguments = parser.parse_args()
    if arguments.version:
        from poliora import __version__

        print(f"poliora {__version__}")
        return
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 60)
    print(" [Poliora] Starting Desktop AI Cost Optimizer...")
    print("=" * 60)

    existing_port = _running_dashboard_port(arguments.port)
    if existing_port is not None:
        if not arguments.no_open:
            webbrowser.open_new_tab(f"http://127.0.0.1:{existing_port}")
        return

    workspace_root = _desktop_workspace_root()
    workspace = load_workspace(workspace_root)
    if not workspace.config_path.exists():
        init_workspace(workspace_root, project="Desktop", monthly_budget_usd=1000.0)

    port = _available_port(arguments.port)
    url = f"http://127.0.0.1:{port}"
    print(f"\n[+] Poliora engine running at {url}")
    print("[+] Opening Desktop Application interface...")

    # Open browser window automatically after 0.5s
    if not arguments.no_open:
        Timer(0.5, webbrowser.open_new_tab, args=(url,)).start()

    # Serve local dashboard server
    try:
        run_dashboard(workspace_root, host="127.0.0.1", port=port)
    except KeyboardInterrupt:
        print("\nPoliora closed. Goodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
