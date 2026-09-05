"""Coverage for turning measurements into suggestions.

Advice is where a measurement tool can quietly become a liar: it is tempting to
always have something to say. These tests pin the opposite property -- silence
when the evidence is thin -- as hard as they pin the suggestions themselves,
because a manufactured insight costs more trust than a blank screen.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from poliora.cost.advice import NOTE, NOW, SOON, Suggestion, build_advice
from poliora.cost.capacity import (
    OBSERVED,
    UNKNOWN,
    CapacityCeiling,
    PeakContext,
    RunwayForecast,
)
from poliora.cost.local_usage import DetectedPlan, LocalUsageScan
from poliora.cost.workflows import WorkflowReport, WorkflowUsage

CLOCK = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def forecast(
    *,
    used: int = 10_000,
    ceiling: int | None = 100_000,
    basis: str = OBSERVED,
    burn: float = 1_000.0,
    exhausted_in_minutes: int | None = None,
    resets_in_minutes: int | None = None,
) -> RunwayForecast:
    return RunwayForecast(
        window="five_hour",
        window_started_at=CLOCK - timedelta(hours=5),
        used_tokens=used,
        ceiling=CapacityCeiling("five_hour", ceiling, basis, observations=2 if ceiling else 0),
        burn_tokens_per_hour=burn,
        exhausted_at=CLOCK + timedelta(minutes=exhausted_in_minutes) if exhausted_in_minutes else None,
        resets_at=CLOCK + timedelta(minutes=resets_in_minutes) if resets_in_minutes else None,
    )


def context(*, percentile: float | None = 50.0, samples: int = 100) -> PeakContext:
    return PeakContext(
        window="five_hour",
        current_tokens=10_000,
        busiest_tokens=50_000,
        median_tokens=8_000,
        percentile=percentile,
        samples=samples,
        lookback_days=30,
    )


def codex(*, available: bool = True, quota_pct: float | None = 5.0) -> LocalUsageScan:
    return LocalUsageScan(
        tool="codex",
        display_name="Codex CLI",
        available=available,
        plan=DetectedPlan(tool="codex", plan_type="plus", quota_used_pct=quota_pct),
    )


def workflows(*, top_share: float = 70.0, projects: int = 3) -> WorkflowReport:
    items = [WorkflowUsage("hog", 100, 700_000, top_share)]
    for index in range(1, projects):
        items.append(WorkflowUsage(f"p{index}", 10, 10_000, (100 - top_share) / max(projects - 1, 1)))
    return WorkflowReport(projects=tuple(items), total_tokens=1_000_000, period_days=30)


# --- silence when evidence is thin -----------------------------------------


def test_a_quiet_day_produces_no_suggestions() -> None:
    assert build_advice(forecast=forecast(used=1_000)) == []


def test_no_ceiling_and_no_history_stays_silent() -> None:
    # The cold-start case: nothing measured means nothing to say.
    result = build_advice(
        forecast=forecast(ceiling=None, basis=UNKNOWN),
        context=PeakContext("five_hour", 0, 0, 0, None, 0, 30),
    )
    assert result == []


def test_thin_history_does_not_produce_a_peak_warning() -> None:
    result = build_advice(
        forecast=forecast(ceiling=None, basis=UNKNOWN),
        context=context(percentile=99.0, samples=3),
    )
    assert result == []


def test_an_absent_second_plan_produces_no_arbitrage_advice() -> None:
    result = build_advice(forecast=forecast(used=90_000), other_plan=codex(available=False))
    assert not any("spare capacity" in item.headline for item in result)


def test_a_second_plan_without_quota_data_is_skipped() -> None:
    result = build_advice(forecast=forecast(used=90_000), other_plan=codex(quota_pct=None))
    assert not any("spare capacity" in item.headline for item in result)


def test_a_busy_second_plan_is_not_offered_as_spare() -> None:
    result = build_advice(forecast=forecast(used=90_000), other_plan=codex(quota_pct=85.0))
    assert not any("spare capacity" in item.headline for item in result)


def test_a_diffuse_project_mix_produces_no_attribution_advice() -> None:
    result = build_advice(forecast=forecast(), workflows=workflows(top_share=20.0))
    assert not any(item.urgency == NOTE for item in result)


def test_a_single_project_is_not_called_dominant() -> None:
    # With one project the share is trivially 100%; that is not a finding.
    single = WorkflowReport(
        projects=(WorkflowUsage("only", 10, 1_000, 100.0),), total_tokens=1_000, period_days=30
    )
    result = build_advice(forecast=forecast(), workflows=single)
    assert not any(item.urgency == NOTE for item in result)


# --- urgent capacity warnings ----------------------------------------------


def test_a_spent_window_is_reported_now() -> None:
    result = build_advice(forecast=forecast(used=200_000, ceiling=100_000))
    assert result and result[0].urgency == NOW
    assert "spent" in result[0].headline


def test_a_spent_window_names_the_reset_time_when_known() -> None:
    result = build_advice(forecast=forecast(used=200_000, resets_in_minutes=45))
    assert "Wait for the reset" in result[0].action


def test_a_spent_window_without_a_reset_still_advises() -> None:
    result = build_advice(forecast=forecast(used=200_000, resets_in_minutes=None))
    assert result[0].action


def test_an_imminent_wall_is_reported_now() -> None:
    result = build_advice(forecast=forecast(used=90_000, exhausted_in_minutes=30))
    assert result[0].urgency == NOW
    assert "minutes of capacity left" in result[0].headline


def test_a_distant_wall_is_not_urgent() -> None:
    result = build_advice(forecast=forecast(used=20_000, exhausted_in_minutes=600))
    assert not any(item.urgency == NOW for item in result)


def test_an_unusual_window_warns_even_without_a_ceiling() -> None:
    # The cold-start value: own history substitutes for an unknown limit.
    result = build_advice(
        forecast=forecast(ceiling=None, basis=UNKNOWN),
        context=context(percentile=92.0),
    )
    assert result and result[0].urgency == SOON
    assert "heaviest" in result[0].headline


def test_a_typical_window_does_not_warn() -> None:
    result = build_advice(
        forecast=forecast(ceiling=None, basis=UNKNOWN), context=context(percentile=40.0)
    )
    assert result == []


def test_a_known_ceiling_takes_precedence_over_history() -> None:
    # A measured limit is better evidence than a percentile; do not say both.
    result = build_advice(
        forecast=forecast(used=95_000, exhausted_in_minutes=10),
        context=context(percentile=99.0),
    )
    assert sum(1 for item in result if item.urgency == NOW) == 1


# --- cross-plan arbitrage --------------------------------------------------


def test_spare_capacity_elsewhere_is_surfaced() -> None:
    result = build_advice(forecast=forecast(used=80_000), other_plan=codex(quota_pct=5.0))
    assert any("spare capacity" in item.headline for item in result)


def test_arbitrage_cites_both_utilizations() -> None:
    result = build_advice(forecast=forecast(used=80_000), other_plan=codex(quota_pct=5.0))
    advice = next(item for item in result if "spare capacity" in item.headline)
    assert "80%" in advice.because and "5%" in advice.because


def test_arbitrage_is_not_offered_when_this_plan_is_comfortable() -> None:
    result = build_advice(forecast=forecast(used=10_000), other_plan=codex(quota_pct=2.0))
    assert not any("spare capacity" in item.headline for item in result)


def test_arbitrage_needs_a_known_ceiling_to_compare_against() -> None:
    result = build_advice(
        forecast=forecast(ceiling=None, basis=UNKNOWN), other_plan=codex(quota_pct=2.0)
    )
    assert not any("spare capacity" in item.headline for item in result)


# --- attribution -----------------------------------------------------------


def test_a_dominant_project_is_named() -> None:
    result = build_advice(forecast=forecast(), workflows=workflows(top_share=74.0))
    note = next(item for item in result if item.urgency == NOTE)
    assert "hog" in note.headline
    assert "74%" in note.because


# --- ordering and shape ----------------------------------------------------


def test_urgent_advice_comes_first() -> None:
    result = build_advice(
        forecast=forecast(used=200_000, resets_in_minutes=30),
        workflows=workflows(top_share=80.0),
        other_plan=codex(quota_pct=3.0),
    )
    assert [item.urgency for item in result] == sorted(
        [item.urgency for item in result], key=lambda u: {NOW: 0, SOON: 1, NOTE: 2}[u]
    )


def test_every_suggestion_carries_evidence_and_an_action() -> None:
    result = build_advice(
        forecast=forecast(used=200_000, resets_in_minutes=30),
        workflows=workflows(top_share=80.0),
        other_plan=codex(quota_pct=3.0),
    )
    assert result
    for item in result:
        assert item.because and item.action and item.headline


def test_suggestions_serialize() -> None:
    result = build_advice(forecast=forecast(used=200_000))
    assert set(result[0].to_dict()) == {"urgency", "headline", "because", "action"}


@pytest.mark.parametrize("urgency", [NOW, SOON, NOTE])
def test_urgency_levels_are_distinct(urgency: str) -> None:
    assert isinstance(Suggestion(urgency, "h", "b", "a").urgency, str)


def test_advice_works_with_only_a_forecast() -> None:
    # Every other input is optional; missing data must never raise.
    assert isinstance(build_advice(forecast=forecast()), list)
