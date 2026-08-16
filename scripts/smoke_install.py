"""Portable installed-package smoke test used by CI and release checks."""

from __future__ import annotations

import json
import tempfile
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from poliora.cost import init_workspace
from poliora.web import create_dashboard_server


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="poliora-smoke-") as temporary:
        root = Path(temporary)
        init_workspace(root, project="release-smoke", monthly_budget_usd=500)
        server = create_dashboard_server(root, port=0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", "/api/overview")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["project"] == "release-smoke"
            assert payload["report"]["requests"] == 0

            connection.request("GET", "/")
            response = connection.getresponse()
            page = response.read().decode("utf-8")
            assert response.status == 200
            assert "Poliora" in page and "Connections" in page
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("Poliora installed-package smoke test passed.")


if __name__ == "__main__":
    main()
