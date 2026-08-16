"""Coverage for the local dashboard's request guard.

Binding to loopback keeps the dashboard off the network, but it does not keep
it away from the person's own browser. Any page they visit while the dashboard
is running can aim a request at 127.0.0.1, and a hostile DNS record can point
an attacker-controlled hostname at loopback. These tests pin both defences and,
just as importantly, pin that ordinary local use still works.
"""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import Iterator

import pytest

from poliora.cost import init_workspace
from poliora.web import create_dashboard_server


@pytest.fixture()
def dashboard(tmp_path: Path) -> Iterator[int]:
    """Serve a dashboard on an ephemeral loopback port for one test."""
    init_workspace(tmp_path, project="guard-test", monthly_budget_usd=50.0)
    server = create_dashboard_server(tmp_path, port=0)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def request(
    port: int,
    method: str,
    path: str,
    *,
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Issue one request, overriding Host when the caller supplies it."""
    connection = HTTPConnection("127.0.0.1", port)
    sent = dict(headers or {})
    if body is not None:
        sent.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=body, headers=sent)
    response = connection.getresponse()
    payload = response.read().decode("utf-8")
    connection.close()
    return response.status, payload


# --- ordinary local use keeps working --------------------------------------


def test_dashboard_page_loads(dashboard: int) -> None:
    status, body = request(dashboard, "GET", "/")
    assert status == 200
    assert "<!doctype html>" in body.lower()


def test_overview_api_answers(dashboard: int) -> None:
    status, body = request(dashboard, "GET", "/api/overview")
    assert status == 200
    assert json.loads(body)["project"] == "guard-test"


def test_mutating_request_without_an_origin_is_allowed(dashboard: int) -> None:
    # curl, the CLI, and the test suite send no Origin. Rejecting those would
    # break scripted use without closing the browser-forged path.
    status, _ = request(dashboard, "POST", "/api/detect-tools", body="{}")
    assert status == 200


def test_mutating_request_from_the_dashboards_own_origin_is_allowed(dashboard: int) -> None:
    status, _ = request(
        dashboard,
        "POST",
        "/api/detect-tools",
        body="{}",
        headers={"Origin": f"http://127.0.0.1:{dashboard}"},
    )
    assert status == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_loopback_hostnames_are_accepted(dashboard: int, host: str) -> None:
    status, _ = request(dashboard, "GET", "/api/overview", headers={"Host": f"{host}:{dashboard}"})
    assert status == 200


# --- DNS rebinding ---------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "evil.example.com",
        "poliora.attacker.test",
        "127.0.0.1.nip.io",
        "localhost.evil.com",
    ],
)
def test_non_loopback_host_headers_are_rejected(dashboard: int, host: str) -> None:
    # A rebound DNS name resolves to loopback but arrives with its own Host.
    status, _ = request(dashboard, "GET", "/api/overview", headers={"Host": host})
    assert status == 421


def test_rebinding_is_blocked_on_mutating_verbs_too(dashboard: int) -> None:
    status, _ = request(
        dashboard, "POST", "/api/detect-tools", body="{}", headers={"Host": "evil.example.com"}
    )
    assert status == 421


def test_rejected_host_gets_a_plain_language_explanation(dashboard: int) -> None:
    _, body = request(dashboard, "GET", "/api/overview", headers={"Host": "evil.example.com"})
    assert "this computer" in json.loads(body)["error"]


# --- cross-site request forgery --------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example.com",
        "https://evil.example.com",
        "http://localhost:9999",
        "null",
    ],
)
def test_mutating_requests_from_a_foreign_origin_are_rejected(dashboard: int, origin: str) -> None:
    status, _ = request(dashboard, "POST", "/api/detect-tools", body="{}", headers={"Origin": origin})
    assert status == 403


def test_forged_import_cannot_inject_usage(dashboard: int) -> None:
    # The highest-value forgery target: silently seeding someone's cost history.
    payload = json.dumps({"csv_text": "provider,model,input_tokens,output_tokens\nopenai,gpt-5.6-sol,1,1\n"})
    status, _ = request(
        dashboard, "POST", "/api/import", body=payload, headers={"Origin": "http://evil.example.com"}
    )
    assert status == 403


def test_forged_pricing_override_is_rejected(dashboard: int) -> None:
    payload = json.dumps(
        {"provider": "anthropic", "model": "claude-opus-5", "input_per_1m": 0, "output_per_1m": 0}
    )
    status, _ = request(
        dashboard, "POST", "/api/pricing", body=payload, headers={"Origin": "http://evil.example.com"}
    )
    assert status == 403


def test_forged_delete_is_rejected(dashboard: int) -> None:
    status, _ = request(
        dashboard, "DELETE", "/api/scenarios/anything", headers={"Origin": "http://evil.example.com"}
    )
    assert status == 403


def test_forged_patch_is_rejected(dashboard: int) -> None:
    status, _ = request(
        dashboard,
        "PATCH",
        "/api/decisions/anything",
        body="{}",
        headers={"Origin": "http://evil.example.com"},
    )
    assert status == 403


def test_reads_are_not_blocked_by_a_foreign_origin(dashboard: int) -> None:
    # Origin is only load-bearing on state-changing verbs; the browser's own
    # same-origin policy already stops a foreign page reading the response.
    status, _ = request(dashboard, "GET", "/api/overview", headers={"Origin": "http://evil.example.com"})
    assert status == 200


def test_rejected_origin_gets_a_plain_language_explanation(dashboard: int) -> None:
    _, body = request(
        dashboard, "POST", "/api/detect-tools", body="{}", headers={"Origin": "http://evil.example.com"}
    )
    assert "local dashboard" in json.loads(body)["error"]


# --- unrelated request handling still behaves ------------------------------


def test_unknown_path_is_a_clean_not_found(dashboard: int) -> None:
    status, _ = request(dashboard, "GET", "/api/nope")
    assert status == 404


def test_unknown_post_path_is_a_clean_not_found(dashboard: int) -> None:
    status, _ = request(dashboard, "POST", "/api/nope", body="{}")
    assert status == 404


def test_oversized_body_is_refused(dashboard: int) -> None:
    status, _ = request(dashboard, "POST", "/api/simulate", body="x" * 40_000)
    assert status == 400


def test_non_object_json_body_is_refused(dashboard: int) -> None:
    status, _ = request(dashboard, "POST", "/api/simulate", body="[1, 2, 3]")
    assert status == 400


def test_malformed_json_body_is_refused(dashboard: int) -> None:
    status, _ = request(dashboard, "POST", "/api/simulate", body="{not json")
    assert status == 400
