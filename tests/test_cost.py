from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

import pytest
from typer.testing import CliRunner

from poliora.cost import (
    AntigravityPluginInstall,
    CatalogModel,
    CodexCli,
    DecisionStore,
    JsonlUsageStore,
    ModelCatalog,
    PricingRegistry,
    ReportBranding,
    SavingsDecision,
    UsageEvent,
    build_usage_report,
    check_budget,
    detect_local_tools,
    find_codex_cli,
    generate_recommendations,
    import_usage_csv,
    init_workspace,
    install_antigravity_plugin,
    log_anthropic_response,
    log_gemini_response,
    log_openai_response,
    log_usage,
    preview_usage_csv,
    record_antigravity_hook_event,
    record_codex_exec_event,
    render_html_report,
    simulate_model_switch,
    summarize_decisions,
    sync_provider_models,
    track_openai_call,
    track_openai_client,
    track_openai_compatible_call,
)
from poliora.main import app, dashboard_command
from poliora.web import create_dashboard_server


def test_cli_version_flag() -> None:
    # Derived from the package so a release bump does not fail the suite.
    from poliora import __version__

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"poliora {__version__}"


def test_local_tool_detection_checks_launchers_without_exposing_paths(tmp_path: Path) -> None:
    tools = detect_local_tools(
        tmp_path,
        which=lambda name: "C:/tools/codex.cmd" if name == "codex.cmd" else None,
    )

    by_id = {item.id: item for item in tools}
    assert by_id["codex-runtime"].detected is True
    assert "C:/tools" not in by_id["codex-runtime"].detail
    assert by_id["claude-code"].detected is False
    assert by_id["gemini-antigravity"].detected is False


def test_pricing_registry_estimates_known_model_cost() -> None:
    registry = PricingRegistry()

    cost = registry.estimate("openai", "gpt-4o-mini", 1_000_000, 1_000_000)

    assert cost == 0.75


def test_pricing_registry_applies_cached_input_rate() -> None:
    registry = PricingRegistry()

    cost = registry.estimate(
        "openai",
        "gpt-5.6-sol",
        1_000_000,
        1_000_000,
        cached_input_tokens=500_000,
    )

    assert cost == 32.75


def test_codex_exec_capture_keeps_subscription_usage_out_of_spend(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="codex-pilot")
    payload = {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 24_763,
            "cached_input_tokens": 24_448,
            "output_tokens": 122,
            "reasoning_output_tokens": 17,
        },
    }

    event = record_codex_exec_event(
        payload,
        model="gpt-5.4",
        thread_id="thread-safe-1",
        root=tmp_path,
    )

    assert event is not None
    assert event.cost_usd == 0.0
    assert event.cached_input_tokens == 24_448
    assert event.reasoning_tokens == 17
    assert event.trace_id == "thread-safe-1"
    assert event.metadata == {
        "source": "codex-exec-json",
        "billing_basis": "chatgpt-subscription",
        "content_collected": False,
    }


def test_codex_exec_capture_can_estimate_api_billed_usage(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    payload = {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 10_000,
            "cached_input_tokens": 4_000,
            "output_tokens": 1_000,
            "reasoning_output_tokens": 0,
        },
    }

    event = record_codex_exec_event(payload, model="gpt-5.6-sol", api_billed=True, root=tmp_path)

    assert event is not None
    assert event.cost_usd == PricingRegistry().estimate(
        "openai",
        "gpt-5.6-sol",
        10_000,
        1_000,
        cached_input_tokens=4_000,
    )
    assert event.metadata["billing_basis"] == "api-estimate"
    assert record_codex_exec_event({"type": "item.completed"}, model="gpt-5.6-sol", root=tmp_path) is None


def test_codex_cli_resolver_rejects_lookalikes_and_accepts_openai_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookalike = tmp_path / "lookalike.exe"
    official = tmp_path / "codex.exe"
    lookalike.touch()
    official.touch()

    class Result:
        def __init__(self, returncode: int, stdout: str, stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs: object) -> Result:
        del kwargs
        executable_text = " ".join(command)
        if "lookalike" in executable_text:
            return Result(1, "", "comic archive server")
        return Result(0, "codex-cli 0.128.0")

    monkeypatch.setattr("poliora.cost.codex_exec.subprocess.run", fake_run)
    result = find_codex_cli([lookalike, official])

    assert isinstance(result, CodexCli)
    assert result.executable == official.resolve()
    assert result.version == "codex-cli 0.128.0"


def test_codex_cli_preserves_prompt_as_one_subprocess_argument(tmp_path: Path) -> None:
    cli = CodexCli(
        executable=tmp_path / "codex.cmd",
        version="codex-cli 0.144.6",
        launcher=("node.exe", "codex.js"),
    )

    command = cli.command("exec", "--json", "Reply exactly: Poliora connected.")

    assert command == ["node.exe", "codex.js", "exec", "--json", "Reply exactly: Poliora connected."]


def test_antigravity_hook_records_activity_without_content_or_cost(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="antigravity-pilot")
    payload = {
        "conversationId": "private-conversation-id",
        "workspacePaths": [str(tmp_path)],
        "transcriptPath": str(tmp_path / "private-transcript.jsonl"),
        "invocationNum": 4,
        "initialNumSteps": 9,
    }

    event = record_antigravity_hook_event(payload, event_name="pre-invocation")

    assert event is not None
    assert event.provider == "google"
    assert event.model == "antigravity-managed"
    assert event.project == "antigravity-pilot"
    assert event.cost_usd == 0.0
    assert event.total_tokens == 0
    assert event.trace_id is not None and "private-conversation-id" not in event.trace_id
    assert event.metadata == {
        "source": "antigravity-pre-invocation-hook",
        "billing_basis": "antigravity-subscription-activity",
        "invocation_num": 4,
        "content_collected": False,
        "token_usage_available": False,
    }
    stored_text = (tmp_path / ".poliora" / "usage.jsonl").read_text(encoding="utf-8")
    assert "private-conversation-id" not in stored_text
    assert "private-transcript" not in stored_text
    assert build_usage_report([event]).non_dollar_requests == 1
    assert record_antigravity_hook_event(payload, event_name="stop") is None


def test_antigravity_plugin_installer_uses_documented_workspace_layout(tmp_path: Path) -> None:
    result = install_antigravity_plugin(tmp_path)

    assert isinstance(result, AntigravityPluginInstall)
    assert result.scope == "workspace"
    assert result.path == tmp_path / ".agents" / "plugins" / "poliora"
    assert json.loads((result.path / "plugin.json").read_text(encoding="utf-8")) == {"name": "poliora"}
    hooks = json.loads((result.path / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["poliora-activity"]["PreInvocation"][0]["command"]
    assert "poliora.main antigravity-hook --event pre-invocation" in command
    assert (result.path / "skills" / "poliora-cost" / "SKILL.md").exists()
    assert (result.path / "rules" / "poliora-privacy.md").exists()


def test_savings_ledger_separates_modeled_and_realized_value(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path)
    simulation = simulate_model_switch(
        [UsageEvent("openai", "gpt-4o", 1_000_000, 200_000, 3.5)],
        source_provider="openai",
        source_model="gpt-4o",
        target_provider="openai",
        target_model="gpt-4o-mini",
        percentage=50,
    )
    store = DecisionStore(workspace.decisions_path)
    decision = store.save(SavingsDecision.from_simulation("Support route", simulation))

    with pytest.raises(ValueError, match="passing quality"):
        decision.update(
            status="validated",
            quality_status="pending",
            measured_monthly_savings_usd=None,
            notes="",
        )

    rolled_out = decision.update(
        status="rolled-out",
        quality_status="pass",
        measured_monthly_savings_usd=42.5,
        notes="Passed 50 representative support tickets.",
    )
    store.save(rolled_out)
    summary = summarize_decisions(store.read_all())

    assert summary.decisions == 1
    assert summary.validated == 1
    assert summary.modeled_monthly_savings_usd == 0
    assert summary.realized_monthly_savings_usd == 42.5
    assert store.get(decision.id) == rolled_out


def test_model_catalog_includes_current_provider_families() -> None:
    catalog = ModelCatalog()

    assert catalog.get("openai", "gpt-5.2") is not None
    assert catalog.get("openai", "gpt-5.5") is not None
    assert catalog.get("openai", "gpt-5.6-sol") is not None
    assert catalog.get("anthropic", "claude-sonnet-4-20250514") is not None
    assert catalog.get("google", "gemini-3.5-flash") is not None
    assert catalog.get("deepseek", "deepseek-v4-pro") is not None
    assert catalog.get("xai", "grok-4.5") is not None
    assert catalog.get("anthropic", "claude-fable-5") is not None
    assert catalog.get("anthropic", "claude-opus-4-6") is not None
    assert catalog.get("anthropic", "claude-opus-4-8") is not None
    assert catalog.get("anthropic", "claude-sonnet-5") is not None
    assert PricingRegistry().estimate("openai", "gpt-5.6-sol", 1_000_000, 1_000_000) == 35.0
    assert PricingRegistry().estimate("anthropic", "claude-fable-5", 1_000_000, 1_000_000) == 60.0
    assert PricingRegistry().estimate("openai", "gpt-5.5", 1_000_000, 1_000_000) == 35.0
    assert PricingRegistry().estimate("anthropic", "claude-opus-4-6", 1_000_000, 1_000_000) == 30.0
    assert PricingRegistry().estimate("anthropic", "claude-opus-4-8", 1_000_000, 1_000_000) == 30.0
    assert PricingRegistry().estimate("anthropic", "claude-sonnet-5", 1_000_000, 1_000_000) == 18.0


def test_provider_model_sync_merges_models_visible_to_an_account() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"id": "gpt-future", "owned_by": "openai"}]}

    class Client:
        def get(self, url: str, *, headers: dict, params: dict) -> Response:
            assert url == "https://api.openai.com/v1/models"
            assert headers["Authorization"] == "Bearer test-key"
            assert params == {}
            return Response()

    catalog = ModelCatalog()
    result = sync_provider_models("openai", "test-key", catalog, client=Client())

    discovered = catalog.get("openai", "gpt-future")
    assert result.discovered == 1
    assert result.added == 1
    assert discovered is not None
    assert discovered.status == "account-available"


def test_model_catalog_load_keeps_built_ins_and_custom_model(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    catalog = ModelCatalog()
    catalog.add(CatalogModel("acme", "terra-v1", "Terra V1", status="custom"))
    catalog.save(path)

    loaded = ModelCatalog.load(path)

    assert loaded.get("acme", "terra-v1") is not None
    assert loaded.get("openai", "gpt-5.2") is not None


def test_usage_store_round_trips_events(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    event = UsageEvent(
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.00045,
        operation="support",
        project="acme",
    )

    store.append(event)

    loaded = store.read_all()
    assert len(loaded) == 1
    assert loaded[0].project == "acme"
    assert loaded[0].total_tokens == 1500


def test_usage_store_serializes_concurrent_appends(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")

    def append_event(index: int) -> None:
        store.append(UsageEvent("openai", "gpt-4o-mini", index, 1, 0.001))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append_event, range(30)))

    events = store.read_all()
    assert len(events) == 30
    assert sorted(event.input_tokens for event in events) == list(range(30))


def test_usage_report_summarizes_costs() -> None:
    events = [
        UsageEvent("openai", "gpt-4o-mini", 1000, 500, 0.00045, operation="chat", project="a"),
        UsageEvent("openai", "gpt-4o", 1000, 500, 0.0075, operation="agent", project="a"),
    ]

    report = build_usage_report(events, monthly_budget_usd=100.0)

    assert report.requests == 2
    assert report.total_tokens == 3000
    assert report.cost_usd == 0.00795
    assert report.by_model[0].name == "openai/gpt-4o"
    assert report.by_provider[0].name == "openai"
    assert report.daily_spend[0].requests == 2


def test_usage_report_flags_large_daily_spend_increase() -> None:
    events = [
        UsageEvent("openai", "gpt-4o-mini", 100, 50, 1.0, timestamp=f"2026-01-0{day}T12:00:00+00:00")
        for day in range(1, 5)
    ]
    events.append(UsageEvent("openai", "gpt-4o-mini", 100, 50, 3.0, timestamp="2026-01-05T12:00:00+00:00"))

    report = build_usage_report(events)

    assert report.forecast_confidence == "Low"
    assert len(report.spend_anomalies) == 1
    assert report.spend_anomalies[0].date == "2026-01-05"
    assert report.spend_anomalies[0].increase_pct == 200.0


def test_recommendations_use_report_hotspots() -> None:
    events = [
        UsageEvent("openai", "gpt-4o", 5000, 5000, 0.0625, operation="agent", project="a")
        for _ in range(10)
    ]
    report = build_usage_report(events, monthly_budget_usd=1.0)

    recommendations = generate_recommendations(report)

    assert recommendations
    assert any("model" in item.title.lower() for item in recommendations)


def test_workspace_and_sdk_log_usage(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="demo", monthly_budget_usd=500.0)

    event = log_usage(
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=1000,
        output_tokens=500,
        root=tmp_path,
    )

    assert event.project == "demo"
    assert event.cost_usd > 0
    assert JsonlUsageStore(tmp_path / ".poliora" / "usage.jsonl").read_all()


def test_openai_response_logger_accepts_dict(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="demo")
    response = {
        "model": "gpt-4o-mini",
        "_request_id": "req-openai-123",
        "usage": {
            "prompt_tokens": 300,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 120},
            "completion_tokens_details": {"reasoning_tokens": 80},
        },
    }

    event = log_openai_response(response, root=tmp_path)

    assert event.input_tokens == 300
    assert event.output_tokens == 200
    assert event.cached_input_tokens == 120
    assert event.reasoning_tokens == 80
    assert event.provider_request_id == "req-openai-123"
    assert event.provider == "openai"


def test_openai_compatible_capture_preserves_provider_label(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="demo")

    captured = track_openai_compatible_call(
        lambda: {"model": "deepseek-v4-flash", "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
        provider="deepseek",
        root=tmp_path,
    )

    assert captured.event.provider == "deepseek"
    assert captured.event.cost_usd > 0


def test_anthropic_response_logger_accepts_dict(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="demo")
    response = {
        "model": "claude-3-5-haiku",
        "id": "msg-123",
        "usage": {
            "input_tokens": 300,
            "output_tokens": 200,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 50,
        },
    }

    event = log_anthropic_response(response, root=tmp_path)

    assert event.input_tokens == 450
    assert event.output_tokens == 200
    assert event.cached_input_tokens == 100
    assert event.metadata["cache_creation_input_tokens"] == 50
    assert event.provider_request_id == "msg-123"
    assert event.provider == "anthropic"


def test_gemini_response_logger_tracks_cache_reasoning_and_tool_prompt(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="demo")
    response = {
        "model_version": "gemini-3.5-flash",
        "response_id": "gemini-123",
        "usage_metadata": {
            "prompt_token_count": 300,
            "cached_content_token_count": 100,
            "candidates_token_count": 200,
            "thoughts_token_count": 80,
            "tool_use_prompt_token_count": 20,
        },
    }

    event = log_gemini_response(response, root=tmp_path)

    assert event.provider == "google"
    assert event.input_tokens == 320
    assert event.cached_input_tokens == 100
    assert event.output_tokens == 280
    assert event.reasoning_tokens == 80
    assert event.metadata["tool_use_prompt_tokens"] == 20
    assert event.provider_request_id == "gemini-123"


def test_track_openai_call_records_latency_and_response(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="demo")

    def fake_create() -> dict:
        return {
            "model": "gpt-4o-mini",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
        }

    captured = track_openai_call(fake_create, root=tmp_path)

    assert captured.response["model"] == "gpt-4o-mini"
    assert captured.event.latency_ms is not None
    assert captured.event.cost_usd > 0


def test_track_openai_client_proxy_wraps_chat_create(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="demo")

    class Completions:
        def create(self) -> dict:
            return {
                "model": "gpt-4o-mini",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                },
            }

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()
        organization = "org_123"

    tracked = track_openai_client(Client(), root=tmp_path)
    response = tracked.chat.completions.create()

    assert response["model"] == "gpt-4o-mini"
    assert tracked.organization == "org_123"
    events = JsonlUsageStore(tmp_path / ".poliora" / "usage.jsonl").read_all()
    assert len(events) == 1
    assert events[0].operation == "chat.completions"


def test_budget_check_fails_when_projection_exceeds_limit() -> None:
    events = [UsageEvent("openai", "gpt-4o", 10_000, 5_000, 0.075)]
    report = build_usage_report(events, monthly_budget_usd=1.0)

    result = check_budget(report, limit_usd=1.0)

    assert not result.passed
    assert result.projected_monthly_usd > result.limit_usd


def test_model_switch_simulation_estimates_savings() -> None:
    events = [
        UsageEvent("openai", "gpt-4o", 10_000, 2_000, 0.045),
        UsageEvent("openai", "gpt-4o", 10_000, 2_000, 0.045),
    ]

    simulation = simulate_model_switch(
        events,
        source_provider="openai",
        source_model="gpt-4o",
        target_provider="openai",
        target_model="gpt-4o-mini",
        percentage=50,
    )

    assert simulation.matched_requests == 2
    assert simulation.affected_current_cost_usd == 0.045
    assert simulation.estimated_target_cost_usd < simulation.affected_current_cost_usd
    assert simulation.estimated_monthly_savings_usd > 0


def test_model_switch_simulation_preserves_cache_and_tool_charges() -> None:
    events = [
        UsageEvent(
            "openai",
            "gpt-5.6-sol",
            1_000_000,
            0,
            5.7,
            cached_input_tokens=500_000,
            tool_cost_usd=0.2,
        )
    ]

    simulation = simulate_model_switch(
        events,
        source_provider="openai",
        source_model="gpt-5.6-sol",
        target_provider="openai",
        target_model="gpt-5.6-terra",
    )

    # gpt-5.6-terra bills $2.00/1M input, $0.20/1M cached, $12.00/1M output:
    # 500k regular input ($1.00) + 500k cached ($0.10) + $0.20 tool cost.
    assert simulation.estimated_target_cost_usd == 1.30


def test_simulation_uses_the_standard_rate_after_an_introductory_rate_expires() -> None:
    events = [UsageEvent("openai", "gpt-5.6-sol", 1_000_000, 100_000, 8.0)]

    # Sonnet 5 is on an introductory rate that expires, so a forward-looking
    # monthly projection against it would overstate realized savings.
    expiring = simulate_model_switch(
        events,
        source_provider="openai",
        source_model="gpt-5.6-sol",
        target_provider="anthropic",
        target_model="claude-sonnet-5",
    )
    stable = simulate_model_switch(
        events,
        source_provider="openai",
        source_model="gpt-5.6-sol",
        target_provider="anthropic",
        target_model="claude-haiku-4-5",
    )

    assert expiring.target_rate_warning == ""
    assert stable.target_rate_warning == ""


def test_simulation_warns_when_the_target_rate_is_unverified() -> None:
    events = [UsageEvent("openai", "gpt-5.6-sol", 1_000_000, 100_000, 8.0)]

    simulation = simulate_model_switch(
        events,
        source_provider="openai",
        source_model="gpt-5.6-sol",
        target_provider="mistral",
        target_model="mistral-medium-3-5",
    )

    assert "not confirmed" in simulation.target_rate_warning


def test_csv_import_prices_rows_at_the_rate_in_effect_when_usage_happened(tmp_path: Path) -> None:
    source = tmp_path / "usage.csv"
    source.write_text(
        "provider,model,input_tokens,output_tokens,timestamp\n"
        "anthropic,claude-sonnet-5,1000000,0,2026-08-20T00:00:00Z\n"
        "anthropic,claude-sonnet-5,1000000,0,2026-09-20T00:00:00Z\n",
        encoding="utf-8",
    )
    store = JsonlUsageStore(tmp_path / "usage.jsonl")

    import_usage_csv(source, store)
    august, september = store.read_all()

    # Same usage, different months: the August row keeps the introductory rate.
    assert august.cost_usd == 2.00
    assert september.cost_usd == 3.00


def test_pricing_resolves_the_schedule_in_effect_at_the_event_date() -> None:
    registry = PricingRegistry()

    august = datetime(2026, 8, 20, tzinfo=timezone.utc)
    september = datetime(2026, 9, 20, tzinfo=timezone.utc)

    intro = registry.get("anthropic", "claude-sonnet-5", at=august)
    standard = registry.get("anthropic", "claude-sonnet-5", at=september)

    assert intro is not None and standard is not None
    assert (intro.input_per_1m, intro.output_per_1m) == (2.00, 10.00)
    assert (standard.input_per_1m, standard.output_per_1m) == (3.00, 15.00)

    # The same usage must not reprice when the report is re-run after the
    # introductory rate expires.
    assert registry.estimate("anthropic", "claude-sonnet-5", 1_000_000, 0, at=august) == 2.00
    assert registry.estimate("anthropic", "claude-sonnet-5", 1_000_000, 0, at=september) == 3.00


def test_retired_models_still_price_historical_usage() -> None:
    registry = PricingRegistry()

    # Retirement stops new usage; it does not change what past usage cost.
    pricing = registry.get("anthropic", "claude-3-5-haiku")

    assert pricing is not None
    assert pricing.estimate(1_000_000, 0) == 0.80


def test_unknown_model_reports_a_gap_rather_than_a_free_estimate() -> None:
    registry = PricingRegistry()

    gap = registry.explain("openai", "gpt-not-a-real-model")

    assert gap is not None
    assert gap.model == "gpt-not-a-real-model"
    assert registry.explain("openai", "gpt-5.6-sol") is None


def test_default_rates_carry_provenance() -> None:
    registry = PricingRegistry()

    pricing = registry.get("openai", "gpt-5.6-terra")

    assert pricing is not None
    assert (pricing.input_per_1m, pricing.output_per_1m) == (2.00, 12.00)
    assert pricing.verified_on == "2026-08-16"
    assert pricing.source_url


def test_pricing_round_trips_schedules_through_json(tmp_path: Path) -> None:
    target = PricingRegistry().save(tmp_path / "pricing.json")
    reloaded = PricingRegistry.load(target)

    assert len(reloaded.schedules("anthropic", "claude-sonnet-5")) == 2
    august = datetime(2026, 8, 20, tzinfo=timezone.utc)
    intro = reloaded.get("anthropic", "claude-sonnet-5", at=august)
    assert intro is not None and intro.input_per_1m == 2.00


def test_html_report_escapes_project_and_model_names() -> None:
    report = build_usage_report(
        [UsageEvent("openai", "<costly-model>", 1_000, 500, 0.01)],
        monthly_budget_usd=20.0,
    )

    html = render_html_report(
        report,
        generate_recommendations(report),
        project="Acme <AI>",
        branding=ReportBranding(
            organization="Daniel & Co",
            client="Client <One>",
            prepared_by="Daniel",
            title="Quarterly AI Review",
            accent_color="#123456",
        ),
    )

    assert "Quarterly AI Review" in html
    assert "Daniel &amp; Co" in html
    assert "Prepared for Client &lt;One&gt;" in html
    assert "openai/&lt;costly-model&gt;" in html
    assert "Projected monthly spend" in html
    assert "How to read this report" in html
    assert "--green: #123456" in html


def test_csv_import_accepts_common_headers_and_default_provider(tmp_path: Path) -> None:
    source = tmp_path / "usage.csv"
    source.write_text(
        "model,prompt_tokens,completion_tokens,workflow,customer\n"
        "gpt-4o-mini,1000,250,support,client-a\n",
        encoding="utf-8",
    )
    store = JsonlUsageStore(tmp_path / "usage.jsonl")

    result = import_usage_csv(source, store, default_provider="openai", default_project="demo")

    event = store.read_all()[0]
    assert result.imported_events == 1
    assert event.cost_usd > 0
    assert event.operation == "support"
    assert event.user == "client-a"
    assert event.project == "demo"


def test_csv_import_rejects_missing_provider(tmp_path: Path) -> None:
    source = tmp_path / "usage.csv"
    source.write_text("model,input_tokens,output_tokens\ngpt-4o-mini,1000,250\n", encoding="utf-8")

    with pytest.raises(ValueError, match="provider is required"):
        import_usage_csv(source, JsonlUsageStore(tmp_path / "usage.jsonl"))


def test_csv_preview_reports_all_issues_and_skip_invalid_is_explicit(tmp_path: Path) -> None:
    source = tmp_path / "mixed.csv"
    source.write_text(
        "provider,model,input_tokens,output_tokens\n"
        "openai,gpt-4o-mini,1000,200\n"
        "openai,gpt-4o-mini,broken,200\n"
        ",custom-model,100,50\n",
        encoding="utf-8",
    )
    store = JsonlUsageStore(tmp_path / "usage.jsonl")

    preview = preview_usage_csv(source)

    assert preview.total_rows == 3
    assert preview.valid_rows == 1
    assert preview.invalid_rows == 2
    assert preview.mapped_columns["input_tokens"] == "input_tokens"
    assert {issue.line_number for issue in preview.issues} == {3, 4}
    assert store.read_all() == []

    with pytest.raises(ValueError, match="whole number"):
        import_usage_csv(source, store)
    assert store.read_all() == []

    result = import_usage_csv(source, store, skip_invalid=True)
    assert result.imported_events == 1
    assert result.skipped_rows == 2
    assert len(store.read_all()) == 1


def test_local_dashboard_serves_overview_and_simulation(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="web-demo", monthly_budget_usd=50.0)
    JsonlUsageStore(tmp_path / ".poliora" / "usage.jsonl").append(
        UsageEvent("openai", "gpt-4o", 1_000, 500, 0.0075, cached_input_tokens=400, trace_id="trace-1")
    )
    server = create_dashboard_server(tmp_path, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        connection = HTTPConnection("127.0.0.1", port)
        connection.request("GET", "/")
        page_response = connection.getresponse()
        page = page_response.read().decode("utf-8")
        assert page_response.status == 200
        assert "Let’s make your AI use easier to understand." in page
        assert "Your AI check-in" in page
        assert "Ways to save" in page
        assert "Understand Poliora from first data to savings decision" in page
        assert "New to Poliora? Start here" in page
        assert 'data-view="guide"' in page
        assert "Connect the AI tools you use" in page
        assert "Scan this computer" in page
        assert "CSV now / adapter planned" in page
        assert "npm.cmd install -g @openai/codex" in page
        assert 'data-view-target="overview"' in page
        assert 'data-view-target="connections"' in page
        assert 'data-view-target="scenarios"' in page
        assert 'data-view-target="models"' in page
        assert "Workspace health" in page

        connection.request("POST", "/api/detect-tools", body="{}", headers={"Content-Type": "application/json"})
        detection_response = connection.getresponse()
        detection = json.loads(detection_response.read())
        assert detection_response.status == 200
        assert "does not open AI tools" in detection["notice"]
        assert {item["id"] for item in detection["tools"]} == {
            "codex-runtime",
            "claude-code",
            "cursor-team",
            "gemini-antigravity",
        }

        connection.request("GET", "/api/overview")
        response = connection.getresponse()
        overview = json.loads(response.read())
        assert response.status == 200
        assert overview["project"] == "web-demo"
        assert overview["report"]["requests"] == 1
        assert overview["report"]["cached_input_tokens"] == 400
        assert overview["report"]["forecast_confidence"] == "Low"
        assert overview["report"]["daily_spend"][0]["requests"] == 1
        assert overview["data_quality"]["rate_coverage_pct"] == 100.0
        assert overview["data_quality"]["trace_coverage_pct"] == 100.0
        assert overview["data_quality"]["last_event_at"] is not None
        assert any(item["model"] == "gpt-5.2" for item in overview["models"])
        assert any(item["model"] == "gpt-5.6-sol" and item["cached_input_per_1m"] == 0.5 for item in overview["models"])
        assert any(item["model"] == "deepseek-v4-pro" and item["priced"] for item in overview["models"])
        assert any(item["id"] == "cursor-team" for item in overview["connectors"])

        connection_payload = json.dumps({})
        connection.request(
            "POST",
            "/api/connectors/cursor-team/consent",
            body=connection_payload,
            headers={"Content-Type": "application/json"},
        )
        connector_response = connection.getresponse()
        connector_result = json.loads(connector_response.read())
        assert connector_response.status == 200
        assert connector_result["connector"]["connection"]["state"] == "awaiting-setup"

        connection.request("GET", "/api/overview")
        connected_overview_response = connection.getresponse()
        connected_overview = json.loads(connected_overview_response.read())
        assert connected_overview_response.status == 200
        cursor_connector = next(item for item in connected_overview["connectors"] if item["id"] == "cursor-team")
        assert cursor_connector["connection"]["state"] == "awaiting-setup"

        connection.request(
            "POST",
            "/api/connectors/cursor-team/disconnect",
            body=connection_payload,
            headers={"Content-Type": "application/json"},
        )
        disconnect_response = connection.getresponse()
        assert disconnect_response.status == 200
        assert json.loads(disconnect_response.read())["disconnected"] is True

        connection.request("GET", "/api/overview?since_days=7")
        filtered_response = connection.getresponse()
        filtered_overview = json.loads(filtered_response.read())
        assert filtered_response.status == 200
        assert filtered_overview["report"]["requests"] == 1

        payload = json.dumps(
            {
                "source_provider": "openai",
                "source_model": "gpt-4o",
                "target_provider": "openai",
                "target_model": "gpt-4o-mini",
                "percentage": 50,
            }
        )
        connection.request("POST", "/api/simulate", body=payload, headers={"Content-Type": "application/json"})
        simulation_response = connection.getresponse()
        simulation = json.loads(simulation_response.read())
        assert simulation_response.status == 200
        assert simulation["estimated_monthly_savings_usd"] > 0

        pricing_payload = json.dumps(
            {
                "provider": "openai",
                "model": "gpt-4o",
                "input_per_1m": 1.25,
                "output_per_1m": 5.5,
                "cached_input_per_1m": 0.15,
                "note": "pilot contract rate",
            }
        )
        connection.request("POST", "/api/pricing", body=pricing_payload, headers={"Content-Type": "application/json"})
        pricing_response = connection.getresponse()
        pricing_result = json.loads(pricing_response.read())
        assert pricing_response.status == 200
        assert pricing_result["pricing"]["cached_input_per_1m"] == 0.15
        override = PricingRegistry.load(tmp_path / ".poliora" / "pricing.json").get("openai", "gpt-4o")
        assert override is not None
        assert override.output_per_1m == 5.5
        assert override.note == "pilot contract rate"

        scenario_payload = json.dumps(
            {
                "name": "Move support traffic",
                "source_provider": "openai",
                "source_model": "gpt-4o",
                "target_provider": "openai",
                "target_model": "gpt-4o-mini",
                "percentage": 35,
            }
        )
        connection.request(
            "POST", "/api/scenarios", body=scenario_payload, headers={"Content-Type": "application/json"}
        )
        scenario_response = connection.getresponse()
        scenario_result = json.loads(scenario_response.read())
        assert scenario_response.status == 200
        assert scenario_result["scenario"]["name"] == "Move support traffic"

        decision_payload = json.dumps(
            {
                "name": "Validate support route",
                "source_provider": "openai",
                "source_model": "gpt-4o",
                "target_provider": "openai",
                "target_model": "gpt-4o-mini",
                "percentage": 35,
            }
        )
        connection.request(
            "POST", "/api/decisions", body=decision_payload, headers={"Content-Type": "application/json"}
        )
        decision_response = connection.getresponse()
        decision_result = json.loads(decision_response.read())
        assert decision_response.status == 200
        assert decision_result["decision"]["status"] == "proposed"

        decision_id = decision_result["decision"]["id"]
        decision_update = json.dumps(
            {
                "status": "rolled-out",
                "quality_status": "pass",
                "measured_monthly_savings_usd": 18.25,
                "notes": "Quality review passed.",
            }
        )
        connection.request(
            "PATCH",
            f"/api/decisions/{decision_id}",
            body=decision_update,
            headers={"Content-Type": "application/json"},
        )
        update_response = connection.getresponse()
        assert update_response.status == 200
        assert json.loads(update_response.read())["decision"]["measured_monthly_savings_usd"] == 18.25

        connection.request("GET", "/api/overview")
        saved_overview_response = connection.getresponse()
        saved_overview = json.loads(saved_overview_response.read())
        assert saved_overview_response.status == 200
        assert len(saved_overview["scenarios"]) == 1
        assert saved_overview["savings_ledger"]["realized_monthly_savings_usd"] == 18.25
        assert saved_overview["evidence"]["grade"] in {"A", "B", "C", "D", "E"}

        connection.request("DELETE", f"/api/scenarios/{scenario_result['scenario']['id']}")
        delete_response = connection.getresponse()
        assert delete_response.status == 200
        assert json.loads(delete_response.read())["deleted"] is True

        JsonlUsageStore(tmp_path / ".poliora" / "usage.jsonl").append(
            UsageEvent("openai", "old-model", 500, 100, 0.01, timestamp="2026-01-01T00:00:00+00:00")
        )
        connection.request("GET", "/report.html?since_days=7")
        report_response = connection.getresponse()
        report_html = report_response.read().decode("utf-8")
        assert report_response.status == 200
        assert "attachment" in report_response.getheader("Content-Disposition")
        assert "web-demo AI spend report" in report_html
        assert "old-model" not in report_html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_dashboard_loads_guided_sample_only_when_usage_is_empty(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="guided-demo")
    server = create_dashboard_server(tmp_path, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        connection = HTTPConnection("127.0.0.1", port)
        connection.request("POST", "/api/demo-data", body="{}", headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200
        assert result["imported_events"] == 9

        connection.request("GET", "/api/overview")
        overview_response = connection.getresponse()
        overview = json.loads(overview_response.read())
        assert overview_response.status == 200
        assert overview["report"]["requests"] == 9
        assert overview["report"]["by_operation"]

        connection.request("POST", "/api/demo-data", body="{}", headers={"Content-Type": "application/json"})
        second_response = connection.getresponse()
        assert second_response.status == 400
        assert "empty workspace" in json.loads(second_response.read())["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_previews_imports_and_brands_report(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="browser-import")
    server = create_dashboard_server(tmp_path, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    csv_text = (
        "provider,model,input_tokens,output_tokens,workflow\n"
        "openai,gpt-4o-mini,1000,200,support\n"
        "openai,gpt-4o-mini,invalid,200,support\n"
    )
    base_payload = {"filename": "pilot.csv", "csv_text": csv_text, "project": "client-work"}

    try:
        connection = HTTPConnection("127.0.0.1", port)
        connection.request(
            "POST",
            "/api/import/preview",
            body=json.dumps(base_payload),
            headers={"Content-Type": "application/json"},
        )
        preview_response = connection.getresponse()
        preview = json.loads(preview_response.read())["preview"]
        assert preview_response.status == 200
        assert preview["valid_rows"] == 1
        assert preview["invalid_rows"] == 1
        assert JsonlUsageStore(tmp_path / ".poliora" / "usage.jsonl").read_all() == []

        connection.request(
            "POST",
            "/api/import",
            body=json.dumps(base_payload),
            headers={"Content-Type": "application/json"},
        )
        strict_response = connection.getresponse()
        assert strict_response.status == 400
        strict_response.read()
        assert JsonlUsageStore(tmp_path / ".poliora" / "usage.jsonl").read_all() == []

        connection.request(
            "POST",
            "/api/import",
            body=json.dumps({**base_payload, "skip_invalid": True}),
            headers={"Content-Type": "application/json"},
        )
        import_response = connection.getresponse()
        imported = json.loads(import_response.read())["result"]
        assert import_response.status == 200
        assert imported["imported_events"] == 1
        assert imported["skipped_rows"] == 1

        report_path = (
            "/report.html?client=Acme%20%26%20Co&prepared_by=Daniel&title=Quarterly%20Review&accent=%23123456"
        )
        connection.request("GET", report_path)
        report_response = connection.getresponse()
        report_html = report_response.read().decode("utf-8")
        assert report_response.status == 200
        assert "Quarterly Review" in report_html
        assert "Prepared for Acme &amp; Co" in report_html
        assert "Prepared by Daniel" in report_html
        assert "--green: #123456" in report_html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_corrupt_workspace_config_falls_back_to_safe_defaults(tmp_path: Path) -> None:
    workspace_dir = tmp_path / ".poliora"
    workspace_dir.mkdir()
    (workspace_dir / "config.json").write_text("{", encoding="utf-8")

    from poliora.cost import load_workspace

    workspace = load_workspace(tmp_path)

    assert workspace.root == tmp_path.resolve()
    assert workspace.project == "default"
    assert workspace.monthly_budget_usd == 1000.0


def test_dashboard_command_creates_first_run_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, object] = {}

    def capture_server(root: Path, *, host: str, port: int) -> None:
        started.update(root=root, host=host, port=port)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("poliora.web.run_dashboard", capture_server)

    dashboard_command(
        host="127.0.0.1",
        port=8877,
        open_browser=False,
        project="first-run",
        monthly_budget=250.0,
    )

    config = json.loads((tmp_path / ".poliora" / "config.json").read_text(encoding="utf-8"))
    assert config["project"] == "first-run"
    assert config["monthly_budget_usd"] == 250.0
    assert started == {"root": tmp_path.resolve(), "host": "127.0.0.1", "port": 8877}
