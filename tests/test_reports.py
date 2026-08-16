"""Coverage for usage aggregation, budget gates, and savings recommendations.

These are the numbers a user acts on, so the tests lean on the boundaries where
a report could quietly mislead: subscription activity that must never be priced
as API spend, forecasts built on too little data, and anomaly detection that
should stay silent until it has a baseline worth comparing against.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poliora.cost.budget import check_budget
from poliora.cost.recommendations import generate_recommendations
from poliora.cost.reports import build_usage_report
from poliora.cost.usage import UsageEvent

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def event(
    *,
    days_ago: int = 0,
    cost: float = 1.0,
    input_tokens: int = 1_000,
    output_tokens: int = 500,
    model: str = "gpt-5.6-sol",
    provider: str = "openai",
    operation: str = "chat",
    project: str = "default",
    user: str | None = None,
    metadata: dict | None = None,
    **kwargs: object,
) -> UsageEvent:
    """Build a usage event at a fixed offset from a stable clock."""
    return UsageEvent(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        operation=operation,
        project=project,
        user=user,
        timestamp=(NOW - timedelta(days=days_ago)).isoformat(),
        metadata=metadata or {},
        **kwargs,
    )


# --- empty reports ---------------------------------------------------------


def test_empty_report_has_no_requests() -> None:
    report = build_usage_report([])
    assert report.requests == 0
    assert report.cost_usd == 0.0
    assert report.period_start is None


def test_empty_report_declares_no_forecast_confidence() -> None:
    assert build_usage_report([]).forecast_confidence == "No data"


def test_empty_report_with_budget_reports_the_full_budget_remaining() -> None:
    report = build_usage_report([], monthly_budget_usd=500.0)
    assert report.budget_delta_usd == 500.0
    assert report.budget_used_pct == 0.0


def test_empty_report_without_budget_leaves_budget_fields_unset() -> None:
    report = build_usage_report([])
    assert report.monthly_budget_usd is None
    assert report.budget_used_pct is None


# --- aggregation -----------------------------------------------------------


def test_totals_sum_across_events() -> None:
    report = build_usage_report([event(cost=1.5), event(cost=2.5)])
    assert report.requests == 2
    assert report.cost_usd == 4.0


def test_token_totals_sum_across_events() -> None:
    report = build_usage_report(
        [event(input_tokens=1_000, output_tokens=200), event(input_tokens=3_000, output_tokens=800)]
    )
    assert report.input_tokens == 4_000
    assert report.output_tokens == 1_000
    assert report.total_tokens == 5_000


def test_cached_and_reasoning_tokens_are_tracked_separately() -> None:
    report = build_usage_report(
        [event(input_tokens=1_000, cached_input_tokens=400, reasoning_tokens=150)]
    )
    assert report.cached_input_tokens == 400
    assert report.reasoning_tokens == 150


def test_tool_cost_is_reported_separately_from_token_cost() -> None:
    report = build_usage_report([event(cost=1.0, tool_cost_usd=0.25)])
    assert report.tool_cost_usd == 0.25


def test_period_bounds_span_the_oldest_and_newest_event() -> None:
    report = build_usage_report([event(days_ago=10), event(days_ago=0)])
    assert report.period_start is not None and report.period_end is not None
    assert report.period_start < report.period_end


def test_observed_days_never_drops_below_one() -> None:
    # A single-day sample must not divide the projection by ~0.
    assert build_usage_report([event(), event()]).observed_days == 1.0


@pytest.mark.parametrize(
    ("span_days", "expected"),
    [(0, "Low"), (3, "Low"), (7, "Medium"), (14, "Medium"), (21, "High"), (45, "High")],
)
def test_forecast_confidence_scales_with_observed_history(span_days: int, expected: str) -> None:
    events = [event(days_ago=span_days), event(days_ago=0)]
    assert build_usage_report(events).forecast_confidence == expected


def test_forecast_confidence_reason_is_always_populated() -> None:
    assert build_usage_report([event()]).forecast_confidence_reason


# --- breakdowns ------------------------------------------------------------


def test_model_breakdown_is_ordered_by_spend() -> None:
    report = build_usage_report(
        [event(model="cheap", cost=1.0), event(model="pricey", cost=9.0)]
    )
    assert report.by_model[0].name == "openai/pricey"


def test_model_breakdown_reports_share_of_spend() -> None:
    report = build_usage_report([event(model="a", cost=75.0), event(model="b", cost=25.0)])
    assert report.by_model[0].share_pct == 75.0


def test_provider_breakdown_groups_across_models() -> None:
    report = build_usage_report(
        [
            event(provider="openai", model="a", cost=1.0),
            event(provider="openai", model="b", cost=1.0),
            event(provider="anthropic", model="c", cost=1.0),
        ]
    )
    names = {row.name for row in report.by_provider}
    assert names == {"openai", "anthropic"}


def test_operation_breakdown_separates_workflows() -> None:
    report = build_usage_report([event(operation="chat"), event(operation="summarize")])
    assert {row.name for row in report.by_operation} == {"chat", "summarize"}


def test_project_breakdown_separates_projects() -> None:
    report = build_usage_report([event(project="alpha"), event(project="beta")])
    assert {row.name for row in report.by_project} == {"alpha", "beta"}


def test_events_without_a_user_are_labelled_unassigned() -> None:
    report = build_usage_report([event(user=None)])
    assert report.by_user[0].name == "Unassigned"


def test_user_breakdown_separates_known_users() -> None:
    report = build_usage_report([event(user="ana"), event(user="ben")])
    assert {row.name for row in report.by_user} == {"ana", "ben"}


# --- subscription activity -------------------------------------------------


@pytest.mark.parametrize(
    "basis", ["chatgpt-subscription", "antigravity-subscription-activity"]
)
def test_subscription_activity_is_counted_but_not_billed(basis: str) -> None:
    # Subscription turns carry no token-denominated charge; inventing one would
    # be the single most damaging thing a cost tool could do.
    report = build_usage_report([event(cost=0.0, metadata={"billing_basis": basis})])
    assert report.non_dollar_requests == 1
    assert report.cost_usd == 0.0


def test_api_billed_events_are_not_counted_as_subscription_activity() -> None:
    report = build_usage_report([event(metadata={"billing_basis": "api-estimate"})])
    assert report.non_dollar_requests == 0


def test_mixed_subscription_and_api_usage_reports_both() -> None:
    report = build_usage_report(
        [event(cost=0.0, metadata={"billing_basis": "chatgpt-subscription"}), event(cost=5.0)]
    )
    assert report.requests == 2
    assert report.non_dollar_requests == 1
    assert report.cost_usd == 5.0


# --- daily spend and anomalies ---------------------------------------------


def test_daily_spend_groups_by_calendar_day() -> None:
    report = build_usage_report([event(days_ago=1), event(days_ago=1), event(days_ago=0)])
    assert len(report.daily_spend) == 2


def test_daily_spend_is_ordered_chronologically() -> None:
    report = build_usage_report([event(days_ago=0), event(days_ago=5)])
    dates = [row.date for row in report.daily_spend]
    assert dates == sorted(dates)


def test_anomaly_detection_stays_silent_without_enough_history() -> None:
    # Three quiet days then a spike: too little baseline to make a claim.
    events = [event(days_ago=day, cost=1.0) for day in (3, 2, 1)] + [event(days_ago=0, cost=99.0)]
    assert build_usage_report(events).spend_anomalies == []


def test_anomaly_detection_flags_a_spike_once_a_baseline_exists() -> None:
    events = [event(days_ago=day, cost=1.0) for day in range(10, 3, -1)]
    events.append(event(days_ago=0, cost=50.0))
    anomalies = build_usage_report(events).spend_anomalies
    assert len(anomalies) == 1
    assert anomalies[0].severity == "high"


def test_a_steady_run_rate_produces_no_anomalies() -> None:
    events = [event(days_ago=day, cost=2.0) for day in range(12, 0, -1)]
    assert build_usage_report(events).spend_anomalies == []


def test_a_modest_rise_is_not_flagged() -> None:
    events = [event(days_ago=day, cost=2.0) for day in range(10, 1, -1)]
    events.append(event(days_ago=0, cost=2.4))
    assert build_usage_report(events).spend_anomalies == []


# --- budget gates ----------------------------------------------------------


def test_budget_passes_when_projection_is_under_the_limit() -> None:
    report = build_usage_report([event(cost=1.0)])
    assert check_budget(report, limit_usd=10_000.0).passed


def test_budget_fails_when_projection_exceeds_the_limit() -> None:
    report = build_usage_report([event(cost=1_000.0)])
    assert not check_budget(report, limit_usd=1.0).passed


def test_budget_warns_before_it_fails() -> None:
    report = build_usage_report([event(cost=1.0)])
    check = check_budget(report, limit_usd=report.projected_monthly_usd * 1.05, warn_at_pct=80.0)
    assert check.passed
    assert "Watch this closely" in check.message


def test_budget_reports_remaining_headroom() -> None:
    report = build_usage_report([event(cost=1.0)])
    check = check_budget(report, limit_usd=report.projected_monthly_usd + 100)
    assert check.remaining_usd == pytest.approx(100.0, abs=0.01)


@pytest.mark.parametrize("limit", [0.0, -1.0, -100.0])
def test_budget_rejects_a_non_positive_limit(limit: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        check_budget(build_usage_report([event()]), limit_usd=limit)


@pytest.mark.parametrize("warn_at", [0.0, -5.0])
def test_budget_rejects_a_non_positive_warning_threshold(warn_at: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        check_budget(build_usage_report([event()]), limit_usd=10.0, warn_at_pct=warn_at)


def test_budget_check_serializes() -> None:
    check = check_budget(build_usage_report([event()]), limit_usd=100.0)
    assert set(check.to_dict()) == {
        "passed", "projected_monthly_usd", "limit_usd", "used_pct", "remaining_usd", "message"
    }


# --- recommendations -------------------------------------------------------


def test_no_usage_recommends_collecting_data_first() -> None:
    recommendations = generate_recommendations(build_usage_report([]))
    assert len(recommendations) == 1
    assert recommendations[0].priority == "high"


def test_recommendations_are_capped_to_a_readable_number() -> None:
    events = [event(days_ago=day, cost=50.0, input_tokens=90_000, output_tokens=90_000)
              for day in range(120)]
    report = build_usage_report(events, monthly_budget_usd=1.0)
    assert len(generate_recommendations(report)) <= 5


def test_a_dominant_model_is_flagged_for_benchmarking() -> None:
    events = [event(model="pricey", cost=95.0), event(model="cheap", cost=5.0)]
    titles = [item.title for item in generate_recommendations(build_usage_report(events))]
    assert "Benchmark the top cost model" in titles


def test_exceeding_budget_recommends_a_budget_gate() -> None:
    report = build_usage_report([event(cost=500.0)], monthly_budget_usd=1.0)
    titles = [item.title for item in generate_recommendations(report)]
    assert "Add a monthly AI budget gate" in titles


def test_output_heavy_usage_recommends_capping_verbosity() -> None:
    report = build_usage_report([event(input_tokens=1_000, output_tokens=9_000)])
    titles = [item.title for item in generate_recommendations(report)]
    assert "Cap verbose responses" in titles


def test_large_prompts_recommend_trimming_context() -> None:
    report = build_usage_report([event(input_tokens=50_000, output_tokens=100)])
    titles = [item.title for item in generate_recommendations(report)]
    assert "Trim prompt context" in titles


def test_modest_usage_still_yields_a_forward_looking_action() -> None:
    report = build_usage_report([event(cost=0.01, input_tokens=10, output_tokens=1)])
    recommendations = generate_recommendations(report)
    assert recommendations
    assert all(item.estimated_savings_pct >= 0 for item in recommendations)


def test_recommendation_savings_are_never_negative() -> None:
    report = build_usage_report([event(cost=100.0)], monthly_budget_usd=1.0)
    for item in generate_recommendations(report):
        assert item.estimated_monthly_savings_usd >= 0


def test_recommendation_serializes() -> None:
    item = generate_recommendations(build_usage_report([]))[0]
    assert {"title", "reason", "action", "priority"} <= set(item.to_dict())


# --- export ----------------------------------------------------------------


def test_report_writes_json(tmp_path: Path) -> None:
    target = build_usage_report([event()]).write_json(tmp_path / "report.json")
    assert target.exists() and target.read_text(encoding="utf-8").strip().startswith("{")


def test_report_writes_csv(tmp_path: Path) -> None:
    target = build_usage_report([event()]).write_csv(tmp_path / "models.csv")
    assert "openai/gpt-5.6-sol" in target.read_text(encoding="utf-8")


def test_report_export_creates_missing_directories(tmp_path: Path) -> None:
    target = build_usage_report([event()]).write_json(tmp_path / "a" / "b" / "report.json")
    assert target.exists()


def test_report_serializes_every_top_level_section() -> None:
    data = build_usage_report([event()]).to_dict()
    assert {"requests", "cost_usd", "by_model", "by_provider", "daily_spend"} <= set(data)
