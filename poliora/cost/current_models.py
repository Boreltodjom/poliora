"""Public model snapshot checked against official sources on 2026-11-25.

This supplements historical records. Availability is distinct from a local
usage connector: listing Muse does not imply access to a user's Muse history.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from poliora.cost.catalog import CatalogModel
    from poliora.cost.pricing import ModelPricing

VERIFIED_ON = "2026-11-25"
SOURCES = {
    "openai": "https://developers.openai.com/api/docs/pricing",
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "google": "https://ai.google.dev/gemini-api/docs/pricing",
    "meta": "https://dev.meta.ai/docs/pricing-rate-limits",
    "qwen": "https://www.alibabacloud.com/help/en/model-studio/model-pricing",
    "deepseek": "https://api-docs.deepseek.com/quick_start/pricing/",
    "xai": "https://docs.x.ai/developers/models",
}
# provider, API identifier, display name, input, output, cached input (USD / MTok)
MODELS = (
    ("openai", "gpt-6.2-astra", "GPT-6.2 Astra", 8, 40, 0.4),
    ("openai", "gpt-6.2-sol", "GPT-6.2 Sol", 2, 10, 0.1),
    ("openai", "gpt-6-astra", "GPT-6 Astra", 10, 50, 1),
    ("openai", "gpt-6.1-sol", "GPT-6.1 Sol", 2, 10, 0.1),
    ("openai", "gpt-6-sol", "GPT-6 Sol", 2, 10, 0.2),
    ("openai", "gpt-6.1-terra", "GPT-6.1 Terra", 1, 5, 0.05),
    ("openai", "gpt-6-luna", "GPT-6 Luna", 0.1, 0.5, 0.01),
    ("anthropic", "claude-fable-5-2", "Claude Fable 5.2", 8, 40, 0.2),
    ("anthropic", "claude-mythos-5-2", "Claude Mythos 5.2", 8, 40, 0.2),
    ("anthropic", "claude-fable-5-1", "Claude Fable 5.1", 10, 50, 0.25),
    ("anthropic", "claude-opus-5-5", "Claude Opus 5.5", 4, 20, 0.2),
    ("anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5", 2, 10, 0.1),
    ("anthropic", "claude-haiku-5-5", "Claude Haiku 5.5", 0.1, 0.5, 0.01),
    ("google", "gemini-4-carbon", "Gemini 4 Carbon", 2, 10, 0.1),
    ("google", "gemini-4-argon", "Gemini 4 Argon", 2, 10, 0.1),
    ("google", "gemini-4-neon", "Gemini 4 Neon", 3, 15, 0.15),
    ("google", "gemini-4-hydrogen", "Gemini 4 Hydrogen", 1.5, 7.5, 0.075),
    ("meta", "muse-wonder-1.0", "Muse Wonder", 4, 20, 0.2),
    ("meta", "muse-spark-1.3", "Muse Spark 1.3", 1.25, 4.25, 0.15),
    ("meta", "muse-spark-1.2", "Muse Spark 1.2", 1.25, 4.25, 0.15),
    ("meta", "muse-spark-1.1", "Muse Spark 1.1", 1.25, 4.25, 0.15),
    ("meta", "muse-spark-1.3-contributor", "Muse Spark 1.3 Contributor", 0.1, 0.2, 0.002),
    ("meta", "muse-spark-1.2-contributor", "Muse Spark 1.2 Contributor", 0.1, 0.2, 0.002),
    ("meta", "muse-ember-1.0", "Muse Ember", 0.1, 0.5, 0.004),
    ("qwen", "qwen4-max", "Qwen 4 Max", 1.413, 4.24, None),
    ("qwen", "qwen3.8-max", "Qwen 3.8 Max", 1.65, 4.951, None),
    ("qwen", "qwen3.7-max", "Qwen 3.7 Max", 1.65, 4.951, None),
    ("qwen", "qwen3.6-plus", "Qwen 3.6 Plus", 0.5, 3, None),
    ("qwen", "qwen3.5-plus", "Qwen 3.5 Plus", 0.4, 2.4, None),
    ("qwen", "qwen3-coder-flash", "Qwen 3 Coder Flash", 0.3, 1.5, None),
    ("deepseek", "deepseek-flash", "DeepSeek V4.1 Flash 1017", 0.3, 1.2, 0.006),
    ("deepseek", "deepseek-v4-pro-0813", "DeepSeek V4 Pro 0813", 1.32, 3.96, 0.044),
    ("xai", "grok-4.7", "Grok 4.7", 2, 6, None),
)
# threshold, full-request long input/output/cache rates
LONG_CONTEXT = {
    "claude-haiku-5-5": (100_000, 0.5, 2.5, 0.05),
    "muse-ember-1.0": (200_000, 0.3, 1.5, 0.012),
    "gpt-6.1-terra": (272_000, 2, 7.5, 0.1),
    "gpt-6.2-astra": (272_000, 16, 60, 0.8),
    "gpt-6.2-sol": (272_000, 4, 15, 0.2),
    "gpt-6-sol": (272_000, 4, 15, 0.4),
    "gpt-6.1-sol": (272_000, 4, 15, 0.2),
    "gpt-6-astra": (272_000, 20, 75, 2),
    "gpt-6-luna": (272_000, 0.2, 0.75, 0.02),
    "qwen3.6-plus": (256_000, 2, 6, None),
    "qwen3.5-plus": (256_000, 0.5, 3, None),
}


def _note(provider: str, model: str) -> str:
    note = "Standard text token list price; excludes tools, cache writes, taxes and regional surcharges."
    if provider == "qwen":
        note += " Max models: Global deployment; Plus/Coder: Singapore International. Region affects price."
    if provider == "deepseek":
        note += " Peak-rate upper estimate; off-peak costs half. Holiday/time discounts are not inferred."
    if model.endswith("contributor"):
        note += " Contributor mode permits Meta to use submitted content for model training."
    if model == "claude-mythos-5-2":
        note += " Restricted access; availability depends on provider approval."
    if model in LONG_CONTEXT:
        note += " Higher rates apply to the full request above the prompt threshold."
    if model == "qwen3-coder-flash":
        note += " Base rate up to 32K; larger prompts require a custom tier rate."
    return note


def current_catalog() -> list[CatalogModel]:
    from poliora.cost.catalog import CatalogModel

    models = [CatalogModel(
        provider, model, label, source_url=SOURCES[provider], verified_at=VERIFIED_ON,
        capabilities=("text",), status="restricted" if model == "claude-mythos-5-2" else "active",
        note=_note(provider, model),
    ) for provider, model, label, *_ in MODELS]
    models.append(CatalogModel(
        "mistral", "mistral-large-latest", "Mistral Large 4", capabilities=("text", "vision"),
        source_url="https://docs.mistral.ai/models", verified_at=VERIFIED_ON,
        note="Availability verified; select your deployment's pricing before estimating cost.",
    ))
    return models


def current_pricing() -> list[ModelPricing]:
    from poliora.cost.pricing import VERIFIED, ModelPricing

    rates = []
    for provider, model, _, input_rate, output_rate, cache_rate in MODELS:
        if model == "qwen3-coder-flash":
            continue
        tier = LONG_CONTEXT.get(model)
        details = dict(
            provider=provider, model=model, input_per_1m=input_rate, output_per_1m=output_rate,
            cached_input_per_1m=cache_rate, source_url=SOURCES[provider], verified_on=VERIFIED_ON,
            confidence=VERIFIED, note=_note(provider, model),
        )
        if tier:
            details.update(long_context_threshold=tier[0], long_input_per_1m=tier[1],
                           long_output_per_1m=tier[2], long_cached_input_per_1m=tier[3])
        if model in {"gemini-4-carbon", "gemini-4-argon"}:
            rates.append(ModelPricing(**details, effective_until="2026-12-01"))
            details.update(input_per_1m=4, output_per_1m=20, cached_input_per_1m=0.2,
                           effective_from="2026-12-01")
        rates.append(ModelPricing(**details))
    return rates
