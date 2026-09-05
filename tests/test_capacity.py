"""Coverage for subscription capacity forecasting.

A forecast is a claim about the future, which makes it the easiest thing in the
product to be confidently wrong about. These tests lean hardest on the cases
where being wrong would cost trust: an unknown ceiling must never be filled in
with a guess, a measured ceiling must be traceable to the refusal that produced
it, and provenance must survive all the way to the rendered sentence.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poliora.cost.capacity import (
    FIVE_HOUR,
    OBSERVED,
    PRIOR,
    UNKNOWN,
    WEEKLY,
    CapacityCache,
    CapacityCeiling,
    ThrottleEvent,
    burn_rate_per_hour,
    estimate_ceiling,
    forecast_runway,
    load_capacity_cache,
    load_status_line,
    peak_context,
    read_throttle_events,
    save_capacity_cache,
    save_status_line,
    window_consumption,
)
from poliora.cost.usage import UsageEvent

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def usage(*, minutes_ago: int = 0, tokens: int = 1_000) -> UsageEvent:
    """A usage event placed at a fixed offset from a stable clock."""
    half = tokens // 2
    return UsageEvent(
        provider="anthropic",
        model="claude-opus-5",
        input_tokens=half,
        output_tokens=tokens - half,
        cost_usd=0.0,
        timestamp=(NOW - timedelta(minutes=minutes_ago)).isoformat(),
    )


def throttle(*, minutes_ago: int = 0, window: str = FIVE_HOUR, resets_in_minutes: int | None = 30) -> ThrottleEvent:
    """A refusal recorded at a fixed offset from the same clock."""
    return ThrottleEvent(
        occurred_at=NOW - timedelta(minutes=minutes_ago),
        window=window,
        resets_at=NOW + timedelta(minutes=resets_in_minutes) if resets_in_minutes is not None else None,
    )


# --- window consumption ----------------------------------------------------


def test_consumption_counts_events_inside_the_window() -> None:
    events = [usage(minutes_ago=10, tokens=500), usage(minutes_ago=60, tokens=500)]
    assert window_consumption(events, window=FIVE_HOUR, ending_at=NOW) == 1_000


def test_consumption_excludes_events_older_than_the_window() -> None:
    events = [usage(minutes_ago=10, tokens=500), usage(minutes_ago=6 * 60, tokens=999_999)]
    assert window_consumption(events, window=FIVE_HOUR, ending_at=NOW) == 500


def test_consumption_excludes_events_after_the_window_end() -> None:
    events = [usage(minutes_ago=-30, tokens=999_999), usage(minutes_ago=10, tokens=500)]
    assert window_consumption(events, window=FIVE_HOUR, ending_at=NOW) == 500


def test_weekly_window_reaches_further_back_than_five_hours() -> None:
    events = [usage(minutes_ago=48 * 60, tokens=1_000)]
    assert window_consumption(events, window=FIVE_HOUR, ending_at=NOW) == 0
    assert window_consumption(events, window=WEEKLY, ending_at=NOW) == 1_000


def test_consumption_of_no_events_is_zero() -> None:
    assert window_consumption([], window=FIVE_HOUR, ending_at=NOW) == 0


def test_unparseable_timestamps_are_skipped_not_fatal() -> None:
    broken = UsageEvent("anthropic", "claude-opus-5", 100, 100, 0.0, timestamp="not-a-date")
    assert window_consumption([broken, usage(tokens=400)], window=FIVE_HOUR, ending_at=NOW) == 400


def test_unknown_window_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="window must be one of"):
        window_consumption([], window="fortnightly", ending_at=NOW)


# --- ceiling estimation ----------------------------------------------------


def test_ceiling_is_unknown_without_observations_or_prior() -> None:
    ceiling = estimate_ceiling([usage(tokens=500)], [], window=FIVE_HOUR)
    assert ceiling.basis == UNKNOWN
    assert ceiling.tokens is None
    assert ceiling.is_known is False


def test_ceiling_falls_back_to_a_supplied_prior() -> None:
    ceiling = estimate_ceiling([], [], window=FIVE_HOUR, prior_tokens=88_000)
    assert ceiling.basis == PRIOR
    assert ceiling.tokens == 88_000


def test_a_refusal_measures_the_ceiling_from_the_window_it_ended() -> None:
    # 30k tokens were consumed in the five hours before the refusal, so 30k is
    # a direct measurement of where this account's ceiling sits.
    events = [usage(minutes_ago=120, tokens=20_000), usage(minutes_ago=90, tokens=10_000)]
    ceiling = estimate_ceiling(events, [throttle(minutes_ago=60)], window=FIVE_HOUR)
    assert ceiling.basis == OBSERVED
    assert ceiling.tokens == 30_000
    assert ceiling.observations == 1


def test_an_observation_overrides_a_prior() -> None:
    events = [usage(minutes_ago=120, tokens=25_000)]
    ceiling = estimate_ceiling(
        events, [throttle(minutes_ago=60)], window=FIVE_HOUR, prior_tokens=999_999
    )
    assert ceiling.basis == OBSERVED
    assert ceiling.tokens == 25_000


def test_multiple_observations_are_combined_with_a_median() -> None:
    events = [
        usage(minutes_ago=400, tokens=10_000),
        usage(minutes_ago=250, tokens=30_000),
        usage(minutes_ago=100, tokens=20_000),
    ]
    throttles = [throttle(minutes_ago=390), throttle(minutes_ago=240), throttle(minutes_ago=90)]
    ceiling = estimate_ceiling(events, throttles, window=FIVE_HOUR)
    assert ceiling.basis == OBSERVED
    assert ceiling.observations == 3


def test_refusals_for_another_window_do_not_calibrate_this_one() -> None:
    events = [usage(minutes_ago=120, tokens=30_000)]
    ceiling = estimate_ceiling(events, [throttle(minutes_ago=60, window=WEEKLY)], window=FIVE_HOUR)
    assert ceiling.basis == UNKNOWN


def test_a_refusal_with_no_preceding_usage_is_ignored() -> None:
    # A zero-token window cannot be a ceiling; treating it as one would render
    # the account permanently "exhausted".
    ceiling = estimate_ceiling([], [throttle(minutes_ago=60)], window=FIVE_HOUR)
    assert ceiling.basis == UNKNOWN


@pytest.mark.parametrize(
    ("basis", "phrase"),
    [(OBSERVED, "Calibrated from"), (PRIOR, "not yet confirmed"), (UNKNOWN, "No ceiling is known")],
)
def test_ceiling_explains_its_own_provenance(basis: str, phrase: str) -> None:
    events = [usage(minutes_ago=120, tokens=30_000)]
    if basis == OBSERVED:
        ceiling = estimate_ceiling(events, [throttle(minutes_ago=60)], window=FIVE_HOUR)
    elif basis == PRIOR:
        ceiling = estimate_ceiling([], [], window=FIVE_HOUR, prior_tokens=50_000)
    else:
        ceiling = estimate_ceiling([], [], window=FIVE_HOUR)
    assert phrase in ceiling.describe()


def test_ceiling_serializes_with_provenance() -> None:
    data = estimate_ceiling([], [], window=FIVE_HOUR, prior_tokens=10).to_dict()
    assert {"window", "tokens", "basis", "observations", "is_known", "description"} <= set(data)


# --- burn rate -------------------------------------------------------------


def test_burn_rate_is_measured_over_the_recent_lookback() -> None:
    events = [usage(minutes_ago=30, tokens=6_000)]
    rate = burn_rate_per_hour(events, now=NOW, lookback=timedelta(minutes=60))
    assert rate == pytest.approx(6_000.0)


def test_burn_rate_ignores_work_older_than_the_lookback() -> None:
    events = [usage(minutes_ago=180, tokens=999_999)]
    assert burn_rate_per_hour(events, now=NOW, lookback=timedelta(minutes=60)) == 0.0


def test_burn_rate_of_an_idle_session_is_zero() -> None:
    assert burn_rate_per_hour([], now=NOW) == 0.0


def test_a_shorter_lookback_reacts_faster_to_a_burst() -> None:
    events = [usage(minutes_ago=5, tokens=5_000)]
    fast = burn_rate_per_hour(events, now=NOW, lookback=timedelta(minutes=15))
    slow = burn_rate_per_hour(events, now=NOW, lookback=timedelta(minutes=60))
    assert fast > slow


# --- forecasting -----------------------------------------------------------


def test_forecast_reports_usage_without_inventing_a_wall_time() -> None:
    # The single most important guarantee: no ceiling means no prediction.
    forecast = forecast_runway([usage(minutes_ago=10, tokens=5_000)], [], now=NOW)
    assert forecast.used_tokens == 5_000
    assert forecast.exhausted_at is None
    assert forecast.used_pct is None
    assert forecast.remaining_tokens is None


def test_headline_says_plainly_when_no_ceiling_is_known() -> None:
    headline = forecast_runway([usage(tokens=5_000)], [], now=NOW).headline(now=NOW)
    assert "not guessing" in headline


def test_forecast_projects_exhaustion_from_burn_and_ceiling() -> None:
    # 10k used of a 40k ceiling, burning 30k/hour -> 30k left -> about an hour.
    events = [usage(minutes_ago=30, tokens=10_000), usage(minutes_ago=45, tokens=5_000)]
    forecast = forecast_runway(events, [], now=NOW, prior_tokens=40_000)
    assert forecast.ceiling.basis == PRIOR
    assert forecast.exhausted_at is not None
    assert forecast.exhausted_at > NOW


def test_idle_session_has_no_exhaustion_time_even_with_a_ceiling() -> None:
    events = [usage(minutes_ago=240, tokens=10_000)]
    forecast = forecast_runway(events, [], now=NOW, prior_tokens=40_000)
    assert forecast.burn_tokens_per_hour == 0.0
    assert forecast.exhausted_at is None
    assert "Nothing is being consumed" in forecast.headline(now=NOW)


def test_used_percentage_is_reported_against_a_known_ceiling() -> None:
    events = [usage(minutes_ago=10, tokens=10_000)]
    forecast = forecast_runway(events, [], now=NOW, prior_tokens=40_000)
    assert forecast.used_pct == 25.0
    assert forecast.remaining_tokens == 30_000


def test_remaining_tokens_never_goes_negative() -> None:
    events = [usage(minutes_ago=10, tokens=90_000)]
    forecast = forecast_runway(events, [], now=NOW, prior_tokens=40_000)
    assert forecast.remaining_tokens == 0


def test_time_remaining_never_goes_negative() -> None:
    events = [usage(minutes_ago=10, tokens=90_000)]
    forecast = forecast_runway(events, [], now=NOW, prior_tokens=40_000)
    remaining = forecast.time_remaining(now=NOW)
    assert remaining is None or remaining >= timedelta(0)


def test_forecast_surfaces_a_recorded_future_reset() -> None:
    forecast = forecast_runway(
        [usage(tokens=1_000)], [throttle(minutes_ago=60, resets_in_minutes=45)], now=NOW
    )
    assert forecast.resets_at == NOW + timedelta(minutes=45)


def test_past_resets_are_not_offered_as_upcoming() -> None:
    forecast = forecast_runway(
        [usage(tokens=1_000)], [throttle(minutes_ago=600, resets_in_minutes=-120)], now=NOW
    )
    assert forecast.resets_at is None


def test_weekly_forecast_uses_the_weekly_window() -> None:
    events = [usage(minutes_ago=48 * 60, tokens=7_000)]
    weekly = forecast_runway(events, [], window=WEEKLY, now=NOW)
    five_hour = forecast_runway(events, [], window=FIVE_HOUR, now=NOW)
    assert weekly.used_tokens == 7_000
    assert five_hour.used_tokens == 0


def test_forecast_serializes_completely() -> None:
    data = forecast_runway([usage(tokens=1_000)], [], now=NOW, prior_tokens=10_000).to_dict()
    assert {"window", "used_tokens", "used_pct", "ceiling", "burn_tokens_per_hour",
            "exhausted_at", "seconds_remaining", "headline"} <= set(data)


def test_headline_includes_a_human_readable_duration() -> None:
    events = [usage(minutes_ago=30, tokens=10_000)]
    headline = forecast_runway(events, [], now=NOW, prior_tokens=40_000).headline(now=NOW)
    assert "left at the current pace" in headline


# --- reading refusals from disk --------------------------------------------


def write_session(home: Path, *records: dict) -> None:
    directory = home / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records), encoding="utf-8"
    )


def rejection_record(*, window: str = FIVE_HOUR, resets_at: int | None = 1788568200) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-09-05T00:21:45.030Z",
        "isApiErrorMessage": True,
        "apiErrorStatus": 429,
        "error": "rate_limit",
        "quotaLimits": {
            "status": "rejected",
            "resetsAt": resets_at,
            "rateLimitType": window,
            "upgradePaths": ["upgrade_plan"],
        },
    }


def test_no_claude_directory_yields_no_refusals(tmp_path: Path) -> None:
    assert read_throttle_events(home=tmp_path) == []


def test_a_recorded_refusal_is_read(tmp_path: Path) -> None:
    write_session(tmp_path, rejection_record())
    events = read_throttle_events(home=tmp_path)
    assert len(events) == 1
    assert events[0].window == FIVE_HOUR
    assert events[0].resets_at == datetime.fromtimestamp(1788568200, tz=timezone.utc)


def test_weekly_refusals_are_read_too(tmp_path: Path) -> None:
    write_session(tmp_path, rejection_record(window=WEEKLY))
    assert read_throttle_events(home=tmp_path)[0].window == WEEKLY


def test_ordinary_turns_are_not_mistaken_for_refusals(tmp_path: Path) -> None:
    # Only 3 of 3,271 real assistant records carry quotaLimits; the rest must
    # not be read as ceiling hits.
    write_session(
        tmp_path,
        {"type": "assistant", "timestamp": "2026-09-05T00:00:00Z", "message": {"model": "claude-opus-5"}},
        rejection_record(),
    )
    assert len(read_throttle_events(home=tmp_path)) == 1


def test_an_allowed_quota_block_is_not_a_refusal(tmp_path: Path) -> None:
    allowed = rejection_record()
    allowed["quotaLimits"]["status"] = "allowed"
    write_session(tmp_path, allowed)
    assert read_throttle_events(home=tmp_path) == []


def test_an_unrecognized_window_type_is_ignored(tmp_path: Path) -> None:
    write_session(tmp_path, rejection_record(window="hourly"))
    assert read_throttle_events(home=tmp_path) == []


def test_a_refusal_without_a_reset_timestamp_still_counts(tmp_path: Path) -> None:
    write_session(tmp_path, rejection_record(resets_at=None))
    events = read_throttle_events(home=tmp_path)
    assert len(events) == 1 and events[0].resets_at is None


def test_malformed_lines_do_not_break_the_read(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "session.jsonl").write_text(
        json.dumps(rejection_record()) + "\n{\"truncated\": ", encoding="utf-8"
    )
    assert len(read_throttle_events(home=tmp_path)) == 1


def test_refusals_are_returned_oldest_first(tmp_path: Path) -> None:
    early = rejection_record()
    early["timestamp"] = "2026-09-01T00:00:00Z"
    late = rejection_record()
    late["timestamp"] = "2026-09-05T00:00:00Z"
    write_session(tmp_path, late, early)
    events = read_throttle_events(home=tmp_path)
    assert events[0].occurred_at < events[1].occurred_at


def test_throttle_event_serializes(tmp_path: Path) -> None:
    write_session(tmp_path, rejection_record())
    data = read_throttle_events(home=tmp_path)[0].to_dict()
    assert set(data) == {"occurred_at", "window", "resets_at"}


# --- ceiling cache ---------------------------------------------------------


def a_cache(*, tokens: int | None = 50_000, basis: str = OBSERVED, age_hours: int = 0) -> CapacityCache:
    return CapacityCache(
        ceilings={FIVE_HOUR: CapacityCeiling(FIVE_HOUR, tokens, basis, observations=2)},
        computed_at=NOW - timedelta(hours=age_hours),
    )


def test_cache_round_trips(tmp_path: Path) -> None:
    path = save_capacity_cache(tmp_path / "capacity.json", a_cache())
    loaded = load_capacity_cache(path)
    assert loaded is not None
    ceiling = loaded.ceilings[FIVE_HOUR]
    assert ceiling.tokens == 50_000
    assert ceiling.basis == OBSERVED
    assert ceiling.observations == 2


def test_missing_cache_returns_none(tmp_path: Path) -> None:
    assert load_capacity_cache(tmp_path / "absent.json") is None


@pytest.mark.parametrize("content", ["{ truncated", "[]", '{"ceilings": 5}', '{"computed_at": "nope"}'])
def test_a_damaged_cache_degrades_to_none(tmp_path: Path, content: str) -> None:
    # Worst case must be paying for one rebuild, never a crash.
    target = tmp_path / "capacity.json"
    target.write_text(content, encoding="utf-8")
    assert load_capacity_cache(target) is None


def test_cache_ignores_unknown_window_names(tmp_path: Path) -> None:
    target = tmp_path / "capacity.json"
    target.write_text(
        json.dumps({"computed_at": NOW.isoformat(), "ceilings": {"fortnightly": {"tokens": 5}}}),
        encoding="utf-8",
    )
    loaded = load_capacity_cache(target)
    assert loaded is not None and loaded.ceilings == {}


def test_cache_reports_its_age() -> None:
    assert a_cache(age_hours=3).age(now=NOW) == timedelta(hours=3)


def test_cache_write_creates_missing_directories(tmp_path: Path) -> None:
    path = save_capacity_cache(tmp_path / "deep" / "nested" / "capacity.json", a_cache())
    assert path.exists()


def test_cache_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    save_capacity_cache(tmp_path / "capacity.json", a_cache())
    assert list(tmp_path.glob(".*tmp")) == []


def test_a_supplied_ceiling_skips_derivation() -> None:
    # The fast path: pass a cached ceiling and no refusals need replaying.
    supplied = CapacityCeiling(FIVE_HOUR, 40_000, OBSERVED, observations=3)
    forecast = forecast_runway([usage(minutes_ago=10, tokens=10_000)], [], now=NOW, ceiling=supplied)
    assert forecast.ceiling is supplied
    assert forecast.used_pct == 25.0


def test_a_supplied_ceiling_wins_over_a_prior() -> None:
    supplied = CapacityCeiling(FIVE_HOUR, 40_000, OBSERVED, observations=1)
    forecast = forecast_runway(
        [usage(tokens=1_000)], [], now=NOW, prior_tokens=999_999, ceiling=supplied
    )
    assert forecast.ceiling.tokens == 40_000


def test_a_cached_unknown_ceiling_still_refuses_to_guess() -> None:
    supplied = CapacityCeiling(FIVE_HOUR, None, UNKNOWN)
    forecast = forecast_runway([usage(tokens=5_000)], [], now=NOW, ceiling=supplied)
    assert forecast.exhausted_at is None
    assert "not guessing" in forecast.headline(now=NOW)


# --- rendered status line cache --------------------------------------------


def test_status_line_round_trips(tmp_path: Path) -> None:
    path = save_status_line(tmp_path / "statusline.json", "Poliora 12% used", now=NOW)
    assert load_status_line(path, now=NOW) == "Poliora 12% used"


def test_a_fresh_status_line_is_served(tmp_path: Path) -> None:
    path = save_status_line(tmp_path / "s.json", "fresh", now=NOW)
    assert load_status_line(path, max_age=timedelta(seconds=60), now=NOW + timedelta(seconds=30)) == "fresh"


def test_a_stale_status_line_is_refused(tmp_path: Path) -> None:
    # Refusing forces a recompute; serving it forever would freeze the gauge.
    path = save_status_line(tmp_path / "s.json", "old", now=NOW)
    assert load_status_line(path, max_age=timedelta(seconds=60), now=NOW + timedelta(seconds=90)) is None


def test_missing_status_line_returns_none(tmp_path: Path) -> None:
    assert load_status_line(tmp_path / "absent.json") is None


@pytest.mark.parametrize(
    "content",
    [
        "{ truncated",
        "[]",
        '{"text": "x"}',
        '{"rendered_at": "nope", "text": "x"}',
        '{"text": "", "rendered_at": "2026-09-05T12:00:00Z"}',
    ],
)
def test_a_damaged_status_line_degrades_to_none(tmp_path: Path, content: str) -> None:
    target = tmp_path / "s.json"
    target.write_text(content, encoding="utf-8")
    assert load_status_line(target, now=NOW) is None


def test_status_line_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    save_status_line(tmp_path / "s.json", "text", now=NOW)
    assert list(tmp_path.glob(".*tmp")) == []


def test_status_line_write_creates_missing_directories(tmp_path: Path) -> None:
    path = save_status_line(tmp_path / "a" / "b" / "s.json", "text", now=NOW)
    assert path.exists()


# --- history-relative context ----------------------------------------------


def busy_history(hours: int, tokens_per_hour: int) -> list[UsageEvent]:
    """One event per hour going back ``hours``, for percentile comparisons."""
    return [usage(minutes_ago=60 * h, tokens=tokens_per_hour) for h in range(1, hours + 1)]


def test_no_history_is_not_meaningful() -> None:
    # The first run must say it cannot compare yet, not invent a comparison.
    context = peak_context([], now=NOW)
    assert context.is_meaningful is False
    assert "Not enough history" in context.describe()


def test_a_trickle_of_history_is_not_meaningful() -> None:
    context = peak_context(busy_history(3, 1_000), now=NOW)
    assert context.is_meaningful is False


def test_enough_history_enables_a_comparison() -> None:
    context = peak_context(busy_history(200, 1_000), now=NOW)
    assert context.is_meaningful is True
    assert context.percentile is not None


def test_a_quiet_window_ranks_low() -> None:
    events = busy_history(200, 10_000) + [usage(minutes_ago=1, tokens=1)]
    context = peak_context(events, now=NOW)
    assert context.percentile is not None and context.percentile < 50


def test_the_busiest_window_is_reported() -> None:
    events = busy_history(100, 1_000) + [usage(minutes_ago=30, tokens=500_000)]
    context = peak_context(events, now=NOW)
    assert context.busiest_tokens >= 500_000


def test_a_median_window_is_reported() -> None:
    context = peak_context(busy_history(200, 4_000), now=NOW)
    assert context.median_tokens > 0


def test_the_description_cites_the_lookback_period() -> None:
    context = peak_context(busy_history(200, 1_000), now=NOW, lookback_days=14)
    assert "14 days" in context.describe()


def test_context_works_without_any_ceiling() -> None:
    # The whole point: useful on a machine that has never been refused.
    context = peak_context(busy_history(200, 5_000), now=NOW)
    assert context.is_meaningful
    assert "heavier than" in context.describe()


def test_context_serializes() -> None:
    data = peak_context(busy_history(200, 1_000), now=NOW).to_dict()
    assert {"window", "current_tokens", "busiest_tokens", "percentile",
            "samples", "is_meaningful", "description"} <= set(data)


def test_weekly_context_uses_the_weekly_window() -> None:
    events = busy_history(400, 1_000)
    weekly = peak_context(events, window=WEEKLY, now=NOW)
    five_hour = peak_context(events, window=FIVE_HOUR, now=NOW)
    assert weekly.current_tokens > five_hour.current_tokens


def test_context_ignores_work_beyond_the_lookback() -> None:
    ancient = [usage(minutes_ago=60 * 24 * 300, tokens=9_000_000)]
    context = peak_context(busy_history(200, 1_000) + ancient, now=NOW, lookback_days=30)
    assert context.busiest_tokens < 9_000_000
