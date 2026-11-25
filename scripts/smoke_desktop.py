"""Check a frozen desktop engine in an isolated user-data directory."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from http.client import HTTPConnection


def main() -> None:
    executable = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="poliora-desktop-test-") as temporary:
        environment = {**os.environ, "LOCALAPPDATA": temporary, "HOME": temporary, "USERPROFILE": temporary}
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([executable, "--no-open", "--port", str(port)], env=environment)
        try:
            deadline = time.monotonic() + 90
            last_error = "Engine did not answer"
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Desktop engine exited early: {process.returncode}")
                connection = HTTPConnection("127.0.0.1", port, timeout=2)
                try:
                    connection.request("GET", "/api/health")
                    response = connection.getresponse()
                    health = json.loads(response.read())
                    assert response.status == 200 and health["application"] == "poliora"
                    connection.request("GET", "/api/overview")
                    response = connection.getresponse()
                    overview = json.loads(response.read())
                    assert response.status == 200 and overview["report"]["requests"] == 0
                    assert any(item["model"] == "gpt-6.2-sol" for item in overview["models"])
                    print(f"Frozen desktop engine passed: {health['version']}, {len(overview['models'])} models")
                    return
                except (OSError, ValueError, AssertionError, KeyError) as error:
                    last_error = str(error)
                    time.sleep(0.25)
                finally:
                    connection.close()
            raise RuntimeError(last_error)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
