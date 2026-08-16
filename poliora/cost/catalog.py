"""Versioned model catalog with provider provenance and lifecycle metadata."""
# ruff: noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from poliora.cost.pricing import make_pricing_key

OPENAI_MODELS_URL = "https://platform.openai.com/docs/api-reference/models/object"
OPENAI_GPT_5_6_URL = "https://openai.com/index/gpt-5-6/"
OPENAI_GPT_5_5_URL = "https://developers.openai.com/api/docs/models/gpt-5.5"
ANTHROPIC_MODELS_URL = "https://docs.anthropic.com/en/docs/about-claude/models"
ANTHROPIC_FABLE_URL = "https://www.anthropic.com/claude/fable"
ANTHROPIC_OPUS_URL = "https://www.anthropic.com/claude/opus"
ANTHROPIC_SONNET_URL = "https://www.anthropic.com/claude/sonnet"
GOOGLE_MODELS_URL = "https://ai.google.dev/gemini-api/docs/models"
DEEPSEEK_MODELS_URL = "https://api-docs.deepseek.com/quick_start/pricing"
XAI_MODELS_URL = "https://docs.x.ai/developers/models"
MISTRAL_MODELS_URL = "https://docs.mistral.ai/models/model-selection-guide"
CATALOG_VERIFIED_AT = "2026-07-27"


@dataclass(frozen=True)
class CatalogModel:
    """One provider model known to Poliora.

    Catalog records deliberately do not contain contract pricing. Pricing lives
    in ``pricing.json`` so a public catalog refresh never overwrites customer
    rates.
    """

    provider: str
    model: str
    display_name: str
    status: str = "active"
    capabilities: tuple[str, ...] = ("text",)
    context_window: int | None = None
    source_url: str = ""
    verified_at: str = CATALOG_VERIFIED_AT
    note: str = ""

    @property
    def key(self) -> str:
        """Return the normalized provider/model key."""
        return make_pricing_key(self.provider, self.model)

    def to_dict(self) -> dict[str, object]:
        """Serialize this model for the workspace catalog."""
        return {
            "provider": self.provider,
            "model": self.model,
            "display_name": self.display_name,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "context_window": self.context_window,
            "source_url": self.source_url,
            "verified_at": self.verified_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "CatalogModel":
        """Deserialize a model record from JSON."""
        capabilities = data.get("capabilities", ["text"])
        if not isinstance(capabilities, list):
            raise ValueError("Catalog model capabilities must be a list.")
        context_window = data.get("context_window")
        return cls(
            provider=str(data["provider"]),
            model=str(data["model"]),
            display_name=str(data.get("display_name") or data["model"]),
            status=str(data.get("status", "active")),
            capabilities=tuple(str(item) for item in capabilities),
            context_window=int(context_window) if context_window is not None else None,
            source_url=str(data.get("source_url", "")),
            verified_at=str(data.get("verified_at", CATALOG_VERIFIED_AT)),
            note=str(data.get("note", "")),
        )


class ModelCatalog:
    """Catalog that combines built-in records with workspace additions."""

    def __init__(self, models: Iterable[CatalogModel] | None = None) -> None:
        self._models: dict[str, CatalogModel] = {}
        for model in default_catalog():
            self.add(model)
        for model in models or []:
            self.add(model)

    def add(self, model: CatalogModel) -> None:
        """Add or replace a model record."""
        self._models[model.key] = model

    def get(self, provider: str, model: str) -> CatalogModel | None:
        """Look up a provider model."""
        return self._models.get(make_pricing_key(provider, model))

    def to_list(self) -> list[dict[str, object]]:
        """Return stable, JSON-ready records."""
        return [model.to_dict() for model in sorted(self._models.values(), key=lambda item: item.key)]

    def save(self, path: str | Path) -> Path:
        """Write the full effective catalog to JSON."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_list(), indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "ModelCatalog":
        """Load catalog entries and merge them over current built-in records."""
        source = Path(path)
        if not source.exists():
            return cls()
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Model catalog JSON must be a list.")
        return cls(CatalogModel.from_dict(dict(item)) for item in raw)


def default_catalog() -> list[CatalogModel]:
    """Return built-in model records verified against official provider docs."""
    return [
        CatalogModel("openai", "gpt-5", "GPT-5", capabilities=("text", "reasoning"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5.1", "GPT-5.1", capabilities=("text", "reasoning"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5.2", "GPT-5.2", capabilities=("text", "reasoning"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5.4", "GPT-5.4", capabilities=("text", "vision", "reasoning", "tools"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5.4-mini", "GPT-5.4 Mini", capabilities=("text", "vision", "reasoning", "tools"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5.4-nano", "GPT-5.4 Nano", capabilities=("text", "vision", "reasoning", "tools"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5.5", "GPT-5.5", capabilities=("text", "vision", "reasoning", "tools"), context_window=1_050_000, source_url=OPENAI_GPT_5_5_URL),
        CatalogModel("openai", "gpt-5-pro", "GPT-5 Pro", capabilities=("text", "reasoning"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5-mini", "GPT-5 Mini", capabilities=("text", "reasoning"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5-nano", "GPT-5 Nano", capabilities=("text", "reasoning"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5-chat-latest", "GPT-5 Chat Latest", source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-5.6", "GPT-5.6", capabilities=("text", "vision", "reasoning", "tools"), source_url=OPENAI_GPT_5_6_URL),
        CatalogModel("openai", "gpt-5.6-sol", "GPT-5.6 Sol", capabilities=("text", "vision", "reasoning", "tools"), source_url=OPENAI_GPT_5_6_URL),
        CatalogModel("openai", "gpt-5.6-terra", "GPT-5.6 Terra", capabilities=("text", "vision", "reasoning", "tools"), source_url=OPENAI_GPT_5_6_URL),
        CatalogModel("openai", "gpt-5.6-luna", "GPT-5.6 Luna", capabilities=("text", "vision", "reasoning", "tools"), source_url=OPENAI_GPT_5_6_URL),
        CatalogModel("openai", "gpt-4.1", "GPT-4.1", capabilities=("text", "vision"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-4.1-mini", "GPT-4.1 Mini", capabilities=("text", "vision"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-4.1-nano", "GPT-4.1 Nano", capabilities=("text", "vision"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-4o", "GPT-4o", capabilities=("text", "vision", "audio"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "gpt-4o-mini", "GPT-4o Mini", capabilities=("text", "vision", "audio"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "o3", "o3", capabilities=("text", "reasoning"), source_url=OPENAI_MODELS_URL),
        CatalogModel("openai", "o4-mini", "o4 Mini", capabilities=("text", "reasoning"), source_url=OPENAI_MODELS_URL),
        CatalogModel(
            "anthropic", "claude-opus-4-1-20250805", "Claude Opus 4.1", capabilities=("text", "vision", "reasoning"), source_url=ANTHROPIC_MODELS_URL
        ),
        CatalogModel(
            "anthropic", "claude-opus-4-20250514", "Claude Opus 4", capabilities=("text", "vision", "reasoning"), source_url=ANTHROPIC_MODELS_URL
        ),
        CatalogModel(
            "anthropic", "claude-opus-4-6", "Claude Opus 4.6", capabilities=("text", "vision", "reasoning", "tools"), context_window=1_000_000, source_url=ANTHROPIC_OPUS_URL
        ),
        CatalogModel(
            "anthropic", "claude-opus-4-8", "Claude Opus 4.8", capabilities=("text", "vision", "reasoning", "tools"), context_window=1_000_000, source_url=ANTHROPIC_OPUS_URL
        ),
        CatalogModel(
            "anthropic", "claude-sonnet-4-20250514", "Claude Sonnet 4", capabilities=("text", "vision", "reasoning"), source_url=ANTHROPIC_MODELS_URL
        ),
        CatalogModel(
            "anthropic", "claude-sonnet-4-6", "Claude Sonnet 4.6", capabilities=("text", "vision", "reasoning", "tools"), context_window=1_000_000, source_url=ANTHROPIC_SONNET_URL
        ),
        CatalogModel(
            "anthropic", "claude-sonnet-5", "Claude Sonnet 5", capabilities=("text", "vision", "reasoning", "tools"), context_window=1_000_000, source_url=ANTHROPIC_SONNET_URL, note="Introductory API pricing runs through 2026-08-31."
        ),
        CatalogModel(
            "anthropic", "claude-3-7-sonnet-20250219", "Claude Sonnet 3.7", capabilities=("text", "vision", "reasoning"), source_url=ANTHROPIC_MODELS_URL
        ),
        CatalogModel(
            "anthropic", "claude-3-5-haiku-20241022", "Claude Haiku 3.5", capabilities=("text", "vision"), source_url=ANTHROPIC_MODELS_URL
        ),
        CatalogModel(
            "anthropic", "claude-3-haiku-20240307", "Claude Haiku 3", capabilities=("text", "vision"), source_url=ANTHROPIC_MODELS_URL
        ),
        CatalogModel(
            "anthropic", "claude-fable-5", "Claude Fable 5", capabilities=("text", "vision", "reasoning", "tools"), source_url=ANTHROPIC_FABLE_URL
        ),
        CatalogModel("google", "gemini-3.6-pro", "Gemini 3.6 Pro", capabilities=("text", "vision", "audio", "reasoning"), context_window=2_000_000, source_url=GOOGLE_MODELS_URL),
        CatalogModel("google", "gemini-3.6-flash", "Gemini 3.6 Flash", capabilities=("text", "vision", "audio", "reasoning"), context_window=1_000_000, source_url=GOOGLE_MODELS_URL),
        CatalogModel("google", "gemini-3.5-flash", "Gemini 3.5 Flash", capabilities=("text", "vision", "audio", "reasoning"), source_url=GOOGLE_MODELS_URL),
        CatalogModel("google", "gemini-3.1-pro-preview", "Gemini 3.1 Pro", status="preview", capabilities=("text", "vision", "audio", "reasoning"), context_window=1_000_000, source_url=GOOGLE_MODELS_URL),
        CatalogModel("google", "gemini-3-flash-preview", "Gemini 3 Flash", status="preview", capabilities=("text", "vision", "audio", "reasoning"), context_window=1_000_000, source_url=GOOGLE_MODELS_URL),
        CatalogModel("google", "gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", capabilities=("text", "vision", "audio"), context_window=1_000_000, source_url=GOOGLE_MODELS_URL),
        CatalogModel("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro", capabilities=("text", "reasoning", "tools"), context_window=1_000_000, source_url=DEEPSEEK_MODELS_URL),
        CatalogModel("deepseek", "deepseek-v4-flash", "DeepSeek V4 Flash", capabilities=("text", "reasoning", "tools"), context_window=1_000_000, source_url=DEEPSEEK_MODELS_URL),
        CatalogModel("xai", "grok-4.5", "Grok 4.5", capabilities=("text", "vision", "reasoning", "tools"), context_window=500_000, source_url=XAI_MODELS_URL),
        CatalogModel("xai", "grok-4.3", "Grok 4.3", capabilities=("text", "vision", "reasoning", "tools"), context_window=1_000_000, source_url=XAI_MODELS_URL),
        CatalogModel("xai", "grok-build-0.1", "Grok Build 0.1", capabilities=("text", "code"), context_window=256_000, source_url=XAI_MODELS_URL),
        CatalogModel("mistral", "mistral-medium-3-5", "Mistral Medium 3.5", capabilities=("text", "vision", "tools"), context_window=256_000, source_url=MISTRAL_MODELS_URL),
    ]
