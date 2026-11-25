"""Desktop startup must recognize Poliora rather than any open local port."""

import sys
from http.client import BadStatusLine

import pytest

from poliora import app_launcher


@pytest.mark.parametrize("payload", [b'{}', b'{"application":"other"}', b'[]', b'invalid'])
def test_unrelated_http_service_does_not_prevent_startup(monkeypatch, payload):
    class Connection:
        status = 200

        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            pass

        def getresponse(self):
            return self

        def read(self, limit):
            return payload

        def close(self):
            pass

    monkeypatch.setattr(app_launcher, "HTTPConnection", Connection)
    assert app_launcher._running_dashboard_port(8787) is None


def test_non_http_listener_does_not_prevent_startup(monkeypatch):
    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            raise BadStatusLine("not HTTP")

        def close(self):
            pass

    monkeypatch.setattr(app_launcher, "HTTPConnection", Connection)
    assert app_launcher._running_dashboard_port(8787) is None


def test_second_launch_opens_existing_poliora(monkeypatch):
    windows = []
    monkeypatch.setattr(sys, "argv", ["Poliora"])
    monkeypatch.setattr(app_launcher, "_running_dashboard_port", lambda port: 8790)
    monkeypatch.setattr(app_launcher, "_run_native_window", windows.append)
    app_launcher.main()
    assert windows == ["http://127.0.0.1:8790/"]
