"""Local web dashboard for Poliora workspaces."""
# ruff: noqa: E501

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from poliora.cost import (
    ConnectorStore,
    DecisionStore,
    JsonlUsageStore,
    ModelCatalog,
    ModelPricing,
    PricingRegistry,
    ReportBranding,
    SavedScenario,
    SavingsDecision,
    ScenarioStore,
    UsageEvent,
    build_usage_report,
    connector_catalog,
    detect_local_tools,
    generate_recommendations,
    import_usage_csv_text,
    init_workspace,
    load_workspace,
    preview_usage_csv_text,
    render_html_report,
    scan_local_usage,
    simulate_model_switch,
    summarize_decisions,
)


def create_dashboard_server(root: str | Path, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    """Create a dashboard server without starting its request loop."""
    workspace_root = Path(root).resolve()

    class WorkspaceDashboardHandler(_DashboardRequestHandler):
        root = workspace_root

    return ThreadingHTTPServer((host, port), WorkspaceDashboardHandler)


def run_dashboard(root: str | Path = ".", host: str = "127.0.0.1", port: int = 8787) -> None:
    """Serve the Poliora dashboard until interrupted."""
    server = create_dashboard_server(root, host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


class _DashboardRequestHandler(BaseHTTPRequestHandler):
    root = Path(".")

    def _guard(self, *, mutating: bool) -> bool:
        """Reject requests a browser on another site could have forged.

        The dashboard binds to loopback, but "only reachable from this machine"
        is not the same as "only reachable by this user". Any page the person
        visits while the dashboard runs can aim a request at 127.0.0.1, and a
        hostile DNS record can point an attacker-controlled name at loopback.
        Two checks close both doors:

        * Host must be a loopback name, which defeats DNS rebinding.
        * On state-changing verbs, a supplied Origin must be our own. Browsers
          attach Origin to cross-site form posts, so this blocks the forged-form
          path while leaving curl and the test suite (which send none) working.
        """
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if hostname and hostname not in _LOOPBACK_HOSTS:
            self._send_json(
                {"error": "The dashboard only answers requests addressed to this computer."},
                status=HTTPStatus.MISDIRECTED_REQUEST,
            )
            return False

        origin = self.headers.get("Origin")
        if mutating and origin:
            allowed = {f"http://{host}", f"https://{host}"}
            if origin not in allowed:
                self._send_json(
                    {"error": "This request did not come from the local dashboard."},
                    status=HTTPStatus.FORBIDDEN,
                )
                return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard(mutating=False):
            return
        request = urlparse(self.path)
        if request.path in {"/", "/index.html"}:
            self._send_html(_dashboard_page())
            return
        if request.path == "/api/overview":
            try:
                since_days = _since_days(parse_qs(request.query))
            except ValueError as error:
                self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(self._overview(since_days=since_days))
            return
        if request.path == "/api/system-scan":
            from poliora.cost import scan_system_ai_environment
            self._send_json(scan_system_ai_environment(self.root).to_dict())
            return
        if request.path == "/report.html":
            try:
                query = parse_qs(request.query)
                since_days = _since_days(query)
                branding = _report_branding(query)
            except ValueError as error:
                self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_report(since_days=since_days, branding=branding)
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard(mutating=True):
            return
        request = urlparse(self.path)
        path = request.path
        connector_action = _connector_action(path)
        if path not in {
            "/api/simulate",
            "/api/pricing",
            "/api/scenarios",
            "/api/decisions",
            "/api/demo-data",
            "/api/import/preview",
            "/api/import",
            "/api/detect-tools",
            "/api/detect-history",
        } and connector_action is None:
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body(max_bytes=2_000_000 if path.startswith("/api/import") else 32_768)
            if path == "/api/simulate":
                result = self._simulate(payload)
            elif path == "/api/pricing":
                result = self._save_pricing(payload)
            elif path == "/api/demo-data":
                result = self._seed_demo_data()
            elif path == "/api/import/preview":
                result = self._preview_csv(payload)
            elif path == "/api/import":
                result = self._import_csv(payload)
            elif path == "/api/detect-tools":
                result = self._detect_tools()
            elif path == "/api/detect-history":
                result = self._detect_history(payload)
            elif path == "/api/decisions":
                result = self._save_decision(payload)
            elif connector_action is not None:
                result = self._update_connector(*connector_action)
            else:
                result = self._save_scenario(payload)
        except (KeyError, TypeError, ValueError) as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json(result)

    def _detect_tools(self) -> dict[str, object]:
        """Run the narrow availability scan only after a browser request."""
        return {
            "tools": [item.to_dict() for item in detect_local_tools(self.root)],
            "notice": "This scan checks supported launcher availability and the Poliora workspace helper only. It does not open AI tools or read chats, code, prompts, credentials, or account history.",
        }

    def _detect_history(self, payload: dict[str, Any]) -> dict[str, object]:
        """Read supported local usage logs only after the person asks us to."""
        workspace = load_workspace(self.root)
        registry = PricingRegistry.load(workspace.pricing_path)
        scans = scan_local_usage(registry=registry)
        detected = [scan for scan in scans if scan.available]
        events = [event for scan in detected for event in scan.events]
        imported = 0

        if bool(payload.get("import")):
            store = JsonlUsageStore(workspace.usage_path)
            existing = {_usage_identity(event) for event in store.read_all()}
            for event in events:
                identity = _usage_identity(event)
                if identity not in existing:
                    store.append(event)
                    existing.add(identity)
                    imported += 1

        equivalent_value = sum(scan.equivalent_api_cost_usd for scan in detected)
        return {
            "scans": [scan.to_dict() for scan in scans],
            "requests": len(events),
            "equivalent_api_cost_usd": round(equivalent_value, 4),
            "imported_events": imported,
            "notice": (
                "Poliora read only timestamps, model names, token totals, plan and quota metadata "
                "from supported local logs. It did not read prompts, replies, code, credentials, or chats."
            ),
        }

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._guard(mutating=True):
            return
        request = urlparse(self.path)
        decision_id = _resource_id(request.path, "/api/decisions/")
        if decision_id is None:
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body(max_bytes=32_768)
            result = self._update_decision(decision_id, payload)
        except (KeyError, TypeError, ValueError) as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json(result)

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._guard(mutating=True):
            return
        request = urlparse(self.path)
        scenario_id = _resource_id(request.path, "/api/scenarios/")
        decision_id = _resource_id(request.path, "/api/decisions/")
        if scenario_id is None and decision_id is None:
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        workspace = load_workspace(self.root)
        deleted = (
            ScenarioStore(workspace.scenarios_path).delete(scenario_id)
            if scenario_id is not None
            else DecisionStore(workspace.decisions_path).delete(str(decision_id))
        )
        if not deleted:
            self._send_json({"error": "Record was not found."}, status=HTTPStatus.NOT_FOUND)
            return
        self._send_json({"deleted": True})

    def _overview(self, *, since_days: int | None = None) -> dict[str, object]:
        workspace = load_workspace(self.root)
        store = JsonlUsageStore(workspace.usage_path)
        events = store.read_all() if since_days is None else store.read_since(_cutoff(since_days))
        report = build_usage_report(events, monthly_budget_usd=workspace.monthly_budget_usd)
        registry = PricingRegistry.load(workspace.pricing_path)
        catalog = ModelCatalog.load(workspace.catalog_path)
        models = _catalog_rows(catalog, registry, events)
        decisions = DecisionStore(workspace.decisions_path).read_all()
        data_quality = _data_quality(events, registry)
        return {
            "project": workspace.project,
            "currency": workspace.currency,
            "report": report.to_dict(),
            "recommendations": [item.to_dict() for item in generate_recommendations(report)],
            "models": models,
            "connectors": _connector_rows(workspace),
            "scenarios": [item.to_dict() for item in ScenarioStore(workspace.scenarios_path).read_all()],
            "decisions": [item.to_dict() for item in decisions],
            "savings_ledger": summarize_decisions(decisions).to_dict(),
            "evidence": _evidence_grade(report, data_quality, decisions),
            "data_quality": data_quality,
            "catalog_health": {
                "catalog_models": len(models),
                "priced_models": sum(1 for item in models if item["priced"]),
                "custom_or_observed_models": sum(
                    1 for item in models if item["status"] in {"custom", "observed", "account-available"}
                ),
            },
        }

    def _simulate(self, payload: dict[str, Any]) -> dict[str, object]:
        return self._run_simulation(payload).to_dict()

    def _run_simulation(self, payload: dict[str, Any]):
        workspace = load_workspace(self.root)
        events = JsonlUsageStore(workspace.usage_path).read_all()
        return simulate_model_switch(
            events,
            source_provider=str(payload["source_provider"]),
            source_model=str(payload["source_model"]),
            target_provider=str(payload["target_provider"]),
            target_model=str(payload["target_model"]),
            percentage=float(payload.get("percentage", 100)),
            registry=PricingRegistry.load(workspace.pricing_path),
        )

    def _save_scenario(self, payload: dict[str, Any]) -> dict[str, object]:
        simulation = self._run_simulation(payload)
        name = str(payload.get("name") or "").strip()
        if not name:
            name = f"{simulation.source_model} to {simulation.target_model} ({simulation.percentage:.0f}%)"
        if len(name) > 120:
            raise ValueError("Scenario name must be 120 characters or fewer.")
        workspace = load_workspace(self.root)
        scenario = SavedScenario.from_simulation(name, simulation)
        ScenarioStore(workspace.scenarios_path).save(scenario)
        return {"scenario": scenario.to_dict()}

    def _save_decision(self, payload: dict[str, Any]) -> dict[str, object]:
        simulation = self._run_simulation(payload)
        name = str(payload.get("name") or "").strip()
        if not name:
            name = f"Validate {simulation.source_model} to {simulation.target_model}"
        workspace = load_workspace(self.root)
        decision = SavingsDecision.from_simulation(name, simulation)
        DecisionStore(workspace.decisions_path).save(decision)
        return {"decision": decision.to_dict()}

    def _update_decision(self, decision_id: str, payload: dict[str, Any]) -> dict[str, object]:
        workspace = load_workspace(self.root)
        store = DecisionStore(workspace.decisions_path)
        current = store.get(decision_id)
        if current is None:
            raise ValueError("Savings decision was not found.")
        measured_raw = payload.get("measured_monthly_savings_usd")
        measured = None if measured_raw in (None, "") else float(measured_raw)
        updated = current.update(
            status=str(payload.get("status") or current.status),
            quality_status=str(payload.get("quality_status") or current.quality_status),
            measured_monthly_savings_usd=measured,
            notes=str(payload.get("notes") or ""),
        )
        store.save(updated)
        return {"decision": updated.to_dict()}

    def _save_pricing(self, payload: dict[str, Any]) -> dict[str, object]:
        """Persist a local contract-rate override from the dashboard."""
        provider = _required_text(payload, "provider")
        model = _required_text(payload, "model")
        input_per_1m = _non_negative_number(payload, "input_per_1m")
        output_per_1m = _non_negative_number(payload, "output_per_1m")
        cached_raw = payload.get("cached_input_per_1m")
        cached_input_per_1m = None if cached_raw in (None, "") else _non_negative_number(payload, "cached_input_per_1m")
        note = str(payload.get("note") or "workspace contract rate").strip()
        if len(note) > 300:
            raise ValueError("Pricing note must be 300 characters or fewer.")

        workspace = load_workspace(self.root)
        registry = PricingRegistry.load(workspace.pricing_path)
        pricing = ModelPricing(
            provider=provider,
            model=model,
            input_per_1m=input_per_1m,
            output_per_1m=output_per_1m,
            cached_input_per_1m=cached_input_per_1m,
            note=note or "workspace contract rate",
        )
        registry.add(pricing)
        registry.save(workspace.pricing_path)
        return {"pricing": pricing.to_dict()}

    def _update_connector(self, connector_id: str, action: str) -> dict[str, object]:
        definition = next((item for item in connector_catalog() if item.id == connector_id), None)
        if definition is None:
            raise ValueError("Unknown Companion connector.")
        workspace = load_workspace(self.root)
        store = ConnectorStore(workspace.connectors_path)
        if action == "consent":
            connection = store.consent(connector_id)
            return {"connector": {**definition.to_dict(), "connection": connection.to_dict()}}
        if not store.disconnect(connector_id):
            raise ValueError("Connector is not enabled.")
        return {"disconnected": True}

    def _seed_demo_data(self) -> dict[str, object]:
        """Create a safe, fictional dataset for trying the local dashboard."""
        workspace = init_workspace(self.root, project="guided-demo", monthly_budget_usd=500.0)
        store = JsonlUsageStore(workspace.usage_path)
        if store.read_all():
            raise ValueError("Guided sample data only loads into an empty workspace.")

        registry = PricingRegistry.load(workspace.pricing_path)
        now = datetime.now(timezone.utc)
        samples = [
            (8, "openai", "gpt-5.5", 14_000, 2_000, 4_000, "support-agent", "Northwind", 0.03),
            (7, "openai", "gpt-5.5", 11_000, 1_600, 3_000, "support-agent", "Northwind", 0.02),
            (6, "openai", "gpt-5.4", 18_000, 1_900, 6_000, "document-review", "Contoso", 0.00),
            (5, "google", "gemini-3.5-flash", 8_000, 1_200, 2_000, "knowledge-search", "Northwind", 0.01),
            (4, "openai", "gpt-5.5", 17_000, 2_700, 5_000, "support-agent", "Northwind", 0.04),
            (3, "anthropic", "claude-opus-4-6", 12_000, 2_100, 3_000, "proposal-draft", "Fabrikam", 0.00),
            (2, "openai", "gpt-5.5", 36_000, 6_000, 8_000, "support-agent", "Northwind", 0.06),
            (1, "google", "gemini-3.5-flash", 9_000, 1_400, 1_000, "knowledge-search", "Contoso", 0.01),
            (0, "openai", "gpt-5.4", 20_000, 2_600, 7_000, "document-review", "Fabrikam", 0.00),
        ]
        for days_ago, provider, model, input_tokens, output_tokens, cached_tokens, operation, user, tool_cost in samples:
            cost = registry.estimate(
                provider,
                model,
                input_tokens,
                output_tokens,
                cached_input_tokens=cached_tokens,
            ) + tool_cost
            store.append(
                UsageEvent(
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_tokens,
                    cost_usd=cost,
                    tool_cost_usd=tool_cost,
                    reasoning_tokens=round(output_tokens * 0.3),
                    operation=operation,
                    project=workspace.project,
                    user=user,
                    trace_id=f"guided-{days_ago}",
                    timestamp=(now - timedelta(days=days_ago)).isoformat(),
                    metadata={"source": "guided-sample"},
                )
            )
        return {"imported_events": len(samples), "project": workspace.project}

    def _preview_csv(self, payload: dict[str, Any]) -> dict[str, object]:
        workspace = load_workspace(self.root)
        preview = preview_usage_csv_text(
            _csv_text(payload),
            source_name=_upload_name(payload),
            registry=PricingRegistry.load(workspace.pricing_path),
            default_provider=_optional_payload_text(payload, "provider"),
            default_project=_optional_payload_text(payload, "project") or workspace.project,
        )
        return {"preview": preview.to_dict()}

    def _import_csv(self, payload: dict[str, Any]) -> dict[str, object]:
        workspace = load_workspace(self.root)
        skip_invalid = payload.get("skip_invalid", False)
        if not isinstance(skip_invalid, bool):
            raise ValueError("skip_invalid must be true or false.")
        result = import_usage_csv_text(
            _csv_text(payload),
            JsonlUsageStore(workspace.usage_path),
            source_name=_upload_name(payload),
            registry=PricingRegistry.load(workspace.pricing_path),
            default_provider=_optional_payload_text(payload, "provider"),
            default_project=_optional_payload_text(payload, "project") or workspace.project,
            skip_invalid=skip_invalid,
        )
        return {"result": result.to_dict()}

    def _send_report(self, *, since_days: int | None = None, branding: ReportBranding | None = None) -> None:
        workspace = load_workspace(self.root)
        store = JsonlUsageStore(workspace.usage_path)
        events = store.read_all() if since_days is None else store.read_since(_cutoff(since_days))
        report = build_usage_report(events, monthly_budget_usd=workspace.monthly_budget_usd)
        output = render_html_report(
            report,
            generate_recommendations(report),
            project=workspace.project,
            branding=branding,
            decisions=DecisionStore(workspace.decisions_path).read_all(),
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="poliora-spend-report.html"')
        self.end_headers()
        self.wfile.write(output.encode("utf-8"))

    def _read_json_body(self, *, max_bytes: int) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_bytes:
            raise ValueError(f"Request body must be a JSON object smaller than {max_bytes // 1000:,} KB.")
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object.")
        return parsed

    def _send_json(self, data: dict[str, object], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        """Keep normal browser requests out of the terminal."""


def _dashboard_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Poliora</title>
  <link rel="icon" href="data:,">
  <style>
    :root {
      --ink: #11191f;
      --muted: #5f6b73;
      --line: #cfd8d9;
      --canvas: #edf1f0;
      --card: #ffffff;
      --green: #007a57;
      --green-bright: #35d39b;
      --green-soft: #d9f5e9;
      --blue: #285bd4;
      --blue-soft: #e8efff;
      --amber: #a85b00;
      --amber-soft: #ffedc2;
      --red: #c3413b;
      --rail: #121c22;
    }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    body {
      margin: 0;
      overflow-x: hidden;
      background: var(--canvas);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .topbar {
      display: flex;
      min-height: 68px;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 0 24px;
      background: var(--card);
      border-bottom: 3px solid var(--ink);
    }
    .brand-lockup { display: flex; align-items: center; gap: 10px; }
    .brand-index { display: grid; width: 34px; height: 34px; place-items: center; background: var(--ink); color: var(--green-bright); font-size: 12px; font-weight: 900; }
    .brand-copy { display: flex; flex-direction: column; line-height: 1.05; }
    .brand { color: var(--ink); font-size: 17px; font-weight: 900; letter-spacing: 0; text-transform: uppercase; }
    .brand-copy small { margin-top: 4px; color: var(--muted); font-size: 9px; font-weight: 800; text-transform: uppercase; }
    .workspace { color: var(--muted); font-size: 14px; }
    .right { display: flex; align-items: center; gap: 12px; }
    .live { color: var(--green); font-size: 10px; font-weight: 850; text-transform: uppercase; }
    .live::before { content: ""; display: inline-block; width: 8px; height: 8px; margin-right: 6px; background: var(--green); border-radius: 50%; }
    .export, .start-guide { margin: 0; color: var(--ink); background: #fff; text-decoration: none; border: 1px solid var(--line); border-radius: 6px; padding: 8px 11px; font-size: 13px; font-weight: 700; white-space: nowrap; }
    .export:hover, .start-guide:hover, .start-guide.active { color: var(--blue); background: var(--blue-soft); }
    .start-guide { border-color: #b8e5cf; color: var(--green); }
    .start-guide-compact { display: none; }
    .app-shell { display: grid; grid-template-columns: 238px minmax(0, 1fr); max-width: 1540px; min-height: calc(100vh - 68px); margin: 0 auto; }
    .sidebar { padding: 24px 14px; border-right: 0; background: var(--rail); color: #fff; }
    .nav-label { margin: 0 10px 9px; color: #8fa0aa; font-size: 9px; font-weight: 850; text-transform: uppercase; }
    .nav-label.nav-section { margin-top: 22px; }
    .nav-button { display: block; width: 100%; margin: 2px 0; padding: 10px 11px; border: 0; border-left: 3px solid transparent; border-radius: 2px; background: transparent; color: #dce5e8; font-size: 12px; font-weight: 750; text-align: left; }
    .nav-button:hover { background: #203039; color: #fff; }
    .nav-button.active { border-left-color: var(--green-bright); background: #fff; color: var(--ink); }
    .nav-button span { float: right; color: #8fa0aa; font-size: 10px; font-weight: 750; }
    .sidebar-status { margin-top: 28px; padding: 16px 11px 0; border-top: 1px solid #314049; }
    .sidebar-status h2 { margin: 0 0 11px; color: #fff; font-size: 11px; }
    .status-line { display: flex; justify-content: space-between; gap: 8px; margin: 9px 0; color: #8fa0aa; font-size: 10px; }
    .status-line strong { color: #fff; font-weight: 750; text-align: right; }
    .rail-motto { margin: 26px 10px 0; color: var(--green-bright); font-size: 9px; font-weight: 850; line-height: 1.5; text-transform: uppercase; }
    main { min-width: 0; padding: 30px 34px 52px; }
    .view { display: none; min-width: 0; }
    .view.active { display: block; }
    .welcome-hero { max-width: 880px; padding: 25px 0 30px; border-bottom: 1px solid var(--line); }
    .welcome-hero h1 { max-width: 680px; font-size: 34px; }
    .welcome-hero .summary { max-width: 670px; font-size: 16px; line-height: 1.55; }
    .welcome-choices { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 28px; }
    .welcome-choice { min-height: 214px; padding: 18px; border: 1px solid var(--ink); border-radius: 4px; background: #fff; color: var(--ink); text-align: left; font: inherit; cursor: pointer; }
    .welcome-choice:hover { background: var(--green-soft); }
    .welcome-choice:nth-child(2):hover { background: var(--blue-soft); }
    .welcome-choice:nth-child(3):hover { background: var(--amber-soft); }
    .welcome-choice span { display: block; margin-bottom: 30px; color: var(--green); font-size: 11px; font-weight: 850; text-transform: uppercase; }
    .welcome-choice:nth-child(2) span { color: var(--blue); }
    .welcome-choice:nth-child(3) span { color: var(--amber); }
    .welcome-choice strong { display: block; font-size: 17px; line-height: 1.2; }
    .welcome-choice p { margin: 9px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .welcome-boundary { margin-top: 24px; padding: 16px 18px; border-left: 3px solid var(--green); background: #fff; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .welcome-boundary strong { color: var(--ink); }
    .view-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 20px; }
    .view-heading h1 { max-width: 780px; font-size: 26px; }
    .view-kicker { margin: 0 0 5px; color: var(--green); font-size: 11px; font-weight: 800; text-transform: uppercase; }
    .heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; }
    h1 { margin: 0; font-size: 27px; letter-spacing: 0; line-height: 1.15; }
    .summary { margin: 7px 0 0; color: var(--muted); font-size: 14px; }
    .updated { color: var(--muted); font-size: 12px; text-align: right; }
    .guide { margin-top: 24px; padding: 18px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
    .guide-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }
    .guide h2 { margin: 0; font-size: 16px; }
    .guide-status { margin: 6px 0 0; color: var(--muted); font-size: 13px; }
    .guide-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; margin-top: 16px; }
    .guide-step { padding-left: 12px; border-left: 3px solid var(--blue); }
    .guide-step:nth-child(2) { border-color: var(--green); }
    .guide-step:nth-child(3) { border-color: var(--amber); }
    .guide-step strong { display: block; font-size: 13px; }
    .guide-step p { margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
    .glossary { margin-top: 16px; color: var(--muted); font-size: 13px; }
    .glossary summary { color: var(--blue); cursor: pointer; font-weight: 700; }
    .glossary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 18px; margin: 11px 0 0; }
    .glossary-grid div { line-height: 1.4; }
    .glossary-grid strong { color: var(--ink); }
    .demo-action { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
    .demo-action button { margin: 0; }
    .demo-action span { color: var(--muted); font-size: 12px; }
    .connection-center { margin-top: 18px; }
    .connector-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 17px; }
    .connector-card { min-height: 210px; padding: 16px; border: 1px solid var(--line); border-radius: 8px; background: #fff; display: flex; flex-direction: column; }
    .connector-card h3 { margin: 12px 0 0; font-size: 15px; }
    .connector-card p { margin: 7px 0 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
    .connector-card button { align-self: flex-start; margin-top: auto; padding: 7px 9px; background: transparent; border: 1px solid var(--line); color: var(--blue); font-size: 12px; }
    .connector-card button:hover { background: var(--blue-soft); }
    .connector-kind { color: var(--muted); font-size: 11px; font-weight: 700; text-transform: uppercase; }
    .connector-state { display: inline-block; align-self: flex-start; padding: 3px 6px; border-radius: 4px; background: var(--blue-soft); color: var(--blue); font-size: 11px; font-weight: 800; }
    .connector-state.waiting { background: var(--amber-soft); color: var(--amber); }
    .connector-state.enabled { background: var(--green-soft); color: var(--green); }
    .connector-info { margin: 14px 0 0; padding: 11px; border: 1px solid var(--line); border-radius: 6px; background: var(--canvas); color: var(--muted); font-size: 13px; line-height: 1.45; }
    .connector-list { margin: 9px 0 0; padding-left: 18px; color: var(--muted); font-size: 13px; line-height: 1.55; }
    .tool-scan { margin-bottom: 14px; border-left: 4px solid var(--green); }
    .tool-scan-head { display: flex; align-items: start; justify-content: space-between; gap: 18px; }
    .tool-scan-head h2 { margin: 0; }
    .tool-scan-head p { margin: 5px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .tool-scan-head button { margin: 0; white-space: nowrap; }
    .tool-scan-results { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
    .tool-scan-result { padding: 13px; border: 1px solid var(--line); background: var(--canvas); }
    .tool-scan-result strong { display: block; font-size: 13px; }
    .tool-scan-result .scan-state { display: inline-block; margin-top: 6px; color: var(--amber); font-size: 11px; font-weight: 800; }
    .tool-scan-result.detected .scan-state { color: var(--green); }
    .tool-scan-result p { margin: 8px 0 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
    .import-panel { margin-bottom: 14px; }
    .import-controls { display: grid; grid-template-columns: minmax(220px, 1.4fr) minmax(150px, 0.6fr) minmax(150px, 0.6fr); gap: 12px; align-items: end; }
    .check-line { display: flex; align-items: center; gap: 8px; margin-top: 14px; color: var(--ink); font-size: 12px; font-weight: 650; }
    .check-line input { width: auto; margin: 0; }
    .import-preview { margin-top: 16px; padding: 14px; border: 1px solid var(--line); border-radius: 6px; background: var(--canvas); }
    .preview-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
    .preview-stat { padding: 9px; border-left: 3px solid var(--blue); background: #fff; }
    .preview-stat span { display: block; color: var(--muted); font-size: 10px; text-transform: uppercase; }
    .preview-stat strong { display: block; margin-top: 3px; font-size: 15px; }
    .preview-detail { margin: 11px 0 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
    .issue-list { margin: 10px 0 0; padding-left: 19px; color: var(--red); font-size: 12px; line-height: 1.5; }
    .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 24px; }
    .metric, .panel { background: var(--card); border: 1px solid var(--line); border-radius: 3px; }
    .metrics { gap: 0; border: 1px solid var(--ink); background: var(--card); }
    .metric { min-height: 120px; padding: 17px; border: 0; border-right: 1px solid var(--line); border-radius: 0; }
    .metric:last-child { border-right: 0; }
    .metric label { color: var(--muted); font-size: 13px; }
    .metric strong { display: block; margin-top: 13px; font-size: 26px; letter-spacing: 0; }
    .metric small { display: block; margin-top: 6px; color: var(--muted); font-size: 12px; }
    .filters { display: flex; gap: 8px; margin-top: 18px; }
    .filter { margin: 0; background: transparent; border: 1px solid var(--line); color: var(--muted); padding: 7px 10px; }
    .filter:hover, .filter.active { background: var(--blue-soft); border-color: #b9cef6; color: var(--blue); }
    .grid { display: grid; grid-template-columns: 1.12fr 0.88fr; gap: 14px; margin-top: 14px; }
    .panel { min-width: 0; padding: 20px; overflow-x: auto; box-shadow: 3px 3px 0 rgba(17, 25, 31, 0.05); }
    .panel h2 { margin: 0; font-size: 16px; letter-spacing: 0; }
    .panel-note { margin: 5px 0 18px; color: var(--muted); font-size: 13px; }
    .cost-row { margin: 17px 0; }
    .cost-head { display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }
    .cost-head span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .bar { height: 8px; margin-top: 8px; background: #e8edf0; border-radius: 99px; overflow: hidden; }
    .bar i { display: block; height: 100%; background: var(--blue); border-radius: inherit; }
    .trend-row { display: grid; grid-template-columns: 86px 1fr auto; align-items: center; gap: 10px; margin: 12px 0; font-size: 13px; }
    .trend-row .bar { margin: 0; }
    .quality-row { display: flex; justify-content: space-between; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 14px; }
    .quality-row:last-child { border-bottom: 0; }
    .quality-row strong { color: var(--green); }
    .budget-box { display: grid; place-items: center; min-height: 226px; text-align: center; background: var(--green-soft); border: 1px solid #b8e5cf; border-radius: 8px; }
    .budget-box strong { display: block; color: var(--green); font-size: 43px; line-height: 1; }
    .budget-box p { margin: 9px 0 0; color: #28634c; font-size: 14px; }
    .budget-box small { display: block; margin-top: 16px; color: #28634c; }
    .simulator { margin-top: 14px; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 17px; }
    label { color: var(--muted); display: block; font-size: 12px; font-weight: 700; }
    select, input { width: 100%; margin-top: 6px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); font: inherit; font-size: 14px; padding: 9px; }
    input[type="range"] { padding: 0; accent-color: var(--green); }
    .range-line { display: flex; align-items: center; gap: 13px; margin-top: 17px; }
    .range-line label { min-width: 130px; }
    .range-line output { min-width: 42px; color: var(--green); font-size: 14px; font-weight: 800; text-align: right; }
    button { margin-top: 18px; border: 0; border-radius: 3px; background: var(--green); color: white; cursor: pointer; font: inherit; font-size: 14px; font-weight: 750; padding: 10px 14px; }
    button:hover { background: #056c4d; }
    .simulation-result { display: none; margin-top: 17px; padding: 14px; background: var(--blue-soft); border: 1px solid #ceddf9; border-radius: 6px; }
    .simulation-result strong { color: var(--blue); font-size: 20px; }
    .simulation-result p { margin: 5px 0 0; color: #3d5575; font-size: 13px; }
    .error { color: var(--red); }
    .recommendations { margin-top: 14px; }
    .recommendation { display: grid; grid-template-columns: 92px 1fr auto; gap: 14px; padding: 15px 0; border-bottom: 1px solid var(--line); }
    .recommendation:last-child { border-bottom: 0; padding-bottom: 0; }
    .tag { align-self: start; color: var(--amber); background: var(--amber-soft); border-radius: 99px; font-size: 12px; font-weight: 750; padding: 4px 8px; text-align: center; }
    .recommendation h3 { margin: 0; font-size: 14px; }
    .recommendation p { margin: 5px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .savings { color: var(--green); font-size: 13px; font-weight: 800; text-align: right; white-space: nowrap; }
    table { width: 100%; min-width: 680px; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 11px 8px; border-bottom: 1px solid var(--line); text-align: right; }
    th { color: var(--muted); font-size: 11px; text-transform: uppercase; }
    th:first-child, td:first-child { text-align: left; }
    .empty { color: var(--muted); font-size: 14px; }
    .catalog-toolbar { display: grid; grid-template-columns: 1fr 190px auto; align-items: end; gap: 10px; margin-bottom: 13px; }
    .catalog-toolbar input, .catalog-toolbar select { margin-top: 5px; }
    .catalog-count { color: var(--muted); font-size: 12px; padding: 9px 0; text-align: right; white-space: nowrap; }
    .model-meta { color: var(--muted); font-size: 11px; }
    .capability { display: inline-block; margin: 2px 4px 0 0; padding: 2px 5px; background: var(--blue-soft); color: var(--blue); border-radius: 4px; font-size: 10px; font-weight: 700; }
    .source { color: var(--blue); font-size: 12px; text-decoration: none; }
    .source:hover { text-decoration: underline; }
    .spend-watch { margin-top: 16px; padding: 12px; border: 1px solid #b8e5cf; border-radius: 6px; background: var(--green-soft); color: #28634c; font-size: 13px; }
    .spend-watch.alert { border-color: #f1d194; background: var(--amber-soft); color: #805400; }
    .spend-watch strong { display: block; margin-bottom: 3px; }
    .rate-button { margin: 8px 0 0; padding: 6px 8px; background: transparent; border: 1px solid var(--line); color: var(--blue); font-size: 12px; }
    .rate-button:hover { background: var(--blue-soft); }
    dialog { width: min(520px, calc(100vw - 32px)); border: 1px solid var(--line); border-radius: 8px; color: var(--ink); padding: 0; box-shadow: 0 18px 50px rgba(25, 36, 45, 0.2); }
    dialog::backdrop { background: rgba(25, 36, 45, 0.38); }
    .dialog-body { padding: 22px; }
    .dialog-body h2 { margin: 0; font-size: 18px; }
    .dialog-model { margin: 5px 0 18px; color: var(--muted); font-size: 13px; }
    .dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
    .dialog-actions button { margin-top: 0; }
    .dialog-actions .secondary { background: transparent; border: 1px solid var(--line); color: var(--ink); }
    .form-error { min-height: 18px; margin: 12px 0 0; color: var(--red); font-size: 13px; }
    input[type="color"] { min-height: 40px; padding: 3px; cursor: pointer; }
    .button-row { display: flex; flex-wrap: wrap; gap: 8px; }
    .button-row button { margin-top: 18px; }
    .button-row .secondary { background: transparent; border: 1px solid var(--line); color: var(--ink); }
    .scenario-row { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 14px; padding: 13px 0; border-bottom: 1px solid var(--line); }
    .scenario-row:last-child { border-bottom: 0; }
    .scenario-row h3 { margin: 0; font-size: 14px; }
    .scenario-row p { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
    .scenario-value { color: var(--green); font-size: 13px; font-weight: 800; text-align: right; }
    .scenario-remove { margin: 0; padding: 6px 8px; background: transparent; border: 1px solid var(--line); color: var(--muted); font-size: 12px; }
    .scenario-remove:hover { border-color: #e2b3b3; color: var(--red); background: #fff5f5; }
    .scenario-name { grid-column: 1 / -1; }
    .manual-intro { max-width: 960px; padding: 18px 20px; border-left: 4px solid var(--green); background: var(--green-soft); }
    .manual-intro strong { display: block; font-size: 15px; }
    .manual-intro p { margin: 6px 0 0; color: #28634c; font-size: 13px; line-height: 1.55; }
    .manual-section { max-width: 1040px; min-width: 0; padding: 26px 0; border-bottom: 1px solid var(--line); }
    .manual-section:last-child { border-bottom: 0; }
    .manual-section h2 { margin: 0; font-size: 18px; }
    .manual-lead { max-width: 780px; margin: 7px 0 18px; color: var(--muted); font-size: 13px; line-height: 1.55; }
    .manual-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; }
    .manual-step { min-width: 0; }
    .manual-number { display: grid; width: 28px; height: 28px; place-items: center; border-radius: 50%; background: var(--blue-soft); color: var(--blue); font-size: 12px; font-weight: 800; }
    .manual-step strong { display: block; margin-top: 10px; font-size: 13px; }
    .manual-step p { margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
    .manual-rows { border-top: 1px solid var(--line); }
    .manual-row { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 24px; padding: 15px 0; border-bottom: 1px solid var(--line); }
    .manual-row strong { font-size: 13px; }
    .manual-row p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .integration-row { display: grid; grid-template-columns: 160px minmax(0, 1fr) 145px; gap: 22px; align-items: start; padding: 18px 0; border-bottom: 1px solid var(--line); }
    .integration-row:first-of-type { border-top: 1px solid var(--line); }
    .integration-row > div { min-width: 0; }
    .integration-row h3 { margin: 0; font-size: 14px; }
    .integration-row p { margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
    .integration-status { justify-self: end; padding: 4px 7px; border-radius: 4px; background: var(--green-soft); color: var(--green); font-size: 11px; font-weight: 800; text-align: center; }
    .integration-status.waiting { background: var(--amber-soft); color: var(--amber); }
    .command-line { display: block; width: 100%; max-width: 100%; margin: 9px 0 0; padding: 9px 11px; overflow-x: auto; border: 1px solid var(--line); border-radius: 5px; background: #1f2933; color: #f5f7f7; font-family: Consolas, "Courier New", monospace; font-size: 11px; line-height: 1.45; white-space: pre; }
    .manual-note { margin-top: 13px; padding: 11px 13px; overflow-wrap: anywhere; border-left: 3px solid var(--amber); background: var(--amber-soft); color: #805400; font-size: 12px; line-height: 1.5; }
    .evidence-seal { display: grid; grid-template-columns: 52px minmax(150px, 230px); gap: 12px; align-items: center; padding: 10px 12px; border: 1px solid var(--ink); background: #fff; }
    .evidence-grade { display: grid; width: 52px; height: 52px; place-items: center; background: var(--ink); color: var(--green-bright); font-size: 28px; font-weight: 900; }
    .evidence-copy strong { display: block; font-size: 12px; text-transform: uppercase; }
    .evidence-copy span { display: block; margin-top: 3px; color: var(--muted); font-size: 10px; line-height: 1.35; }
    .launch-strip { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-top: 20px; padding: 12px 14px; border-left: 4px solid var(--blue); background: #fff; }
    .launch-strip strong { display: block; font-size: 12px; text-transform: uppercase; }
    .launch-strip p { margin: 3px 0 0; color: var(--muted); font-size: 12px; }
    .launch-strip button { flex: 0 0 auto; margin: 0; padding: 7px 10px; background: transparent; border: 1px solid var(--line); color: var(--blue); font-size: 11px; }
    .ledger-ribbon { display: grid; grid-template-columns: 1.2fr repeat(3, minmax(120px, 0.7fr)); margin-top: 14px; border: 1px solid var(--ink); background: var(--ink); color: #fff; }
    .ledger-title, .ledger-stat { padding: 15px 17px; }
    .ledger-title { border-right: 1px solid #3a474e; }
    .ledger-title strong { display: block; color: var(--green-bright); font-size: 11px; text-transform: uppercase; }
    .ledger-title p { margin: 5px 0 0; color: #b8c3c8; font-size: 11px; line-height: 1.4; }
    .ledger-stat { border-right: 1px solid #3a474e; }
    .ledger-stat:last-child { border-right: 0; }
    .ledger-stat span { display: block; color: #9dacb4; font-size: 9px; text-transform: uppercase; }
    .ledger-stat strong { display: block; margin-top: 5px; font-size: 20px; }
    .fingerprint { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0; border-top: 1px solid var(--line); }
    .fingerprint-column { min-width: 0; padding: 14px 16px; border-right: 1px solid var(--line); }
    .fingerprint-column:last-child { border-right: 0; }
    .fingerprint-column h3 { margin: 0 0 12px; color: var(--muted); font-size: 10px; text-transform: uppercase; }
    .fingerprint-item { margin: 10px 0; }
    .fingerprint-head { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; }
    .fingerprint-head span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .fingerprint-head strong { font-variant-numeric: tabular-nums; }
    .fingerprint-track { height: 4px; margin-top: 5px; background: #e5eaeb; }
    .fingerprint-track i { display: block; height: 100%; background: var(--blue); }
    .fingerprint-column:nth-child(2) .fingerprint-track i { background: var(--green); }
    .fingerprint-column:nth-child(3) .fingerprint-track i { background: var(--amber); }
    .decision-summary { margin-top: 0; }
    .decision-list { border-top: 1px solid var(--line); }
    .decision-row { padding: 17px 0; border-bottom: 1px solid var(--line); }
    .decision-head { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 12px; align-items: center; }
    .decision-head h3 { margin: 0; font-size: 14px; }
    .decision-route { margin: 5px 0 0; color: var(--muted); font-size: 11px; }
    .decision-impact { color: var(--green); font-size: 12px; font-weight: 850; white-space: nowrap; }
    .decision-badge { padding: 4px 7px; background: var(--blue-soft); color: var(--blue); font-size: 10px; font-weight: 850; text-transform: uppercase; }
    .decision-badge.rolled-out, .decision-badge.validated { background: var(--green-soft); color: var(--green); }
    .decision-badge.rejected { background: #fbe2e1; color: var(--red); }
    .decision-controls { display: grid; grid-template-columns: 150px 120px 155px minmax(180px, 1fr) auto auto; gap: 8px; align-items: end; margin-top: 12px; }
    .decision-controls input, .decision-controls select { margin-top: 4px; padding: 7px; font-size: 12px; }
    .decision-controls button { margin: 0; padding: 8px 10px; font-size: 11px; }
    .decision-controls .decision-delete { background: transparent; border: 1px solid var(--line); color: var(--red); }
    .decision-empty { padding: 18px 0; color: var(--muted); font-size: 13px; }
    @media (max-width: 980px) {
      .app-shell { display: block; }
      .sidebar { width: 100%; min-width: 0; max-width: 100vw; padding: 10px 16px; border-right: 0; border-bottom: 1px solid var(--line); overflow-x: auto; }
      .sidebar nav { display: flex; width: max-content; min-width: 0; gap: 4px; }
      .nav-label, .sidebar-status { display: none; }
      .nav-button { width: auto; min-width: 132px; border-left: 0; border-bottom: 3px solid transparent; text-align: center; }
      .nav-button.active { border-bottom-color: var(--green); }
      .nav-button span { display: none; }
    }
    @media (max-width: 820px) {
      .topbar { padding: 0 16px; } main { padding: 24px 16px 40px; }
      .heading { display: block; } .updated { margin-top: 12px; text-align: left; }
      .view-heading { display: block; } .view-heading .updated { margin-top: 8px; }
      .metrics, .guide-steps, .glossary-grid, .connector-grid, .tool-scan-results, .welcome-choices { grid-template-columns: repeat(2, minmax(0, 1fr)); } .grid { grid-template-columns: 1fr; }
      .import-controls { grid-template-columns: 1fr 1fr; } .import-controls label:first-child { grid-column: 1 / -1; }
      .preview-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .recommendation { grid-template-columns: 1fr; gap: 6px; } .savings { text-align: left; } .catalog-toolbar { grid-template-columns: 1fr 160px; } .catalog-count { grid-column: 1 / -1; text-align: left; padding: 0; } .scenario-row { grid-template-columns: 1fr auto; } .scenario-remove { grid-column: 2; }
      .manual-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .integration-row { grid-template-columns: 130px minmax(0, 1fr); } .integration-status { grid-column: 1; grid-row: 2; justify-self: start; }
      .ledger-ribbon { grid-template-columns: repeat(3, 1fr); } .ledger-title { grid-column: 1 / -1; border-right: 0; border-bottom: 1px solid #3a474e; }
      .fingerprint { grid-template-columns: 1fr; } .fingerprint-column { border-right: 0; border-bottom: 1px solid var(--line); }
      .decision-controls { grid-template-columns: repeat(2, minmax(0, 1fr)); } .decision-controls label:nth-child(4) { grid-column: 1 / -1; }
    }
    @media (max-width: 520px) {
      .workspace, .live { display: none; } .metrics, .form-grid, .catalog-toolbar, .guide-steps, .glossary-grid, .connector-grid, .tool-scan-results, .welcome-choices, .import-controls, .preview-stats { grid-template-columns: 1fr; }
      .import-controls label:first-child { grid-column: auto; }
      .sidebar { overflow-x: visible; }
      .sidebar nav { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }
      .nav-button { min-width: 0; padding: 9px 6px; font-size: 12px; }
      .topbar { gap: 8px; } .right { gap: 6px; } .export, .start-guide { padding: 7px 8px; font-size: 11px; }
      .start-guide-full { display: none; } .start-guide-compact { display: inline; }
      .manual-steps, .manual-row, .integration-row { grid-template-columns: 1fr; }
      .manual-row { gap: 6px; } .integration-status { grid-column: auto; grid-row: auto; justify-self: start; }
      .evidence-seal { width: 100%; margin-top: 14px; } .launch-strip { align-items: flex-start; } .launch-strip button { display: none; }
      .ledger-ribbon { grid-template-columns: 1fr; } .ledger-title, .ledger-stat { border-right: 0; border-bottom: 1px solid #3a474e; }
      .decision-head, .decision-controls { grid-template-columns: 1fr; } .decision-impact { white-space: normal; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand-lockup"><span class="brand-index">P</span><span class="brand-copy"><span class="brand">Poliora</span><small>AI cost operations</small></span><span class="workspace" id="workspace"></span></div>
    <div class="right"><span class="live">Local data</span><button class="start-guide" id="beginner-guide" type="button" data-view-target="welcome"><span class="start-guide-full">New to Poliora? Start here</span><span class="start-guide-compact">Start here</span></button><button class="export" id="report-link" type="button">Export report</button></div>
  </header>
  <div class="app-shell">
    <aside class="sidebar" aria-label="Primary navigation">
      <p class="nav-label">Start</p>
      <nav>
        <button class="nav-button active" type="button" data-view-target="welcome">Start here</button>
        <p class="nav-label nav-section">Understand</p>
        <button class="nav-button" type="button" data-view-target="overview">My AI activity</button>
        <button class="nav-button" type="button" data-view-target="connections">Add a tool or bill <span id="nav-connections-count">0</span></button>
        <p class="nav-label nav-section">Improve</p>
        <button class="nav-button" type="button" data-view-target="scenarios">Ways to save <span id="nav-scenarios-count">0</span></button>
        <button class="nav-button" type="button" data-view-target="models">Models &amp; prices <span id="nav-models-count">0</span></button>
      </nav>
      <section class="sidebar-status" aria-label="Workspace health">
        <h2>Workspace health</h2>
        <div class="status-line"><span>Usage</span><strong id="sidebar-usage">Loading</strong></div>
        <div class="status-line"><span>Rate coverage</span><strong id="sidebar-coverage">Loading</strong></div>
        <div class="status-line"><span>Evidence</span><strong id="sidebar-evidence">Loading</strong></div>
        <div class="status-line"><span>Setups</span><strong id="sidebar-setups">Loading</strong></div>
      </section>
      <p class="rail-motto">Proof before promises.</p>
    </aside>
    <main>
      <section class="view active" data-view="welcome">
        <div class="welcome-hero"><p class="view-kicker">A simple first step</p><h1>Let’s make your AI use easier to understand.</h1><p class="summary">You do not need to know models, tokens, or code to begin. Pick the sentence that sounds most like you. Poliora will show the next useful step and explain what it can, and cannot, see.</p></div>
        <div class="welcome-choices"><button class="welcome-choice" type="button" data-welcome-action="history"><span>I use Codex or Claude Code</span><strong>Show me the usage history already on this computer.</strong><p>First review the token, model, plan, and quota metadata Poliora can see. Nothing is saved until you approve it.</p></button><button class="welcome-choice" type="button" data-welcome-action="import"><span>I have a bill or export</span><strong>I want to understand spending I already have.</strong><p>Bring an approved CSV export. Poliora checks it before saving anything locally.</p></button><button class="welcome-choice" type="button" data-welcome-action="demo"><span>I am just exploring</span><strong>Show me an example before I connect anything.</strong><p>Load fictional sample data, then explore the dashboard without touching your real information.</p></button></div>
        <div class="welcome-boundary"><strong>Your choice comes first.</strong> Poliora does not read your prompts, source code, chats, passwords, or provider account history. It only handles the usage information you choose to connect or import.</div>
      </section>
      <section class="view" data-view="overview">
        <header class="view-heading"><div><p class="view-kicker">Your activity</p><h1>Your AI check-in</h1><p class="summary">See what Poliora can measure, find the biggest cost drivers, and keep estimates separate from proven savings.</p></div><div class="evidence-seal" id="evidence-seal"></div></header>
        <section class="launch-strip"><div><strong>Your next useful action</strong><p id="guide-status"></p></div><button id="open-guide" type="button">Open field guide</button></section>
        <div class="demo-action" id="demo-action"><button id="load-demo" type="button">Load guided sample data</button><span id="demo-message">Use fictional data to explore every dashboard control safely.</span></div>
        <section class="metrics" id="metrics"></section>
        <section class="ledger-ribbon" id="ledger-ribbon"></section>
        <section class="filters" aria-label="Reporting period"><button class="filter active" type="button" data-days="">All time</button><button class="filter" type="button" data-days="7">7 days</button><button class="filter" type="button" data-days="30">30 days</button></section>
        <section class="grid"><article class="panel"><h2>Cost fingerprint</h2><p class="panel-note">The shape of this workspace across providers, workflows, and models.</p><div class="fingerprint"><div class="fingerprint-column"><h3>Provider</h3><div id="fingerprint-providers"></div></div><div class="fingerprint-column"><h3>Workflow</h3><div id="fingerprint-workflows"></div></div><div class="fingerprint-column"><h3>Model</h3><div id="fingerprint-models"></div></div></div></article><article class="budget-box" id="budget"></article></section>
        <section class="grid"><article class="panel"><h2>Spend over time</h2><p class="panel-note">Daily tracked spend in the selected reporting period.</p><div id="trend"></div><div id="spend-watch"></div></article><article class="panel"><h2>Data quality</h2><p class="panel-note">Coverage indicates whether rate-based estimates are trustworthy.</p><div id="quality"></div></article></section>
        <section class="grid"><article class="panel"><h2>Cost drivers by model</h2><p class="panel-note">Prioritize the model routes carrying the most spend.</p><div id="drivers"></div></article><article class="panel"><h2>Spend by customer</h2><p class="panel-note">Find costly accounts and protect customer margins.</p><div id="customers"></div></article></section>
        <section class="panel recommendations"><h2>Recommended next moves</h2><div id="recommendations"></div></section>
      </section>

      <section class="view" data-view="connections">
        <header class="view-heading"><div><p class="view-kicker">Your choices</p><h1>Add a tool or a bill</h1><p class="summary">Choose what Poliora may observe, or import a bill you are allowed to use. Every option explains what it can provide before setup.</p></div></header>
        <section class="panel tool-scan"><div class="tool-scan-head"><div><h2>Find the AI usage already recorded here</h2><p>For Codex and Claude Code, Poliora can review local usage metadata already written by those tools: timestamps, token totals, model, plan, and quota. It never reads prompts, replies, code, credentials, or chats. Review the result before adding anything to your dashboard.</p></div><button id="detect-history" type="button">Review local history</button></div><div id="history-results" class="tool-scan-results" hidden></div></section>
        <section class="panel tool-scan"><div class="tool-scan-head"><div><h2>Check which other tools are ready</h2><p>This availability check only looks for supported launchers and the Poliora Antigravity workspace helper. It never opens a tool or reads account history.</p></div><button id="scan-tools" type="button">Check installed tools</button></div><div id="tool-scan-results" class="tool-scan-results" hidden></div></section>
        <section class="panel import-panel"><h2>Import existing usage</h2><p class="panel-note">Select a CSV to validate it locally before anything is written. Required information is model, input tokens, output tokens, and provider in the file or below.</p><div class="import-controls"><label>Usage CSV<input id="import-file" type="file" accept=".csv,text/csv"></label><label>Default provider<input id="import-provider" placeholder="openai"></label><label>Default project<input id="import-project" placeholder="Workspace project"></label></div><label class="check-line"><input id="import-skip-invalid" type="checkbox"> Import valid rows when some rows are rejected</label><div class="button-row"><button id="import-preview-button" type="button">Preview file</button><button class="secondary" id="import-commit" type="button" disabled>Import rows</button></div><div class="import-preview" id="import-preview" hidden></div></section>
        <section class="panel connection-center"><h2>Available data sources</h2><p class="panel-note">Poliora collects usage metadata and costs by default, never prompts, source code, or model replies. Approval here records consent; credentials are configured separately.</p><div id="connectors" class="connector-grid"></div></section>
      </section>

      <section class="view" data-view="scenarios">
        <header class="view-heading"><div><p class="view-kicker">Try an improvement</p><h1>Ways to save</h1><p class="summary">Compare a possible change, test that it still works, and only call it savings after you have evidence.</p></div></header>
        <section class="ledger-ribbon decision-summary" id="decision-ledger-summary"></section>
        <section class="panel simulator"><h2>Model a routing decision</h2><p class="panel-note">The estimate is a hypothesis. Track it, test representative output quality, then record measured savings only after rollout.</p><div class="form-grid"><label>Current model<select id="source"></select></label><label>Proposed model<select id="target"></select></label><label class="scenario-name">Decision name<input id="scenario-name" maxlength="120" placeholder="Support route trial"></label></div><div class="range-line"><label for="percentage">Traffic to move</label><input id="percentage" type="range" min="5" max="100" value="35"><output id="percentage-output">35%</output></div><div class="button-row"><button id="simulate" type="button">Calculate impact</button><button id="track-decision" type="button" disabled>Track decision</button><button class="secondary" id="save-scenario" type="button" disabled>Save estimate only</button></div><div class="simulation-result" id="simulation-result"></div></section>
        <section class="panel" style="margin-top:14px"><h2>Tracked decisions</h2><p class="panel-note">Update the test status, quality result, and measured monthly value. Poliora counts savings as realized only after rollout.</p><div id="decisions"></div></section>
        <details class="panel" style="margin-top:14px"><summary><strong>Saved estimates</strong></summary><p class="panel-note">Unmanaged calculations kept for reference. Promote important work through the tracked decision flow above.</p><div id="scenarios"></div></details>
      </section>

      <section class="view" data-view="models">
        <header class="view-heading"><div><p class="view-kicker">Details when you need them</p><h1>Models &amp; prices</h1><p class="summary">See the AI models in your data, check where prices came from, and add a private contract rate when it differs.</p></div></header>
        <section class="panel"><h2>Observed model spend</h2><p class="panel-note">Models found in the selected reporting period, including token volume and recorded cost.</p><div id="table"></div></section>
        <section class="panel" style="margin-top:14px"><h2>Model catalog</h2><p class="panel-note">Verified defaults plus models observed in your usage. Models without a verified public price remain visible and can use your contract rate.</p><div class="catalog-toolbar"><label>Find a model<input id="catalog-search" type="search" placeholder="Search model, provider, or capability"></label><label>Provider<select id="catalog-provider"></select></label><span class="catalog-count" id="catalog-count"></span></div><div id="catalog"></div></section>
      </section>

      <section class="view" data-view="guide">
        <header class="view-heading"><div><p class="view-kicker">Beginner guide</p><h1>Understand Poliora from first data to savings decision</h1><p class="summary">A practical guide to what each screen means, which integrations work today, and what Poliora can honestly measure.</p></div></header>
        <div class="manual-intro"><strong>Poliora is a local AI cost control room.</strong><p>It collects usage metadata such as provider, model, tokens, workflow, and cost. It does not need prompts, model replies, source code, or private transcripts. The dashboard stays on this computer unless you later choose a hosted product.</p></div>

        <section class="manual-section"><h2>Your first useful result</h2><p class="manual-lead">Do these four things in order. A savings number becomes credible only after the usage and price behind it are verified.</p><div class="manual-steps"><article class="manual-step"><span class="manual-number">1</span><strong>Collect usage</strong><p>Use a supported runtime wrapper, an agent hook, or preview and import a CSV.</p></article><article class="manual-step"><span class="manual-number">2</span><strong>Check the evidence</strong><p>Confirm the model names, reporting period, and model-rate coverage.</p></article><article class="manual-step"><span class="manual-number">3</span><strong>Model one change</strong><p>Use Scenarios to test moving a safe portion of one workflow to a cheaper model.</p></article><article class="manual-step"><span class="manual-number">4</span><strong>Measure before claiming</strong><p>Validate quality and real production cost, then export a client-ready report.</p></article></div></section>

        <section class="manual-section"><h2>What every dashboard area tells you</h2><p class="manual-lead">Start with Overview, investigate the evidence, then move into decisions. You do not need to understand every technical term on day one.</p><div class="manual-rows"><div class="manual-row"><strong>Overview</strong><p><b>Tracked spend</b> is the cost in the selected records. <b>Projected monthly spend</b> extends the observed daily run-rate to a month, so short periods have lower confidence. Cost drivers show where optimization work matters most.</p></div><div class="manual-row"><strong>Data quality</strong><p>Rate coverage is the percentage of requests whose model has a known public or private price. Subscription turns are counted but excluded from dollar spend. Unpriced models must receive a local rate before forecasts are complete.</p></div><div class="manual-row"><strong>Connections</strong><p>This is where usage enters Poliora. Preview CSV files before importing, review exactly what a connector may receive, and approve only the sources you intend to use.</p></div><div class="manual-row"><strong>Scenarios</strong><p>A scenario estimates the financial effect of routing some traffic to another model. It is not proof that the cheaper model preserves quality; test representative tasks before switching production traffic.</p></div><div class="manual-row"><strong>Models &amp; rates</strong><p>Search the catalog, inspect provider sources and lifecycle, and enter a private contract rate when it differs from the public default. Poliora never silently replaces your local rate.</p></div><div class="manual-row"><strong>Export report</strong><p>Create a standalone client or management report with your organization, client name, preparer, and accent color. Its disclosure separates tracked spend, projections, and modeled savings.</p></div></div></section>

        <section class="manual-section"><h2>Connect the AI tools you use</h2><p class="manual-lead">There are three integration levels: exact API response capture, supported product activity, and admin or CSV import. The status at right describes what is usable in this release.</p>
          <article class="integration-row"><div><h3>OpenAI Codex CLI</h3><p>Runs one Codex task through the documented JSON event stream.</p></div><div><p>Poliora records model and provider-reported token totals without saving your prompt, reply, commands, or file changes. ChatGPT subscription turns remain zero-dollar.</p><code class="command-line">poliora codex --model gpt-5.6-sol --sandbox read-only "Explain this repository"</code><div class="manual-note">If <b>codex --version</b> mentions Python or a comic archive, remove the unrelated package with <b>python -m pip uninstall codex</b>, then install OpenAI Codex with <b>npm.cmd install -g @openai/codex</b>.</div></div><span class="integration-status">Available now</span></article>
          <article class="integration-row"><div><h3>Google Antigravity</h3><p>Uses Antigravity's documented workspace plugin and lifecycle hook.</p></div><div><p>The current hook exposes invocation activity but not model names or token totals. Poliora therefore records activity without inventing spend.</p><code class="command-line">poliora antigravity-install</code></div><span class="integration-status">Available now</span></article>
          <article class="integration-row"><div><h3>Gemini API</h3><p>Wraps Google's official Python client.</p></div><div><p>The wrapper reads exact model, prompt, output, cache, and reasoning usage from the Gemini response metadata. Your API key remains in Google's client environment.</p><code class="command-line">client = track_gemini_client(genai.Client(), project="my-project")</code></div><span class="integration-status">Available now</span></article>
          <article class="integration-row"><div><h3>OpenAI API</h3><p>Wraps Responses or Chat Completions clients.</p></div><div><p>Use <b>track_openai_client</b> around your existing client. The normal SDK response is returned while token and cost metadata is appended locally.</p><code class="command-line">client = track_openai_client(client, project="my-project")</code></div><span class="integration-status">Available now</span></article>
          <article class="integration-row"><div><h3>Claude API</h3><p>Wraps Anthropic message calls.</p></div><div><p>Use <b>track_anthropic_client</b>. Poliora understands standard input, cache-read, cache-creation, and output token fields returned by Anthropic.</p><code class="command-line">client = track_anthropic_client(client, project="my-project")</code></div><span class="integration-status">Available now</span></article>
          <article class="integration-row"><div><h3>DeepSeek and gateways</h3><p>Uses an OpenAI-compatible client while preserving the correct provider label.</p></div><div><p>This works for compatible API workloads. Set the provider explicitly so reports and price lookup do not misclassify the request as OpenAI.</p><code class="command-line">client = track_openai_compatible_client(client, provider="deepseek")</code></div><span class="integration-status">Available now</span></article>
          <article class="integration-row"><div><h3>Cursor Team</h3><p>Team analytics requires administrator access.</p></div><div><p>Direct Cursor Admin API ingestion is not wired into this private-pilot build yet. Export usage to CSV and import it through Connections today; the supported admin adapter is a launch-follow-up.</p></div><span class="integration-status waiting">CSV now / adapter planned</span></article>
          <article class="integration-row"><div><h3>Claude Code</h3><p>Organization analytics requires an Anthropic Admin API key.</p></div><div><p>Direct Claude Code Analytics import is not wired into this build yet. Use an authorized organization CSV export, or track Anthropic API calls directly with the SDK wrapper.</p></div><span class="integration-status waiting">CSV now / adapter planned</span></article>
          <article class="integration-row"><div><h3>Any other AI tool</h3><p>Import a neutral usage CSV without changing application code.</p></div><div><p>Include provider, model, input tokens, output tokens, and optionally cached tokens, cost, workflow, customer, and timestamp. Preview reports every rejected row before writing anything.</p><code class="command-line">poliora import-csv usage.csv --preview</code></div><span class="integration-status">Available now</span></article>
        </section>

        <section class="manual-section"><h2>What Poliora will not claim</h2><p class="manual-lead">A trustworthy cost tool must be explicit about missing evidence.</p><div class="manual-rows"><div class="manual-row"><strong>Subscription cost</strong><p>Codex and Antigravity subscription activity is not converted into imaginary per-token API spend.</p></div><div class="manual-row"><strong>Quality equivalence</strong><p>A cheaper routing scenario is a hypothesis. Poliora does not claim two models produce equal quality without an evaluation.</p></div><div class="manual-row"><strong>Automatic access</strong><p>Approving a connector records local consent; it does not create provider credentials or bypass an administrator.</p></div><div class="manual-row"><strong>Cloud security</strong><p>The current dashboard binds to 127.0.0.1 for local use. It is not a multi-tenant website and should not be exposed publicly.</p></div></div></section>
      </section>
    </main>
  </div>
  <dialog id="rate-dialog"><form id="rate-form" class="dialog-body"><h2>Set local model rate</h2><p class="dialog-model" id="rate-model"></p><div class="form-grid"><label>Input USD per 1M<input id="rate-input" type="number" min="0" step="any" required></label><label>Output USD per 1M<input id="rate-output" type="number" min="0" step="any" required></label><label>Cache read USD per 1M<input id="rate-cache" type="number" min="0" step="any"></label><label>Rate note<input id="rate-note" maxlength="300" placeholder="Contract rate"></label></div><p class="form-error" id="rate-error" role="alert"></p><div class="dialog-actions"><button class="secondary" id="rate-cancel" type="button">Cancel</button><button id="rate-save" type="submit">Save rate</button></div></form></dialog>
  <dialog id="connector-dialog"><div class="dialog-body"><h2 id="connector-title"></h2><p class="dialog-model" id="connector-description"></p><div class="connector-info" id="connector-setup"></div><strong>Poliora will receive</strong><ul class="connector-list" id="connector-metrics"></ul><strong>Permission needed</strong><ul class="connector-list" id="connector-permissions"></ul><p class="form-error" id="connector-error" role="alert"></p><div class="dialog-actions"><button class="secondary" id="connector-cancel" type="button">Close</button><button id="connector-action" type="button"></button></div></div></dialog>
  <dialog id="report-dialog"><form id="report-form" class="dialog-body"><h2>Export client report</h2><p class="dialog-model">Create a standalone HTML report. These details appear only in the downloaded file.</p><div class="form-grid"><label>Brand or organization<input id="report-organization" maxlength="120" value="Poliora"></label><label>Client name<input id="report-client" maxlength="120" placeholder="Acme Corporation"></label><label>Prepared by<input id="report-prepared-by" maxlength="120" placeholder="Your name or team"></label><label>Accent color<input id="report-accent" type="color" value="#087c59"></label><label class="scenario-name">Custom report title<input id="report-title" maxlength="120" placeholder="Quarterly AI efficiency review"></label></div><p class="form-error" id="report-error" role="alert"></p><div class="dialog-actions"><button class="secondary" id="report-cancel" type="button">Cancel</button><button type="submit">Download report</button></div></form></dialog>
  <script>
    const money = value => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}).format(value || 0);
    let overview; let activeDays = ''; let lastSimulationPayload; let activeConnector; let pendingImport;
    const byId = id => document.getElementById(id);
    const viewTitles = {welcome: 'Start here', overview: 'My AI activity', connections: 'Add a tool or bill', scenarios: 'Ways to save', models: 'Models & prices', guide: 'Beginner guide'};
    function activateView(requestedView, updateLocation = true) {
      const view = Object.hasOwn(viewTitles, requestedView) ? requestedView : 'overview';
      document.querySelectorAll('[data-view]').forEach(item => item.classList.toggle('active', item.dataset.view === view));
      document.querySelectorAll('[data-view-target]').forEach(item => {
        const active = item.dataset.viewTarget === view;
        item.classList.toggle('active', active);
        item.setAttribute('aria-current', active ? 'page' : 'false');
      });
      document.title = viewTitles[view] + ' | Poliora';
      if (updateLocation && window.location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
      window.scrollTo({top: 0, behavior: 'auto'});
    }
    function renderBars(rows, emptyMessage) {
      return rows.length ? rows.slice(0, 6).map(row => `<div class="cost-row"><div class="cost-head"><span>${escapeHtml(row.name)}</span><strong>${money(row.cost_usd)}</strong></div><div class="bar"><i style="width:${Math.max(0, Math.min(100, row.share_pct))}%"></i></div></div>`).join('') : `<p class="empty">${emptyMessage}</p>`;
    }
    function renderTrend(rows) {
      if (!rows.length) return '<p class="empty">No daily spend recorded yet.</p>';
      const maximum = Math.max(...rows.map(row => row.cost_usd), 0.000001);
      return rows.map(row => `<div class="trend-row"><span>${escapeHtml(row.date)}</span><div class="bar"><i style="width:${(row.cost_usd / maximum) * 100}%"></i></div><strong>${money(row.cost_usd)}</strong></div>`).join('');
    }
    function ledgerMarkup(ledger) {
      return `<div class="ledger-title"><strong>Savings proof ledger</strong><p>${ledger.decisions.toLocaleString()} tracked decision${ledger.decisions === 1 ? '' : 's'} / ${ledger.validated.toLocaleString()} quality validated. Modeled value is never presented as money already saved.</p></div><div class="ledger-stat"><span>Modeled monthly</span><strong>${money(ledger.modeled_monthly_savings_usd)}</strong></div><div class="ledger-stat"><span>Active tests</span><strong>${ledger.active_tests.toLocaleString()}</strong></div><div class="ledger-stat"><span>Realized monthly</span><strong>${money(ledger.realized_monthly_savings_usd)}</strong></div>`;
    }
    function renderFingerprint(rows, emptyMessage) {
      if (!rows.length) return `<p class="empty">${emptyMessage}</p>`;
      return rows.slice(0, 4).map(row => `<div class="fingerprint-item"><div class="fingerprint-head"><span>${escapeHtml(row.name)}</span><strong>${row.share_pct.toFixed(0)}%</strong></div><div class="fingerprint-track"><i style="width:${Math.max(0, Math.min(100, row.share_pct))}%"></i></div></div>`).join('');
    }
    function render() {
      const report = overview.report;
      byId('workspace').textContent = '/ ' + overview.project;
      const evidence = overview.evidence;
      byId('evidence-seal').innerHTML = `<span class="evidence-grade">${escapeHtml(evidence.grade)}</span><span class="evidence-copy"><strong>${escapeHtml(evidence.label)} / ${evidence.score}%</strong><span>${escapeHtml(evidence.next_action)}</span></span>`;
      byId('guide-status').textContent = evidence.next_action;
      byId('demo-action').hidden = report.requests > 0;
      renderConnectors(overview.connectors || []);
      const connections = overview.connectors || [];
      const approvedConnections = connections.filter(item => item.connection).length;
      byId('nav-connections-count').textContent = approvedConnections + '/' + connections.length;
      byId('nav-scenarios-count').textContent = (overview.decisions || []).length.toLocaleString();
      byId('nav-models-count').textContent = overview.models.length.toLocaleString();
      byId('sidebar-usage').textContent = report.requests ? report.requests.toLocaleString() + ' requests' : 'No data yet';
      byId('sidebar-coverage').textContent = overview.data_quality.rate_coverage_pct.toFixed(1) + '%';
      byId('sidebar-evidence').textContent = evidence.grade + ' / ' + evidence.score + '%';
      byId('sidebar-setups').textContent = approvedConnections + ' of ' + connections.length + ' approved';
      byId('import-project').placeholder = overview.project;
      byId('report-client').placeholder = overview.project;
      const budget = report.monthly_budget_usd;
      const used = report.budget_used_pct === null ? 'Not set' : report.budget_used_pct.toFixed(1) + '%';
      const nonDollarRequests = overview.data_quality.non_dollar_requests || 0;
      const ledger = overview.savings_ledger;
      const cards = [
        ['30-day run rate', money(report.projected_monthly_usd), report.observed_days.toLocaleString() + ' observed day' + (report.observed_days === 1 ? '' : 's') + ' / ' + report.forecast_confidence + ' confidence'],
        ['Tracked spend', money(report.cost_usd), report.requests.toLocaleString() + ' requests' + (nonDollarRequests ? ' / ' + nonDollarRequests.toLocaleString() + ' subscription turns excluded' : '') + (report.tool_cost_usd ? ' / ' + money(report.tool_cost_usd) + ' tools' : '')],
        ['Realized monthly savings', money(ledger.realized_monthly_savings_usd), 'Only rolled-out decisions with measured value'],
        ['Total tokens', report.total_tokens.toLocaleString(), report.input_tokens.toLocaleString() + ' input / ' + report.output_tokens.toLocaleString() + ' output' + (report.cached_input_tokens ? ' / ' + report.cached_input_tokens.toLocaleString() + ' cached' : '')]
      ];
      byId('metrics').innerHTML = cards.map(card => `<article class="metric"><label>${card[0]}</label><strong>${card[1]}</strong><small>${card[2]}</small></article>`).join('');
      byId('ledger-ribbon').innerHTML = ledgerMarkup(ledger);
      byId('decision-ledger-summary').innerHTML = ledgerMarkup(ledger);
      byId('fingerprint-providers').innerHTML = renderFingerprint(report.by_provider, 'No provider usage.');
      byId('fingerprint-workflows').innerHTML = renderFingerprint(report.by_operation, 'No workflow labels.');
      byId('fingerprint-models').innerHTML = renderFingerprint(report.by_model, 'No models observed.');
      byId('drivers').innerHTML = renderBars(report.by_model, 'No tracked usage yet.');
      byId('budget').innerHTML = `<div><strong>${used}</strong><p>of monthly budget used</p><small>Budget remaining: ${budget === null ? 'Not set' : money(report.budget_delta_usd)}<br>Forecast confidence: ${escapeHtml(report.forecast_confidence)}<br>${escapeHtml(report.forecast_confidence_reason)}</small></div>`;
      byId('trend').innerHTML = renderTrend(report.daily_spend);
      const anomalies = report.spend_anomalies || [];
      byId('spend-watch').className = anomalies.length ? 'spend-watch alert' : 'spend-watch';
      byId('spend-watch').innerHTML = anomalies.length ? `<strong>${anomalies.length} material spend spike${anomalies.length === 1 ? '' : 's'} detected</strong>${anomalies.slice(0, 2).map(item => `${escapeHtml(item.date)}: ${money(item.cost_usd)} was ${item.increase_pct.toFixed(1)}% above its prior daily baseline.`).join('<br>')}` : '<strong>Spend watch is clear</strong>No material daily increase is visible once enough historical data exists.';
      byId('customers').innerHTML = renderBars(report.by_user, 'No customer labels recorded yet.');
      const quality = overview.data_quality;
      const missingModels = quality.unpriced_models.length ? quality.unpriced_models.map(escapeHtml).join(', ') : 'None';
      byId('quality').innerHTML = `<div class="quality-row"><span>Model-rate coverage</span><strong>${quality.rate_coverage_pct.toFixed(1)}%</strong></div><div class="quality-row"><span>Priced requests</span><strong>${quality.priced_requests.toLocaleString()}</strong></div><div class="quality-row"><span>Subscription turns excluded from spend</span><strong>${quality.non_dollar_requests.toLocaleString()}</strong></div><div class="quality-row"><span>Trace coverage</span><strong>${quality.trace_coverage_pct.toFixed(1)}%</strong></div><div class="quality-row"><span>Last recorded usage</span><strong>${quality.last_event_at ? escapeHtml(new Date(quality.last_event_at).toLocaleString()) : 'None'}</strong></div><div class="quality-row"><span>Unpriced models</span><strong>${missingModels}</strong></div><div class="quality-row"><span>Catalog entries</span><strong>${overview.catalog_health.catalog_models.toLocaleString()}</strong></div>`;
      byId('recommendations').innerHTML = overview.recommendations.map(item => `<article class="recommendation"><div class="tag">${escapeHtml(item.priority)} priority</div><div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.action)}</p></div><div class="savings">${money(item.estimated_monthly_savings_usd)} / mo</div></article>`).join('');
      renderDecisions(overview.decisions || []);
      renderScenarios(overview.scenarios || []);
      byId('table').innerHTML = report.by_model.length ? `<table><thead><tr><th>Model</th><th>Requests</th><th>Tokens</th><th>Cost</th><th>Share</th></tr></thead><tbody>${report.by_model.map(row => `<tr><td>${escapeHtml(row.name)}</td><td>${row.requests.toLocaleString()}</td><td>${row.total_tokens.toLocaleString()}</td><td>${money(row.cost_usd)}</td><td>${row.share_pct.toFixed(1)}%</td></tr>`).join('')}</tbody></table>` : '<p class="empty">Import a CSV or add the SDK to begin tracking.</p>';
      const optionFor = item => `<option value="${item.provider}|${item.model}">${escapeHtml(item.provider + ' / ' + item.model)}</option>`;
      const options = overview.models.map(optionFor).join('');
      const pricedOptions = overview.models.filter(item => item.priced).map(optionFor).join('');
      byId('source').innerHTML = options; byId('target').innerHTML = pricedOptions;
      if (report.by_model.length) byId('source').value = report.by_model[0].name.replace('/', '|');
      const inexpensive = overview.models.find(item => item.priced && (item.model.includes('mini') || item.model.includes('flash')));
      if (inexpensive) byId('target').value = inexpensive.provider + '|' + inexpensive.model;
      const providers = [...new Set(overview.models.map(item => item.provider))].sort();
      const providerSelect = byId('catalog-provider'); const priorProvider = providerSelect.value;
      providerSelect.innerHTML = '<option value="">All providers</option>' + providers.map(provider => `<option value="${escapeHtml(provider)}">${escapeHtml(provider)}</option>`).join('');
      providerSelect.value = providers.includes(priorProvider) ? priorProvider : '';
      renderCatalog();
    }
    function renderCatalog() {
      if (!overview) return;
      const query = byId('catalog-search').value.trim().toLowerCase();
      const provider = byId('catalog-provider').value;
      const filtered = overview.models.filter(item => {
        const searchable = [item.provider, item.model, item.display_name, item.status, ...(item.capabilities || [])].join(' ').toLowerCase();
        return (!provider || item.provider === provider) && (!query || searchable.includes(query));
      });
      byId('catalog-count').textContent = filtered.length + ' of ' + overview.models.length + ' models';
      if (!filtered.length) { byId('catalog').innerHTML = '<p class="empty">No models match this filter.</p>'; return; }
      byId('catalog').innerHTML = `<table><thead><tr><th>Provider</th><th>Model</th><th>Lifecycle</th><th>Pricing per 1M</th><th>Provenance</th></tr></thead><tbody>${filtered.map(item => {
        const capabilities = (item.capabilities || []).map(capability => `<span class="capability">${escapeHtml(capability)}</span>`).join('');
        const cacheRate = item.cached_input_per_1m === null || item.cached_input_per_1m === undefined ? '' : '<br><span class="model-meta">Cache read ' + money(item.cached_input_per_1m) + '</span>';
        const editRate = `<button class="rate-button" type="button" data-edit-rate data-provider="${escapeHtml(item.provider)}" data-model="${escapeHtml(item.model)}">Edit rate</button>`;
        const pricing = item.priced ? money(item.input_per_1m) + ' in / ' + money(item.output_per_1m) + ' out' + cacheRate + '<br>' + editRate : 'Needs rate<br>' + editRate;
        const source = typeof item.source_url === 'string' && item.source_url.startsWith('https://') ? `<a class="source" href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">Source</a>` : 'Local or observed';
        return `<tr><td>${escapeHtml(item.provider)}</td><td><strong>${escapeHtml(item.display_name)}</strong><br><span class="model-meta">${escapeHtml(item.model)}</span><br>${capabilities}</td><td>${escapeHtml(item.status)}</td><td>${pricing}</td><td>${source}<br><span class="model-meta">${escapeHtml(item.verified_at)}</span></td></tr>`;
      }).join('')}</tbody></table>`;
    }
    function connectorLabel(connector) {
      if (connector.connection) return ['Setup approved', 'enabled'];
      if (connector.availability === 'ready') return ['Ready to set up', ''];
      if (connector.availability === 'credential-required') return ['Admin credential needed', 'waiting'];
      return ['Official surface needed', 'waiting'];
    }
    function renderConnectors(connectors) {
      byId('connectors').innerHTML = connectors.map(connector => {
        const [label, style] = connectorLabel(connector);
        return `<article class="connector-card"><span class="connector-kind">${escapeHtml(connector.category)}</span><span class="connector-state ${style}">${escapeHtml(label)}</span><h3>${escapeHtml(connector.name)}</h3><p>${escapeHtml(connector.description)}</p><button type="button" data-open-connector="${escapeHtml(connector.id)}">${connector.connection ? 'Manage connection' : 'Review access'}</button></article>`;
      }).join('');
    }
    function renderToolScan(tools, notice) {
      const results = byId('tool-scan-results');
      results.hidden = false;
      results.innerHTML = `<p class="panel-note" style="grid-column:1/-1;margin:0">${escapeHtml(notice)}</p>` + tools.map(tool => `<article class="tool-scan-result ${tool.detected ? 'detected' : ''}"><strong>${escapeHtml(tool.name)}</strong><span class="scan-state">${tool.detected ? 'Available to review' : 'Not found'}</span><p>${escapeHtml(tool.detail)}</p><p><strong>Next:</strong> ${escapeHtml(tool.next_step)}</p><p>${escapeHtml(tool.history_note)}</p></article>`).join('');
    }
    function renderHistoryDetection(data) {
      const results = byId('history-results');
      const scans = data.scans || [];
      const available = scans.filter(scan => scan.available);
      const cards = available.map(scan => {
        const plan = scan.plan && scan.plan.plan_type ? scan.plan.plan_type : 'plan not recorded';
        const quota = scan.plan && scan.plan.quota_used_pct !== null && scan.plan.quota_used_pct !== undefined ? `<p><strong>Quota:</strong> ${scan.plan.quota_used_pct.toFixed(1)}% used</p>` : '';
        return `<article class="tool-scan-result detected"><strong>${escapeHtml(scan.display_name)}</strong><span class="scan-state">${scan.requests.toLocaleString()} requests found</span><p><strong>Plan:</strong> ${escapeHtml(plan)}<br><strong>Tokens:</strong> ${scan.total_tokens.toLocaleString()}<br><strong>Equivalent API value:</strong> ${money(scan.equivalent_api_cost_usd)}</p>${quota}</article>`;
      }).join('');
      const action = data.requests && !data.imported_events ? `<p class="panel-note" style="grid-column:1/-1;margin:0"><button id="import-detected-history" type="button">Add ${data.requests.toLocaleString()} metadata-only records to my dashboard</button></p>` : '';
      const imported = data.imported_events ? `<p class="panel-note" style="grid-column:1/-1;margin:0"><strong>${data.imported_events.toLocaleString()} new records added.</strong> Your overview now reflects the local history you approved.</p>` : '';
      results.hidden = false;
      results.innerHTML = `<p class="panel-note" style="grid-column:1/-1;margin:0">${escapeHtml(data.notice || '')}</p>` + (cards || '<p class="panel-note" style="grid-column:1/-1;margin:0">No supported Codex or Claude Code history was found yet. You can still import an authorized CSV or use the guided sample data.</p>') + action + imported;
    }
    async function detectHistory(importHistory = false) {
      const button = byId('detect-history'); const results = byId('history-results');
      button.disabled = true; button.textContent = importHistory ? 'Adding...' : 'Reading local history...'; results.hidden = true;
      try {
        const response = await fetch('/api/detect-history', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({import: importHistory})});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not read local history.');
        renderHistoryDetection(data);
        if (importHistory) await loadOverview(activeDays);
      } catch (error) {
        results.hidden = false; results.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
      } finally { button.disabled = false; button.textContent = 'Review local history'; }
    }
    function renderScenarios(scenarios) {
      if (!scenarios.length) { byId('scenarios').innerHTML = '<p class="empty">No saved routing scenarios yet.</p>'; return; }
      byId('scenarios').innerHTML = scenarios.map(item => {
        const result = item.result || {};
        const route = (result.source_provider || 'Unknown') + ' / ' + (result.source_model || 'Unknown') + ' to ' + (result.target_provider || 'Unknown') + ' / ' + (result.target_model || 'Unknown');
        return `<article class="scenario-row"><div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(route)} | ${Number(result.percentage || 0).toFixed(0)}% traffic | saved ${escapeHtml(new Date(item.created_at).toLocaleString())}</p></div><div class="scenario-value">${money(result.estimated_monthly_savings_usd)} / mo</div><button class="scenario-remove" type="button" data-remove-scenario="${escapeHtml(item.id)}">Remove</button></article>`;
      }).join('');
    }
    function renderDecisions(decisions) {
      if (!decisions.length) {
        byId('decisions').innerHTML = '<p class="decision-empty">No decisions are being tracked. Calculate an impact above, then choose Track decision.</p>';
        return;
      }
      const statusOptions = ['proposed', 'testing', 'validated', 'rolled-out', 'rejected'];
      const qualityOptions = ['pending', 'pass', 'fail'];
      byId('decisions').innerHTML = `<div class="decision-list">${decisions.map(item => {
        const route = item.source_provider + ' / ' + item.source_model + ' to ' + item.target_provider + ' / ' + item.target_model;
        const measured = item.measured_monthly_savings_usd === null ? '' : item.measured_monthly_savings_usd;
        const statuses = statusOptions.map(status => `<option value="${status}"${status === item.status ? ' selected' : ''}>${status.replace('-', ' ')}</option>`).join('');
        const qualities = qualityOptions.map(status => `<option value="${status}"${status === item.quality_status ? ' selected' : ''}>${status}</option>`).join('');
        return `<article class="decision-row" data-decision-row="${escapeHtml(item.id)}"><div class="decision-head"><div><h3>${escapeHtml(item.name)}</h3><p class="decision-route">${escapeHtml(route)} / ${Number(item.traffic_percentage).toFixed(0)}% traffic</p></div><div class="decision-impact">${money(item.estimated_monthly_savings_usd)} modeled / ${item.measured_monthly_savings_usd === null ? 'not measured' : money(item.measured_monthly_savings_usd) + ' measured'}</div><span class="decision-badge ${escapeHtml(item.status)}">${escapeHtml(item.status.replace('-', ' '))}</span></div><div class="decision-controls"><label>Decision state<select data-decision-status>${statuses}</select></label><label>Quality result<select data-decision-quality>${qualities}</select></label><label>Measured USD / month<input data-decision-measured type="number" min="0" step="0.01" value="${escapeHtml(measured)}" placeholder="After rollout"></label><label>Evidence note<input data-decision-notes maxlength="600" value="${escapeHtml(item.notes)}" placeholder="Test set, owner, result, or rollout date"></label><button type="button" data-update-decision="${escapeHtml(item.id)}">Update</button><button class="decision-delete" type="button" data-remove-decision="${escapeHtml(item.id)}">Delete</button></div><p class="form-error" data-decision-error></p></article>`;
      }).join('')}</div>`;
    }
    function escapeHtml(value) { const node = document.createElement('span'); node.textContent = value; return node.innerHTML; }
    function importPayload() {
      return {filename: pendingImport.filename, csv_text: pendingImport.csvText, provider: byId('import-provider').value, project: byId('import-project').value};
    }
    function updateImportCommit() {
      const preview = pendingImport && pendingImport.preview;
      byId('import-commit').disabled = !preview || !preview.valid_rows || (preview.invalid_rows > 0 && !byId('import-skip-invalid').checked);
    }
    function renderImportPreview(preview) {
      const box = byId('import-preview');
      const issues = (preview.issues || []).slice(0, 12);
      const columns = Object.entries(preview.mapped_columns || {}).map(([field, column]) => escapeHtml(field) + ' &larr; ' + escapeHtml(column)).join(', ') || 'None';
      const unpriced = (preview.unpriced_models || []).map(escapeHtml).join(', ') || 'None';
      box.hidden = false;
      box.innerHTML = `<div class="preview-stats"><div class="preview-stat"><span>Total rows</span><strong>${preview.total_rows.toLocaleString()}</strong></div><div class="preview-stat"><span>Valid</span><strong>${preview.valid_rows.toLocaleString()}</strong></div><div class="preview-stat"><span>Rejected</span><strong>${preview.invalid_rows.toLocaleString()}</strong></div><div class="preview-stat"><span>Estimated cost</span><strong>${money(preview.estimated_cost_usd)}</strong></div></div><p class="preview-detail"><strong>Recognized:</strong> ${columns}<br><strong>Models needing a rate:</strong> ${unpriced}</p>${issues.length ? `<ol class="issue-list">${issues.map(issue => `<li>${escapeHtml(issue.message)}</li>`).join('')}</ol>` : '<p class="preview-detail">Every row passed validation. Review the totals, then import.</p>'}`;
      updateImportCommit();
    }
    document.querySelectorAll('[data-view-target]').forEach(button => button.addEventListener('click', () => activateView(button.dataset.viewTarget)));
    window.addEventListener('hashchange', () => activateView(window.location.hash.slice(1), false));
    byId('open-guide').addEventListener('click', () => activateView('guide'));
    byId('report-link').addEventListener('click', () => {
      byId('report-error').textContent = '';
      byId('report-dialog').showModal();
    });
    byId('report-cancel').addEventListener('click', () => byId('report-dialog').close());
    byId('report-form').addEventListener('submit', event => {
      event.preventDefault();
      const params = new URLSearchParams();
      if (activeDays) params.set('since_days', activeDays);
      const fields = {organization: 'report-organization', client: 'report-client', prepared_by: 'report-prepared-by', title: 'report-title', accent: 'report-accent'};
      Object.entries(fields).forEach(([name, id]) => { const value = byId(id).value.trim(); if (value) params.set(name, value); });
      byId('report-dialog').close();
      window.location.assign('/report.html?' + params.toString());
    });
    byId('import-skip-invalid').addEventListener('change', updateImportCommit);
    byId('import-file').addEventListener('change', () => {
      pendingImport = undefined;
      byId('import-preview').hidden = true;
      byId('import-commit').disabled = true;
    });
    byId('import-preview-button').addEventListener('click', async () => {
      const file = byId('import-file').files[0]; const button = byId('import-preview-button'); const box = byId('import-preview');
      if (!file) { box.hidden = false; box.innerHTML = '<p class="error">Choose a CSV file first.</p>'; return; }
      if (file.size > 1400000) { box.hidden = false; box.innerHTML = '<p class="error">CSV must be smaller than 1.4 MB for browser import.</p>'; return; }
      button.disabled = true; box.hidden = false; box.textContent = 'Validating every row locally...';
      try {
        pendingImport = {filename: file.name, csvText: await file.text()};
        const response = await fetch('/api/import/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(importPayload())});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not preview this CSV.');
        pendingImport.preview = data.preview; renderImportPreview(data.preview);
      } catch (error) { pendingImport = undefined; box.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`; } finally { button.disabled = false; }
    });
    byId('import-commit').addEventListener('click', async () => {
      if (!pendingImport) return;
      const button = byId('import-commit'); const box = byId('import-preview'); button.disabled = true; button.textContent = 'Importing...';
      try {
        const payload = {...importPayload(), skip_invalid: byId('import-skip-invalid').checked};
        const response = await fetch('/api/import', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not import this CSV.');
        box.innerHTML = `<p><strong>${data.result.imported_events.toLocaleString()} rows imported.</strong> ${data.result.skipped_rows ? data.result.skipped_rows.toLocaleString() + ' invalid rows were skipped.' : 'No rows were skipped.'}</p>`;
        pendingImport = undefined; byId('import-file').value = ''; await loadOverview(activeDays);
      } catch (error) { box.innerHTML += `<p class="error">${escapeHtml(error.message)}</p>`; } finally { button.textContent = 'Import rows'; updateImportCommit(); }
    });
    byId('catalog-search').addEventListener('input', renderCatalog);
    byId('catalog-provider').addEventListener('change', renderCatalog);
    byId('connectors').addEventListener('click', event => {
      const button = event.target.closest('[data-open-connector]');
      if (!button) return;
      const connector = overview.connectors.find(item => item.id === button.dataset.openConnector);
      if (!connector) return;
      activeConnector = connector;
      byId('connector-title').textContent = connector.name;
      byId('connector-description').textContent = connector.description;
      byId('connector-setup').textContent = connector.setup_hint;
      byId('connector-metrics').innerHTML = connector.metrics.map(item => `<li>${escapeHtml(item)}</li>`).join('');
      byId('connector-permissions').innerHTML = connector.permissions.map(item => `<li>${escapeHtml(item)}</li>`).join('');
      byId('connector-error').textContent = '';
      const action = byId('connector-action');
      const unavailable = connector.availability === 'official-surface-needed';
      action.hidden = unavailable;
      action.textContent = connector.connection ? 'Remove setup approval' : 'Approve local setup';
      byId('connector-dialog').showModal();
    });
    byId('connector-cancel').addEventListener('click', () => byId('connector-dialog').close());
    byId('connector-action').addEventListener('click', async () => {
      if (!activeConnector) return;
      const action = byId('connector-action'); const error = byId('connector-error');
      const endpoint = `/api/connectors/${encodeURIComponent(activeConnector.id)}/${activeConnector.connection ? 'disconnect' : 'consent'}`;
      action.disabled = true; error.textContent = '';
      try {
        const response = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not update connection.');
        byId('connector-dialog').close(); await loadOverview(activeDays);
      } catch (problem) { error.textContent = problem.message; } finally { action.disabled = false; }
    });
    byId('scan-tools').addEventListener('click', async () => {
      const button = byId('scan-tools'); const results = byId('tool-scan-results');
      button.disabled = true; button.textContent = 'Scanning...'; results.hidden = true;
      try {
        const response = await fetch('/api/detect-tools', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not scan this computer.');
        renderToolScan(data.tools || [], data.notice || 'Scan completed.');
      } catch (error) {
        results.hidden = false; results.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
      } finally { button.disabled = false; button.textContent = 'Scan this computer'; }
    });
    byId('detect-history').addEventListener('click', () => detectHistory());
    byId('history-results').addEventListener('click', event => {
      if (event.target.closest('#import-detected-history')) detectHistory(true);
    });
    byId('load-demo').addEventListener('click', async () => {
      const button = byId('load-demo'); const message = byId('demo-message');
      button.disabled = true; message.textContent = 'Loading fictional sample data...';
      try {
        const response = await fetch('/api/demo-data', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not load sample data.');
        message.textContent = data.imported_events + ' sample requests loaded.'; await loadOverview();
      } catch (error) { message.textContent = error.message; button.disabled = false; }
    });
    byId('catalog').addEventListener('click', event => {
      const button = event.target.closest('[data-edit-rate]');
      if (!button) return;
      const model = overview.models.find(item => item.provider === button.dataset.provider && item.model === button.dataset.model);
      if (!model) return;
      byId('rate-model').textContent = model.provider + ' / ' + model.display_name;
      byId('rate-input').value = model.input_per_1m ?? '';
      byId('rate-output').value = model.output_per_1m ?? '';
      byId('rate-cache').value = model.cached_input_per_1m ?? '';
      byId('rate-note').value = model.price_note || '';
      byId('rate-error').textContent = '';
      byId('rate-form').dataset.provider = model.provider;
      byId('rate-form').dataset.model = model.model;
      byId('rate-dialog').showModal();
    });
    byId('rate-cancel').addEventListener('click', () => byId('rate-dialog').close());
    byId('rate-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget; const save = byId('rate-save'); const errorBox = byId('rate-error');
      const payload = {provider: form.dataset.provider, model: form.dataset.model, input_per_1m: Number(byId('rate-input').value), output_per_1m: Number(byId('rate-output').value), cached_input_per_1m: byId('rate-cache').value, note: byId('rate-note').value};
      save.disabled = true; errorBox.textContent = '';
      try {
        const response = await fetch('/api/pricing', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not save this rate.');
        byId('rate-dialog').close(); await loadOverview(activeDays);
      } catch (problem) { errorBox.textContent = problem.message; } finally { save.disabled = false; }
    });
    byId('percentage').addEventListener('input', event => byId('percentage-output').textContent = event.target.value + '%');
    byId('simulate').addEventListener('click', async () => {
      const [source_provider, source_model] = byId('source').value.split('|');
      const [target_provider, target_model] = byId('target').value.split('|');
      lastSimulationPayload = {source_provider, source_model, target_provider, target_model, percentage: Number(byId('percentage').value)};
      byId('save-scenario').disabled = true;
      byId('track-decision').disabled = true;
      const resultBox = byId('simulation-result'); resultBox.style.display = 'block'; resultBox.textContent = 'Calculating...';
      try {
        const response = await fetch('/api/simulate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(lastSimulationPayload)});
        const data = await response.json(); if (!response.ok) throw new Error(data.error);
        resultBox.innerHTML = `<strong>${money(data.estimated_monthly_savings_usd)} estimated monthly savings</strong><p>Moving ${data.percentage}% of ${escapeHtml(data.source_provider + ' / ' + data.source_model)} to ${escapeHtml(data.target_provider + ' / ' + data.target_model)} changes the observed cost by ${data.estimated_savings_pct.toFixed(1)}%.</p>`;
        byId('save-scenario').disabled = false;
        byId('track-decision').disabled = false;
      } catch (error) { resultBox.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`; }
    });
    byId('track-decision').addEventListener('click', async () => {
      if (!lastSimulationPayload) return;
      const button = byId('track-decision'); button.disabled = true;
      try {
        const response = await fetch('/api/decisions', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...lastSimulationPayload, name: byId('scenario-name').value})});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not track this decision.');
        byId('scenario-name').value = '';
        byId('simulation-result').innerHTML += '<p><strong>Decision added to the proof ledger.</strong> Move it to testing when the evaluation begins.</p>';
        await loadOverview(activeDays);
      } catch (error) {
        byId('simulation-result').innerHTML += `<p class="error">${escapeHtml(error.message)}</p>`;
        button.disabled = false;
      }
    });
    byId('save-scenario').addEventListener('click', async () => {
      if (!lastSimulationPayload) return;
      const button = byId('save-scenario'); button.disabled = true;
      try {
        const response = await fetch('/api/scenarios', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...lastSimulationPayload, name: byId('scenario-name').value})});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not save scenario.');
        byId('scenario-name').value = ''; await loadOverview(activeDays);
      } catch (error) { byId('simulation-result').innerHTML += `<p class="error">${escapeHtml(error.message)}</p>`; } finally { button.disabled = false; }
    });
    byId('scenarios').addEventListener('click', async event => {
      const button = event.target.closest('[data-remove-scenario]');
      if (!button) return;
      const response = await fetch('/api/scenarios/' + encodeURIComponent(button.dataset.removeScenario), {method: 'DELETE'});
      if (response.ok) await loadOverview(activeDays);
    });
    byId('decisions').addEventListener('click', async event => {
      const updateButton = event.target.closest('[data-update-decision]');
      const deleteButton = event.target.closest('[data-remove-decision]');
      const button = updateButton || deleteButton;
      if (!button) return;
      const decisionId = updateButton ? updateButton.dataset.updateDecision : deleteButton.dataset.removeDecision;
      const row = button.closest('[data-decision-row]');
      const errorBox = row.querySelector('[data-decision-error]');
      button.disabled = true; errorBox.textContent = '';
      try {
        const endpoint = '/api/decisions/' + encodeURIComponent(decisionId);
        let response;
        if (deleteButton) {
          response = await fetch(endpoint, {method: 'DELETE'});
        } else {
          const measuredValue = row.querySelector('[data-decision-measured]').value.trim();
          const payload = {
            status: row.querySelector('[data-decision-status]').value,
            quality_status: row.querySelector('[data-decision-quality]').value,
            measured_monthly_savings_usd: measuredValue === '' ? null : Number(measuredValue),
            notes: row.querySelector('[data-decision-notes]').value
          };
          response = await fetch(endpoint, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        }
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not update this decision.');
        await loadOverview(activeDays);
      } catch (error) {
        errorBox.textContent = error.message;
        button.disabled = false;
      }
    });
    async function loadOverview(days = activeDays) {
      activeDays = days;
      const suffix = days ? '?since_days=' + encodeURIComponent(days) : '';
      const response = await fetch('/api/overview' + suffix);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not load Poliora data.');
      overview = data; render();
    }
    document.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', async event => {
      document.querySelectorAll('[data-days]').forEach(item => item.classList.remove('active'));
      event.currentTarget.classList.add('active');
      try { await loadOverview(event.currentTarget.dataset.days); } catch (error) { alert(error.message); }
    }));
    document.querySelectorAll('[data-welcome-action]').forEach(button => button.addEventListener('click', () => {
      const action = button.dataset.welcomeAction;
      if (action === 'history') { activateView('connections'); setTimeout(() => byId('detect-history').click(), 0); return; }
      if (action === 'import') { activateView('connections'); setTimeout(() => byId('import-file').focus(), 0); return; }
      activateView('overview');
      if (byId('demo-action').hidden) {
        byId('demo-message').textContent = 'This workspace already has data. Showing your current activity instead.';
        return;
      }
      byId('load-demo').click();
    }));
    async function initializeDashboard() {
      activateView(window.location.hash.slice(1) || 'welcome', false);
      await loadOverview();
      const isDesktopFirstRun = new URLSearchParams(window.location.search).get('desktop-first-run') === '1';
      if (isDesktopFirstRun && overview.report.requests === 0) {
        activateView('connections');
        setTimeout(() => detectHistory(), 0);
      }
    }
    initializeDashboard().catch(error => { document.body.innerHTML = '<main><p class="error">Could not load Poliora data: ' + escapeHtml(error.message) + '</p></main>'; });
  </script>
</body>
</html>"""


def _usage_identity(event: UsageEvent) -> tuple[object, ...]:
    """Identify a locally detected turn so repeated review never duplicates it."""
    return (
        event.provider,
        event.model,
        event.input_tokens,
        event.output_tokens,
        event.cached_input_tokens,
        event.reasoning_tokens,
        event.operation,
        event.trace_id,
        event.timestamp,
        event.metadata.get("source"),
    )


def _catalog_rows(
    catalog: ModelCatalog,
    registry: PricingRegistry,
    events: list[UsageEvent],
) -> list[dict[str, object]]:
    """Merge catalog metadata, workspace pricing, and models seen in usage."""
    rows = {f"{item['provider']}:{item['model']}": dict(item) for item in catalog.to_list()}
    for event in events:
        key = f"{event.provider.strip().lower()}:{event.model.strip().lower()}"
        rows.setdefault(
            key,
            {
                "provider": event.provider,
                "model": event.model,
                "display_name": event.model,
                "status": "observed",
                "capabilities": ["unknown"],
                "context_window": None,
                "source_url": "",
                "verified_at": "not cataloged",
                "note": "Observed in local usage data.",
            },
        )
    for item in rows.values():
        pricing = registry.get(str(item["provider"]), str(item["model"]))
        item["priced"] = pricing is not None
        item["input_per_1m"] = pricing.input_per_1m if pricing else None
        item["output_per_1m"] = pricing.output_per_1m if pricing else None
        item["cached_input_per_1m"] = pricing.cached_input_per_1m if pricing else None
        item["price_note"] = pricing.note if pricing else ""
    return sorted(rows.values(), key=lambda item: (str(item["provider"]), str(item["display_name"])))


def _connector_rows(workspace) -> list[dict[str, object]]:
    connections = {item.connector_id: item.to_dict() for item in ConnectorStore(workspace.connectors_path).read_all()}
    return [{**item.to_dict(), "connection": connections.get(item.id)} for item in connector_catalog()]


def _connector_action(path: str) -> tuple[str, str] | None:
    prefix = "/api/connectors/"
    if not path.startswith(prefix):
        return None
    parts = path.removeprefix(prefix).split("/")
    if len(parts) != 2 or parts[1] not in {"consent", "disconnect"}:
        return None
    return parts[0], parts[1]


def _resource_id(path: str, prefix: str) -> str | None:
    if not path.startswith(prefix):
        return None
    resource_id = path.removeprefix(prefix).strip()
    if not resource_id or "/" in resource_id:
        return None
    return resource_id


def _evidence_grade(report, quality: dict[str, object], decisions: list[SavingsDecision]) -> dict[str, object]:
    """Score whether the workspace can support a defensible savings claim."""
    if not report.requests:
        return {
            "score": 0,
            "grade": "E",
            "label": "No evidence",
            "next_action": "Collect or import usage before making a cost claim.",
        }
    rate_coverage = float(quality["rate_coverage_pct"])
    trace_coverage = float(quality["trace_coverage_pct"])
    validated = any(item.status in {"validated", "rolled-out"} for item in decisions)
    testing = any(item.status == "testing" for item in decisions)
    score = round(
        20
        + min(report.observed_days / 14, 1) * 20
        + rate_coverage / 100 * 30
        + trace_coverage / 100 * 10
        + (20 if validated else 10 if testing else 0)
    )
    if score >= 85:
        grade, label = "A", "Decision ready"
    elif score >= 70:
        grade, label = "B", "Strong evidence"
    elif score >= 50:
        grade, label = "C", "Directional"
    elif score >= 30:
        grade, label = "D", "Early signal"
    else:
        grade, label = "E", "Weak evidence"

    if rate_coverage < 100:
        next_action = "Price the unpriced models before relying on savings estimates."
    elif report.observed_days < 7:
        next_action = "Collect at least seven days of usage to stabilize the monthly projection."
    elif not (validated or testing):
        next_action = "Move one routing scenario into testing and record its quality result."
    elif not validated:
        next_action = "Complete the active quality test before approving a rollout."
    else:
        next_action = "Record measured savings after rollout to close the evidence loop."
    return {"score": score, "grade": grade, "label": label, "next_action": next_action}


def _report_branding(query: dict[str, list[str]]) -> ReportBranding:
    """Build validated report identity from local dashboard query fields."""
    return ReportBranding(
        organization=_query_text(query, "organization") or "Poliora",
        client=_query_text(query, "client"),
        prepared_by=_query_text(query, "prepared_by"),
        title=_query_text(query, "title"),
        accent_color=_query_text(query, "accent") or "#087c59",
    )


def _query_text(query: dict[str, list[str]], field: str) -> str:
    values = query.get(field, [])
    value = values[0].strip() if values else ""
    if len(value) > 120:
        raise ValueError(f"Report {field.replace('_', ' ')} must be 120 characters or fewer.")
    return value


def _data_quality(events: list[UsageEvent], registry: PricingRegistry) -> dict[str, object]:
    priced_events = [event for event in events if registry.get(event.provider, event.model) is not None]
    unpriced_models = sorted(
        {f"{event.provider}/{event.model}" for event in events if registry.get(event.provider, event.model) is None}
    )
    coverage = round(len(priced_events) / len(events) * 100, 1) if events else 100.0
    traced_events = [event for event in events if event.trace_id or event.provider_request_id]
    non_dollar_events = [
        event
        for event in events
        if event.metadata.get("billing_basis") in {
            "chatgpt-subscription",
            "antigravity-subscription-activity",
        }
    ]
    last_event_at = max((event.timestamp for event in events), key=lambda value: value) if events else None
    return {
        "rate_coverage_pct": coverage,
        "priced_requests": len(priced_events),
        "unpriced_requests": len(events) - len(priced_events),
        "unpriced_models": unpriced_models,
        "non_dollar_requests": len(non_dollar_events),
        "trace_coverage_pct": round(len(traced_events) / len(events) * 100, 1) if events else 100.0,
        "last_event_at": last_event_at,
    }


def _csv_text(payload: dict[str, Any]) -> str:
    value = payload.get("csv_text")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("csv_text is required.")
    if "\x00" in value:
        raise ValueError("CSV text contains an invalid null character.")
    return value


def _upload_name(payload: dict[str, Any]) -> str:
    raw = str(payload.get("filename") or "browser-upload.csv").strip()
    name = Path(raw).name
    if not name or len(name) > 200:
        raise ValueError("CSV filename must be 200 characters or fewer.")
    return name


def _optional_payload_text(payload: dict[str, Any], field: str) -> str | None:
    raw = payload.get(field)
    if raw is None:
        return None
    value = str(raw).strip()
    if len(value) > 200:
        raise ValueError(f"{field} must be 200 characters or fewer.")
    return value or None


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required.")
    if len(value) > 200:
        raise ValueError(f"{field} must be 200 characters or fewer.")
    return value


def _non_negative_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if value in (None, "") or isinstance(value, bool):
        raise ValueError(f"{field} is required and must be a non-negative number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a non-negative number.") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be a non-negative number.")
    return parsed


def _since_days(query: dict[str, list[str]]) -> int | None:
    values = query.get("since_days")
    if not values:
        return None
    try:
        days = int(values[0])
    except ValueError as error:
        raise ValueError("since_days must be a whole number.") from error
    if days < 1:
        raise ValueError("since_days must be at least 1.")
    return days


def _cutoff(days: int):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(days=days)
