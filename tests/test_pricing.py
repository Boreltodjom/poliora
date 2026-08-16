"""Coverage for the time-scoped pricing registry and the public rate catalog.

Pricing is the foundation every other number in Poliora rests on: a wrong rate
does not surface as an error, it surfaces as a confident recommendation to
switch to the wrong model. These tests pin both the mechanics (schedule
resolution, serialization, provenance) and the catalog's own integrity.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from poliora.cost.pricing import (
    CARRIED_FORWARD,
    CONFIDENCE_LEVELS,
    PRICING_VERIFIED_ON,
    VERIFIED,
    ModelPricing,
    PricingRegistry,
    default_pricing,
    estimate_cost_usd,
    make_pricing_key,
)

AUGUST = datetime(2026, 8, 20, tzinfo=timezone.utc)
SEPTEMBER = datetime(2026, 9, 20, tzinfo=timezone.utc)


# --- key normalization -----------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-5.6-sol"),
        ("OpenAI", "GPT-5.6-Sol"),
        ("  openai  ", "  gpt-5.6-sol  "),
        ("OPENAI", "gpt-5.6-SOL"),
    ],
)
def test_pricing_key_normalizes_case_and_padding(provider: str, model: str) -> None:
    assert make_pricing_key(provider, model) == "openai:gpt-5.6-sol"


def test_lookup_is_case_insensitive() -> None:
    registry = PricingRegistry()
    assert registry.get("ANTHROPIC", "Claude-Opus-5") is not None


# --- cost arithmetic -------------------------------------------------------


def test_estimate_splits_cached_and_regular_input() -> None:
    pricing = ModelPricing("openai", "demo", 10.0, 20.0, cached_input_per_1m=1.0)
    # 600k regular @ $10 + 400k cached @ $1 + 500k output @ $20
    assert pricing.estimate(1_000_000, 500_000, cached_input_tokens=400_000) == 16.4


def test_estimate_falls_back_to_input_rate_when_no_cached_rate_is_known() -> None:
    pricing = ModelPricing("openai", "demo", 10.0, 20.0)
    assert pricing.estimate(1_000_000, 0, cached_input_tokens=500_000) == 10.0


def test_estimate_clamps_cached_tokens_to_input_tokens() -> None:
    pricing = ModelPricing("openai", "demo", 10.0, 20.0, cached_input_per_1m=1.0)
    # Claiming more cached than input must not produce negative regular input.
    assert pricing.estimate(100_000, 0, cached_input_tokens=999_999) == 0.1


def test_estimate_treats_negative_token_counts_as_zero() -> None:
    pricing = ModelPricing("openai", "demo", 10.0, 20.0)
    assert pricing.estimate(-5_000, 0) == 0.0


def test_estimate_of_zero_usage_is_zero() -> None:
    assert ModelPricing("openai", "demo", 10.0, 20.0).estimate(0, 0) == 0.0


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "expected"),
    [
        (1_000_000, 0, 5.0),
        (0, 1_000_000, 25.0),
        (1_000_000, 1_000_000, 30.0),
        (500_000, 500_000, 15.0),
        (1_000, 1_000, 0.03),
    ],
)
def test_opus_5_arithmetic(input_tokens: int, output_tokens: int, expected: float) -> None:
    registry = PricingRegistry()
    assert registry.estimate("anthropic", "claude-opus-5", input_tokens, output_tokens) == expected


def test_estimate_cost_usd_helper_matches_registry() -> None:
    registry = PricingRegistry()
    direct = registry.estimate("anthropic", "claude-opus-5", 1_000_000, 0)
    assert estimate_cost_usd("anthropic", "claude-opus-5", 1_000_000, 0) == direct


def test_unknown_model_estimates_zero() -> None:
    assert PricingRegistry().estimate("nobody", "nothing", 1_000_000, 1_000_000) == 0.0


# --- schedule resolution ---------------------------------------------------


def test_schedule_resolves_by_moment() -> None:
    registry = PricingRegistry()
    intro = registry.get("anthropic", "claude-sonnet-5", at=AUGUST)
    standard = registry.get("anthropic", "claude-sonnet-5", at=SEPTEMBER)
    assert intro is not None and standard is not None
    assert intro.input_per_1m == 2.00
    assert standard.input_per_1m == 3.00


def test_schedule_boundary_is_inclusive_of_the_successor() -> None:
    registry = PricingRegistry()
    boundary = datetime(2026, 9, 1, tzinfo=timezone.utc)
    pricing = registry.get("anthropic", "claude-sonnet-5", at=boundary)
    assert pricing is not None and pricing.input_per_1m == 3.00


def test_moment_one_second_before_boundary_uses_the_earlier_rate() -> None:
    registry = PricingRegistry()
    just_before = datetime(2026, 8, 31, 23, 59, 59, tzinfo=timezone.utc)
    pricing = registry.get("anthropic", "claude-sonnet-5", at=just_before)
    assert pricing is not None and pricing.input_per_1m == 2.00


def test_naive_datetimes_are_treated_as_utc() -> None:
    registry = PricingRegistry()
    naive = datetime(2026, 8, 20)
    aware = datetime(2026, 8, 20, tzinfo=timezone.utc)
    assert registry.get("anthropic", "claude-sonnet-5", at=naive) == registry.get(
        "anthropic", "claude-sonnet-5", at=aware
    )


def test_covers_respects_an_open_ended_window() -> None:
    pricing = ModelPricing("openai", "demo", 1.0, 1.0)
    assert pricing.covers(datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert pricing.covers(datetime(2099, 1, 1, tzinfo=timezone.utc))


def test_covers_respects_a_closed_window() -> None:
    pricing = ModelPricing(
        "openai", "demo", 1.0, 1.0, effective_from="2026-01-01", effective_until="2026-06-01"
    )
    assert not pricing.covers(datetime(2025, 12, 31, tzinfo=timezone.utc))
    assert pricing.covers(datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert not pricing.covers(datetime(2026, 6, 1, tzinfo=timezone.utc))


def test_schedules_are_returned_oldest_first() -> None:
    schedules = PricingRegistry().schedules("anthropic", "claude-sonnet-5")
    assert len(schedules) == 2
    assert schedules[0].input_per_1m == 2.00
    assert schedules[1].effective_from == "2026-09-01"


def test_schedules_of_unknown_model_is_empty() -> None:
    assert PricingRegistry().schedules("nobody", "nothing") == []


def test_adding_the_same_window_twice_replaces_rather_than_duplicates() -> None:
    registry = PricingRegistry([])
    registry.add(ModelPricing("openai", "demo", 1.0, 2.0))
    registry.add(ModelPricing("openai", "demo", 9.0, 9.0))
    schedules = registry.schedules("openai", "demo")
    assert len(schedules) == 1
    assert schedules[0].input_per_1m == 9.0


def test_adding_a_distinct_window_appends() -> None:
    registry = PricingRegistry([])
    registry.add(ModelPricing("openai", "demo", 1.0, 2.0, effective_until="2026-06-01"))
    registry.add(ModelPricing("openai", "demo", 3.0, 4.0, effective_from="2026-06-01"))
    assert len(registry.schedules("openai", "demo")) == 2


def test_estimate_uses_the_schedule_for_the_supplied_moment() -> None:
    registry = PricingRegistry()
    assert registry.estimate("anthropic", "claude-sonnet-5", 1_000_000, 0, at=AUGUST) == 2.00
    assert registry.estimate("anthropic", "claude-sonnet-5", 1_000_000, 0, at=SEPTEMBER) == 3.00


# --- pricing gaps ----------------------------------------------------------


def test_explain_returns_none_for_a_priced_model() -> None:
    assert PricingRegistry().explain("anthropic", "claude-opus-5") is None


def test_explain_reports_an_unknown_model() -> None:
    gap = PricingRegistry().explain("openai", "not-a-model")
    assert gap is not None
    assert gap.provider == "openai"
    assert "No rate is known" in gap.reason


def test_explain_reports_a_lapsed_schedule() -> None:
    registry = PricingRegistry([ModelPricing("openai", "demo", 1.0, 1.0, effective_until="2026-01-01")])
    gap = registry.explain("openai", "demo", at=SEPTEMBER)
    assert gap is not None
    assert "2026-01-01" in gap.reason


def test_pricing_gap_serializes() -> None:
    gap = PricingRegistry().explain("openai", "not-a-model")
    assert gap is not None
    assert set(gap.to_dict()) == {"provider", "model", "reason"}


# --- serialization ---------------------------------------------------------


def test_registry_round_trips_through_json(tmp_path: Path) -> None:
    original = PricingRegistry()
    reloaded = PricingRegistry.load(original.save(tmp_path / "pricing.json"))
    assert reloaded.to_list() == original.to_list()


def test_round_trip_preserves_multiple_schedules(tmp_path: Path) -> None:
    reloaded = PricingRegistry.load(PricingRegistry().save(tmp_path / "p.json"))
    assert len(reloaded.schedules("anthropic", "claude-sonnet-5")) == 2


def test_round_trip_preserves_provenance(tmp_path: Path) -> None:
    reloaded = PricingRegistry.load(PricingRegistry().save(tmp_path / "p.json"))
    pricing = reloaded.get("openai", "gpt-5.6-sol")
    assert pricing is not None
    assert pricing.verified_on == PRICING_VERIFIED_ON
    assert pricing.confidence == VERIFIED
    assert pricing.source_url


def test_load_of_missing_file_returns_defaults(tmp_path: Path) -> None:
    registry = PricingRegistry.load(tmp_path / "absent.json")
    assert registry.get("anthropic", "claude-opus-5") is not None


def test_workspace_override_wins_over_the_public_default(tmp_path: Path) -> None:
    target = tmp_path / "pricing.json"
    target.write_text(
        '[{"provider": "anthropic", "model": "claude-opus-5", '
        '"input_per_1m": 1.0, "output_per_1m": 2.0, "note": "contract rate"}]',
        encoding="utf-8",
    )
    pricing = PricingRegistry.load(target).get("anthropic", "claude-opus-5")
    assert pricing is not None
    assert pricing.input_per_1m == 1.0


def test_override_does_not_remove_other_public_models(tmp_path: Path) -> None:
    target = tmp_path / "pricing.json"
    target.write_text(
        '[{"provider": "anthropic", "model": "claude-opus-5", '
        '"input_per_1m": 1.0, "output_per_1m": 2.0}]',
        encoding="utf-8",
    )
    assert PricingRegistry.load(target).get("openai", "gpt-5.6-sol") is not None


def test_save_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = PricingRegistry().save(tmp_path / "nested" / "deep" / "pricing.json")
    assert target.exists()


def test_to_dict_exposes_every_field() -> None:
    keys = set(ModelPricing("openai", "demo", 1.0, 2.0).to_dict())
    assert {"provider", "model", "input_per_1m", "output_per_1m", "cached_input_per_1m",
            "effective_from", "effective_until", "verified_on", "source_url", "confidence"} <= keys


# --- catalog integrity -----------------------------------------------------


def test_catalog_is_not_empty() -> None:
    assert len(default_pricing()) > 40


@pytest.mark.parametrize(
    ("provider", "model", "input_per_1m", "output_per_1m"),
    [
        ("anthropic", "claude-fable-5", 10.00, 50.00),
        ("anthropic", "claude-opus-5", 5.00, 25.00),
        ("anthropic", "claude-haiku-4-5", 1.00, 5.00),
        ("openai", "gpt-5.6-sol", 5.00, 30.00),
        ("openai", "gpt-5.6-terra", 2.00, 12.00),
        ("openai", "gpt-5.6-luna", 0.20, 1.20),
        ("google", "gemini-3.5-flash", 1.50, 9.00),
        ("xai", "grok-4.6", 2.00, 6.00),
        ("xai", "grok-4.3", 1.25, 2.50),
        ("deepseek", "deepseek-v4-flash", 0.14, 0.28),
    ],
)
def test_verified_rates_match_the_published_list_price(
    provider: str, model: str, input_per_1m: float, output_per_1m: float
) -> None:
    pricing = PricingRegistry().get(provider, model)
    assert pricing is not None
    assert (pricing.input_per_1m, pricing.output_per_1m) == (input_per_1m, output_per_1m)


@pytest.mark.parametrize(
    "model",
    ["claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
)
def test_current_anthropic_models_are_present(model: str) -> None:
    assert PricingRegistry().get("anthropic", model) is not None


@pytest.mark.parametrize(
    "model", ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4"]
)
def test_current_openai_models_are_present(model: str) -> None:
    assert PricingRegistry().get("openai", model) is not None


@pytest.mark.parametrize("model", ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"])
def test_current_google_models_are_present(model: str) -> None:
    assert PricingRegistry().get("google", model) is not None


@pytest.mark.parametrize("model", ["grok-4.6", "grok-4.5", "grok-4.3", "grok-4.1"])
def test_current_xai_models_are_present(model: str) -> None:
    assert PricingRegistry().get("xai", model) is not None


@pytest.mark.parametrize(
    "model",
    ["claude-3-5-haiku", "claude-3-7-sonnet", "claude-opus-4-1", "claude-sonnet-4-20250514"],
)
def test_retired_models_still_price_historical_usage(model: str) -> None:
    # Retirement stops new usage; it must not orphan already-logged usage.
    assert PricingRegistry().get("anthropic", model) is not None


def test_every_rate_has_a_recognized_confidence_level() -> None:
    assert all(pricing.confidence in CONFIDENCE_LEVELS for pricing in default_pricing())


def test_every_verified_rate_records_when_it_was_checked() -> None:
    for pricing in default_pricing():
        if pricing.confidence == VERIFIED:
            assert pricing.verified_on == PRICING_VERIFIED_ON, pricing.key


def test_every_paid_rate_cites_a_source() -> None:
    for pricing in default_pricing():
        if pricing.provider != "local":
            assert pricing.source_url, f"{pricing.key} has no source"


def test_no_rate_is_negative() -> None:
    for pricing in default_pricing():
        assert pricing.input_per_1m >= 0 and pricing.output_per_1m >= 0, pricing.key


def test_cached_input_never_costs_more_than_regular_input() -> None:
    for pricing in default_pricing():
        if pricing.cached_input_per_1m is not None:
            assert pricing.cached_input_per_1m <= pricing.input_per_1m, pricing.key


def test_output_is_never_cheaper_than_input() -> None:
    # True of every provider's published pricing; a violation signals a typo.
    for pricing in default_pricing():
        if pricing.provider != "local":
            assert pricing.output_per_1m >= pricing.input_per_1m, pricing.key


def test_local_model_is_free_by_design() -> None:
    pricing = PricingRegistry().get("local", "local-model")
    assert pricing is not None
    assert pricing.estimate(10_000_000, 10_000_000) == 0.0


def test_unverified_rates_are_flagged_rather_than_silently_trusted() -> None:
    carried = [p for p in default_pricing() if p.confidence == CARRIED_FORWARD]
    assert carried, "the audit should leave some rates explicitly unconfirmed"
    for pricing in carried:
        assert "verify" in pricing.note.lower() or "re-verify" in pricing.note.lower()


def test_no_model_has_overlapping_rate_windows() -> None:
    registry = PricingRegistry()
    for pricing in default_pricing():
        schedules = registry.schedules(pricing.provider, pricing.model)
        for earlier, later in zip(schedules, schedules[1:]):
            assert earlier.effective_until is not None, f"{pricing.key} overlaps"
            assert earlier.effective_until == later.effective_from, f"{pricing.key} has a gap"


def test_every_closed_window_has_a_successor() -> None:
    # The known-successor rule: never close a window into nothing, because an
    # unpriced event costs $0.00 and $0.00 reads as free.
    registry = PricingRegistry()
    for pricing in default_pricing():
        if not pricing.effective_until:
            continue
        successors = [
            item
            for item in registry.schedules(pricing.provider, pricing.model)
            if item.effective_from == pricing.effective_until
        ]
        assert successors, f"{pricing.key} closes at {pricing.effective_until} with no successor"


def test_every_catalog_model_prices_today() -> None:
    registry = PricingRegistry()
    for pricing in default_pricing():
        assert registry.get(pricing.provider, pricing.model) is not None, pricing.key
