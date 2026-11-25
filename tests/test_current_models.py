"""Regression checks for the November model and pricing update."""
import json
from datetime import datetime, timezone

import pytest

from poliora.cost.catalog import ModelCatalog
from poliora.cost.current_models import MODELS, VERIFIED_ON
from poliora.cost.pricing import ModelPricing, PricingRegistry

NOVEMBER = datetime(2026, 11, 25, tzinfo=timezone.utc)
DECEMBER = datetime(2026, 12, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("provider,model", [(row[0], row[1]) for row in MODELS])
def test_current_models_have_sources(provider, model):
    item = ModelCatalog().get(provider, model)
    assert item is not None and item.source_url.startswith("https://")
    assert item.verified_at == VERIFIED_ON


@pytest.mark.parametrize("model,threshold,low,high", [
    ("claude-haiku-5-5", 100_000, 0.1, 0.5),
    ("muse-ember-1.0", 200_000, 0.1, 0.3),
])
def test_long_prompt_prices_entire_request(model, threshold, low, high):
    provider = "meta" if model.startswith("muse") else "anthropic"
    rate = PricingRegistry().get(provider, model, at=NOVEMBER)
    assert rate.estimate(threshold, 0) == round(threshold * low / 1e6, 8)
    assert rate.estimate(threshold + 1, 0) == round((threshold + 1) * high / 1e6, 8)
    assert rate.estimate(threshold + 1, 1000, cached_input_tokens=threshold) > 0


@pytest.mark.parametrize("model", ["gemini-4-carbon", "gemini-4-argon"])
def test_december_price_change_does_not_reprice_november(model):
    registry = PricingRegistry()
    assert registry.estimate("google", model, 1_000_000, 1_000_000, at=NOVEMBER) == 12
    assert registry.estimate("google", model, 1_000_000, 1_000_000, at=DECEMBER) == 24


def test_context_tier_survives_save_load(tmp_path):
    registry = PricingRegistry.load(PricingRegistry().save(tmp_path / "pricing.json"))
    rate = registry.get("meta", "muse-ember-1.0", at=NOVEMBER)
    assert rate.estimate(300_000, 1_000_000, cached_input_tokens=100_000) == 1.5612


def test_customer_rate_is_preserved(tmp_path):
    target = tmp_path / "pricing.json"
    rate = ModelPricing("meta", "muse-ember-1.0", 9, 10, note="customer contract")
    target.write_text(json.dumps([rate.to_dict()]), encoding="utf-8")
    assert PricingRegistry.load(target).get("meta", "muse-ember-1.0", at=NOVEMBER).input_per_1m == 9


def test_old_public_snapshot_cannot_downgrade_new_rate(tmp_path):
    target = tmp_path / "pricing.json"
    rate = PricingRegistry().get("meta", "muse-ember-1.0", at=NOVEMBER).to_dict()
    rate.update(verified_on="2026-08-16", input_per_1m=99)
    target.write_text(json.dumps([rate]), encoding="utf-8")
    assert PricingRegistry.load(target).get("meta", "muse-ember-1.0", at=NOVEMBER).input_per_1m == 0.1


@pytest.mark.parametrize("provider,url", [
    ("meta", "https://api.meta.ai/v1/models"),
    ("deepseek", "https://api.deepseek.com/models"),
])
def test_new_provider_discovery_uses_authorized_models_endpoint(provider, url):
    from poliora.cost.sync import sync_provider_models

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "new-model"}]}

    class Client:
        def get(self, actual_url, *, headers, params):
            assert actual_url == url
            assert headers == {"Authorization": "Bearer test-key"}
            assert params == {}
            return Response()

    catalog = ModelCatalog()
    result = sync_provider_models(provider, "test-key", catalog, client=Client())
    assert result.added == 1
    assert catalog.get(provider, "new-model").status == "account-available"


def test_saved_custom_rate_keeps_its_provenance(tmp_path):
    rate = PricingRegistry().get("meta", "muse-ember-1.0", at=NOVEMBER).to_dict()
    rate.update(verified_on="2026-08-16", input_per_1m=9, note="private contract override")
    target = tmp_path / "prices.json"
    target.write_text(json.dumps([rate]), encoding="utf-8")
    assert PricingRegistry.load(target).get("meta", "muse-ember-1.0", at=NOVEMBER).input_per_1m == 9


def test_partial_routing_preserves_full_request_context_tier():
    from poliora.cost.simulation import simulate_model_switch
    from poliora.cost.usage import UsageEvent

    result = simulate_model_switch(
        [UsageEvent("openai", "source", 300_000, 0, 1)],
        source_provider="openai", source_model="source", target_provider="meta",
        target_model="muse-ember-1.0", percentage=50,
    )
    assert result.estimated_target_cost_usd == 0.045
