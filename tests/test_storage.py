"""Coverage for local persistence: usage log, decision ledger, scenarios, consent.

Poliora's whole claim is that the numbers are yours and stay on your machine, so
the storage layer is load-bearing for trust. These tests pin the validation that
keeps impossible data out of the log, the atomic writes that keep an interrupted
save from destroying a ledger, and the invariants that stop a savings decision
from claiming a win it never earned.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poliora.cost.companion import ConnectorStore, connector_catalog
from poliora.cost.decisions import DecisionStore, SavingsDecision, summarize_decisions
from poliora.cost.scenarios import SavedScenario, ScenarioStore
from poliora.cost.simulation import simulate_model_switch
from poliora.cost.usage import JsonlUsageStore, UsageEvent, parse_timestamp, write_events
from poliora.cost.workspace import init_workspace, load_workspace


def simulation():
    """A small, valid simulation used to seed scenarios and decisions."""
    return simulate_model_switch(
        [UsageEvent("openai", "gpt-5.6-sol", 1_000_000, 100_000, 8.0)],
        source_provider="openai",
        source_model="gpt-5.6-sol",
        target_provider="anthropic",
        target_model="claude-haiku-4-5",
    )


# --- usage event validation ------------------------------------------------


def test_event_reports_total_tokens() -> None:
    assert UsageEvent("openai", "m", 1_000, 250, 0.0).total_tokens == 1_250


def test_event_round_trips_through_a_dict() -> None:
    original = UsageEvent("openai", "m", 10, 5, 0.5, trace_id="t-1", metadata={"k": "v"})
    assert UsageEvent.from_dict(original.to_dict()) == original


def test_event_defaults_its_timestamp_to_now() -> None:
    assert parse_timestamp(UsageEvent("openai", "m", 1, 1, 0.0).timestamp) <= datetime.now(timezone.utc)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"cached_input_tokens": -1},
        {"reasoning_tokens": -1},
    ],
)
def test_negative_token_counts_are_refused(tmp_path: Path, kwargs: dict) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    base = {"input_tokens": 10, "output_tokens": 10}
    with pytest.raises(ValueError, match="non-negative"):
        store.append(UsageEvent("openai", "m", cost_usd=0.0, **{**base, **kwargs}))


def test_cached_tokens_cannot_exceed_input_tokens(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    with pytest.raises(ValueError, match="cannot exceed"):
        store.append(UsageEvent("openai", "m", 100, 10, 0.0, cached_input_tokens=101))


@pytest.mark.parametrize("field", ["cost_usd", "tool_cost_usd"])
def test_negative_costs_are_refused(tmp_path: Path, field: str) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    with pytest.raises(ValueError, match="non-negative"):
        store.append(UsageEvent("openai", "m", 10, 10, **{"cost_usd": 0.0, field: -1.0}))


def test_a_rejected_event_is_not_written(tmp_path: Path) -> None:
    target = tmp_path / "usage.jsonl"
    store = JsonlUsageStore(target)
    with pytest.raises(ValueError):
        store.append(UsageEvent("openai", "m", -1, 0, 0.0))
    assert not target.exists() or target.read_text(encoding="utf-8") == ""


# --- usage log behaviour ---------------------------------------------------


def test_reading_a_missing_log_returns_nothing(tmp_path: Path) -> None:
    assert JsonlUsageStore(tmp_path / "absent.jsonl").read_all() == []


def test_appends_preserve_insertion_order(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    for index in range(5):
        store.append(UsageEvent("openai", f"m{index}", 1, 1, 0.0))
    assert [event.model for event in store.read_all()] == [f"m{i}" for i in range(5)]


def test_append_creates_missing_parent_directories(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "a" / "b" / "usage.jsonl")
    store.append(UsageEvent("openai", "m", 1, 1, 0.0))
    assert len(store.read_all()) == 1


def test_blank_lines_in_the_log_are_skipped(tmp_path: Path) -> None:
    target = tmp_path / "usage.jsonl"
    target.write_text(
        json.dumps(UsageEvent("openai", "m", 1, 1, 0.0).to_dict()) + "\n\n\n", encoding="utf-8"
    )
    assert len(JsonlUsageStore(target).read_all()) == 1


def test_read_since_filters_by_timestamp(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    now = datetime.now(timezone.utc)
    store.append(UsageEvent("openai", "old", 1, 1, 0.0, timestamp=(now - timedelta(days=30)).isoformat()))
    store.append(UsageEvent("openai", "new", 1, 1, 0.0, timestamp=now.isoformat()))
    recent = store.read_since(now - timedelta(days=1))
    assert [event.model for event in recent] == ["new"]


def test_read_since_without_a_cutoff_returns_everything(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    store.append(UsageEvent("openai", "m", 1, 1, 0.0))
    assert len(store.read_since(None)) == 1


def test_concurrent_appends_do_not_lose_events(tmp_path: Path) -> None:
    # The file lock exists so two Poliora processes can log at once.
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: store.append(UsageEvent("openai", f"m{i}", 1, 1, 0.0)), range(40)))
    assert len(store.read_all()) == 40


def test_write_events_replaces_the_whole_log(tmp_path: Path) -> None:
    target = tmp_path / "usage.jsonl"
    store = JsonlUsageStore(target)
    store.append(UsageEvent("openai", "stale", 1, 1, 0.0))
    write_events(target, [UsageEvent("openai", "fresh", 1, 1, 0.0)])
    assert [event.model for event in store.read_all()] == ["fresh"]


def test_write_events_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "usage.jsonl"
    write_events(target, [UsageEvent("openai", "m", 1, 1, 0.0)])
    assert list(tmp_path.glob(".*tmp")) == []


@pytest.mark.parametrize(
    "raw",
    ["2026-08-16T12:00:00Z", "2026-08-16T12:00:00+00:00", "2026-08-16T12:00:00"],
)
def test_timestamps_parse_to_aware_utc(raw: str) -> None:
    parsed = parse_timestamp(raw)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


# --- savings decision ledger -----------------------------------------------


def test_a_new_decision_starts_unproven() -> None:
    decision = SavingsDecision.from_simulation("Try Haiku", simulation())
    assert decision.status == "proposed"
    assert decision.quality_status == "pending"
    assert decision.measured_monthly_savings_usd is None


def test_a_decision_requires_a_name() -> None:
    with pytest.raises(ValueError, match="name is required"):
        SavingsDecision.from_simulation("   ", simulation())


def test_a_decision_name_has_a_length_limit() -> None:
    with pytest.raises(ValueError, match="120 characters"):
        SavingsDecision.from_simulation("x" * 121, simulation())


def test_a_decision_can_be_validated_after_a_passing_quality_check() -> None:
    updated = SavingsDecision.from_simulation("Try Haiku", simulation()).update(
        status="validated", quality_status="pass", measured_monthly_savings_usd=42.0, notes="held up"
    )
    assert updated.status == "validated"
    assert updated.measured_monthly_savings_usd == 42.0


def test_a_decision_cannot_be_validated_without_passing_quality() -> None:
    # The core integrity rule: no claiming a win the evidence does not support.
    with pytest.raises(ValueError, match="passing quality result"):
        SavingsDecision.from_simulation("Try Haiku", simulation()).update(
            status="validated", quality_status="pending", measured_monthly_savings_usd=None, notes=""
        )


def test_a_rolled_out_decision_cannot_be_validated_without_passing_quality() -> None:
    with pytest.raises(ValueError, match="passing quality result"):
        SavingsDecision.from_simulation("Try Haiku", simulation()).update(
            status="rolled-out", quality_status="fail", measured_monthly_savings_usd=None, notes=""
        )


def test_a_rejected_decision_cannot_also_have_passed() -> None:
    with pytest.raises(ValueError, match="rejected decision"):
        SavingsDecision.from_simulation("Try Haiku", simulation()).update(
            status="rejected", quality_status="pass", measured_monthly_savings_usd=None, notes=""
        )


def test_measured_savings_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="zero or greater"):
        SavingsDecision.from_simulation("Try Haiku", simulation()).update(
            status="testing", quality_status="pending", measured_monthly_savings_usd=-1.0, notes=""
        )


@pytest.mark.parametrize("status", ["nonsense", "done", ""])
def test_unknown_statuses_are_refused(status: str) -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        SavingsDecision.from_simulation("Try Haiku", simulation()).update(
            status=status, quality_status="pending", measured_monthly_savings_usd=None, notes=""
        )


def test_notes_have_a_length_limit() -> None:
    with pytest.raises(ValueError, match="600 characters"):
        SavingsDecision.from_simulation("Try Haiku", simulation()).update(
            status="testing", quality_status="pending", measured_monthly_savings_usd=None, notes="x" * 601
        )


def test_an_update_stamps_a_new_modified_time() -> None:
    decision = SavingsDecision.from_simulation("Try Haiku", simulation())
    updated = decision.update(
        status="testing", quality_status="pending", measured_monthly_savings_usd=None, notes=""
    )
    assert updated.updated_at >= decision.updated_at


def test_a_corrupt_ledger_entry_is_refused() -> None:
    with pytest.raises(ValueError):
        SavingsDecision.from_dict({"id": "x", "created_at": "now", "status": "invented"})


# --- decision store --------------------------------------------------------


def test_reading_a_missing_ledger_returns_nothing(tmp_path: Path) -> None:
    assert DecisionStore(tmp_path / "absent.json").read_all() == []


def test_a_saved_decision_can_be_read_back(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    saved = store.save(SavingsDecision.from_simulation("Try Haiku", simulation()))
    assert store.get(saved.id) == saved


def test_saving_the_same_id_replaces_rather_than_duplicates(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    decision = store.save(SavingsDecision.from_simulation("Try Haiku", simulation()))
    store.save(decision.update(
        status="testing", quality_status="pending", measured_monthly_savings_usd=None, notes="running"
    ))
    assert len(store.read_all()) == 1


def test_deleting_a_decision_reports_success(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    decision = store.save(SavingsDecision.from_simulation("Try Haiku", simulation()))
    assert store.delete(decision.id) is True
    assert store.read_all() == []


def test_deleting_an_unknown_decision_reports_failure(tmp_path: Path) -> None:
    assert DecisionStore(tmp_path / "decisions.json").delete("nope") is False


def test_a_non_list_ledger_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "decisions.json"
    target.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        DecisionStore(target).read_all()


# --- ledger rollup ---------------------------------------------------------


def test_an_empty_ledger_summarizes_to_zero() -> None:
    summary = summarize_decisions([])
    assert summary.decisions == 0
    assert summary.modeled_monthly_savings_usd == 0.0
    assert summary.realized_monthly_savings_usd == 0.0


def test_modeled_and_realized_savings_never_double_count() -> None:
    # A rolled-out decision's saving is realized, so it must leave the modeled
    # column; otherwise the same dollar is claimed twice.
    proposed = SavingsDecision.from_simulation("Proposed", simulation())
    rolled_out = SavingsDecision.from_simulation("Rolled out", simulation()).update(
        status="rolled-out", quality_status="pass", measured_monthly_savings_usd=10.0, notes=""
    )
    summary = summarize_decisions([proposed, rolled_out])
    assert summary.realized_monthly_savings_usd == 10.0
    assert summary.modeled_monthly_savings_usd == proposed.estimated_monthly_savings_usd


def test_rejected_decisions_are_excluded_from_modeled_savings() -> None:
    rejected = SavingsDecision.from_simulation("Rejected", simulation()).update(
        status="rejected", quality_status="fail", measured_monthly_savings_usd=None, notes=""
    )
    assert summarize_decisions([rejected]).modeled_monthly_savings_usd == 0.0


def test_active_tests_are_counted(tmp_path: Path) -> None:
    testing = SavingsDecision.from_simulation("Testing", simulation()).update(
        status="testing", quality_status="pending", measured_monthly_savings_usd=None, notes=""
    )
    assert summarize_decisions([testing]).active_tests == 1


def test_ledger_summary_serializes() -> None:
    assert set(summarize_decisions([]).to_dict()) == {
        "decisions", "active_tests", "validated",
        "modeled_monthly_savings_usd", "realized_monthly_savings_usd",
    }


# --- scenarios -------------------------------------------------------------


def test_reading_missing_scenarios_returns_nothing(tmp_path: Path) -> None:
    assert ScenarioStore(tmp_path / "absent.json").read_all() == []


def test_a_scenario_round_trips(tmp_path: Path) -> None:
    store = ScenarioStore(tmp_path / "scenarios.json")
    saved = store.save(SavedScenario.from_simulation("Haiku swap", simulation()))
    assert store.read_all()[0].id == saved.id


def test_saving_a_scenario_twice_replaces_it(tmp_path: Path) -> None:
    store = ScenarioStore(tmp_path / "scenarios.json")
    scenario = store.save(SavedScenario.from_simulation("Haiku swap", simulation()))
    store.save(scenario)
    assert len(store.read_all()) == 1


def test_deleting_a_scenario_reports_success(tmp_path: Path) -> None:
    store = ScenarioStore(tmp_path / "scenarios.json")
    scenario = store.save(SavedScenario.from_simulation("Haiku swap", simulation()))
    assert store.delete(scenario.id) is True


def test_deleting_an_unknown_scenario_reports_failure(tmp_path: Path) -> None:
    assert ScenarioStore(tmp_path / "scenarios.json").delete("nope") is False


def test_a_non_list_scenario_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "scenarios.json"
    target.write_text('"not a list"', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        ScenarioStore(target).read_all()


# --- connector consent -----------------------------------------------------


def test_no_connector_is_enabled_before_consent(tmp_path: Path) -> None:
    assert ConnectorStore(tmp_path / "connectors.json").read_all() == []


def test_consent_is_recorded_as_awaiting_setup(tmp_path: Path) -> None:
    # Consent is not a connection: nothing is read until setup completes.
    connection = ConnectorStore(tmp_path / "connectors.json").consent("cursor-team")
    assert connection.state == "awaiting-setup"


def test_consent_is_idempotent(tmp_path: Path) -> None:
    store = ConnectorStore(tmp_path / "connectors.json")
    store.consent("cursor-team")
    store.consent("cursor-team")
    assert len(store.read_all()) == 1


def test_disconnecting_removes_consent(tmp_path: Path) -> None:
    store = ConnectorStore(tmp_path / "connectors.json")
    store.consent("cursor-team")
    assert store.disconnect("cursor-team") is True
    assert store.read_all() == []


def test_disconnecting_something_never_connected_reports_failure(tmp_path: Path) -> None:
    assert ConnectorStore(tmp_path / "connectors.json").disconnect("cursor-team") is False


def test_connector_catalog_states_its_availability_honestly() -> None:
    # Every connector must declare what it can actually do today.
    for definition in connector_catalog():
        assert definition.availability in {"ready", "credential-required", "official-surface-needed"}
        assert definition.setup_hint


def test_connector_catalog_ids_are_unique() -> None:
    ids = [definition.id for definition in connector_catalog()]
    assert len(ids) == len(set(ids))


# --- workspace -------------------------------------------------------------


def test_init_creates_every_workspace_file(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path, project="demo", monthly_budget_usd=250.0)
    for path in (
        workspace.config_path, workspace.usage_path, workspace.pricing_path,
        workspace.catalog_path, workspace.scenarios_path,
        workspace.connectors_path, workspace.decisions_path,
    ):
        assert path.exists(), path


def test_workspace_config_round_trips(tmp_path: Path) -> None:
    init_workspace(tmp_path, project="demo", monthly_budget_usd=250.0)
    loaded = load_workspace(tmp_path)
    assert loaded.project == "demo"
    assert loaded.monthly_budget_usd == 250.0


def test_init_is_idempotent_and_preserves_usage(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path, project="demo")
    JsonlUsageStore(workspace.usage_path).append(UsageEvent("openai", "m", 1, 1, 0.0))
    init_workspace(tmp_path, project="demo")
    assert len(JsonlUsageStore(workspace.usage_path).read_all()) == 1


def test_loading_an_uninitialized_workspace_returns_defaults(tmp_path: Path) -> None:
    assert load_workspace(tmp_path).project == "default"


def test_a_damaged_config_falls_back_instead_of_crashing(tmp_path: Path) -> None:
    # An interrupted write must not lock the user out of their own dashboard.
    workspace = init_workspace(tmp_path, project="demo")
    workspace.config_path.write_text("{ truncated", encoding="utf-8")
    assert load_workspace(tmp_path).project == "default"


def test_a_damaged_config_does_not_touch_usage_data(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path, project="demo")
    JsonlUsageStore(workspace.usage_path).append(UsageEvent("openai", "m", 1, 1, 0.0))
    workspace.config_path.write_text("{ truncated", encoding="utf-8")
    load_workspace(tmp_path)
    assert len(JsonlUsageStore(workspace.usage_path).read_all()) == 1
