"""Provider model discovery for keeping a local catalog current."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from poliora.cost.catalog import (
    ANTHROPIC_MODELS_URL,
    GOOGLE_MODELS_URL,
    MISTRAL_MODELS_URL,
    OPENAI_MODELS_URL,
    XAI_MODELS_URL,
    CatalogModel,
    ModelCatalog,
)


@dataclass(frozen=True)
class ModelSyncResult:
    """Outcome of a provider model discovery request."""

    provider: str
    discovered: int
    added: int
    updated: int


def sync_provider_models(
    provider: str,
    api_key: str,
    catalog: ModelCatalog,
    *,
    client: Any | None = None,
) -> ModelSyncResult:
    """Merge models visible to a provider account into the local catalog.

    The provider API key is used only for this request. Public or contract
    pricing is intentionally not replaced here; keep those values in the
    workspace pricing registry.
    """
    normalized_provider = provider.strip().lower()
    request = _provider_request(normalized_provider, api_key)
    owns_client = client is None
    if owns_client:
        import httpx

        client = httpx.Client(timeout=20.0)

    try:
        response = client.get(request["url"], headers=request["headers"], params=request["params"])
        response.raise_for_status()
        models = _parse_provider_payload(normalized_provider, response.json())
    finally:
        if owns_client:
            client.close()

    added = 0
    updated = 0
    for model in models:
        if catalog.get(model.provider, model.model) is None:
            added += 1
        else:
            updated += 1
        catalog.add(model)
    return ModelSyncResult(
        provider=normalized_provider,
        discovered=len(models),
        added=added,
        updated=updated,
    )


def _provider_request(provider: str, api_key: str) -> dict[str, object]:
    if provider == "openai":
        return {
            "url": "https://api.openai.com/v1/models",
            "headers": {"Authorization": f"Bearer {api_key}"},
            "params": {},
        }
    if provider == "anthropic":
        return {
            "url": "https://api.anthropic.com/v1/models",
            "headers": {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            "params": {"limit": 1000},
        }
    if provider == "google":
        return {
            "url": "https://generativelanguage.googleapis.com/v1beta/models",
            "headers": {},
            "params": {"key": api_key, "pageSize": 1000},
        }
    if provider == "mistral":
        return {
            "url": "https://api.mistral.ai/v1/models",
            "headers": {"Authorization": f"Bearer {api_key}"},
            "params": {},
        }
    if provider == "xai":
        return {
            "url": "https://api.x.ai/v1/language-models",
            "headers": {"Authorization": f"Bearer {api_key}"},
            "params": {},
        }
    supported = "openai, anthropic, google, mistral, xai"
    raise ValueError(f"Unsupported provider '{provider}'. Supported providers: {supported}.")


def _parse_provider_payload(provider: str, payload: dict[str, Any]) -> list[CatalogModel]:
    verified_at = datetime.now(timezone.utc).date().isoformat()
    if provider in {"openai", "anthropic", "mistral"}:
        items = payload.get("data", [])
        source_urls = {
            "openai": OPENAI_MODELS_URL,
            "anthropic": ANTHROPIC_MODELS_URL,
            "mistral": MISTRAL_MODELS_URL,
        }
        source_url = source_urls[provider]
        return [_catalog_model(provider, item, source_url, verified_at) for item in items if item.get("id")]
    if provider == "google":
        return [
            _catalog_model(provider, item, GOOGLE_MODELS_URL, verified_at, model_key="name", display_key="displayName")
            for item in payload.get("models", [])
            if item.get("name")
        ]
    if provider == "xai":
        return [
            _catalog_model(provider, item, XAI_MODELS_URL, verified_at, capabilities=_xai_capabilities(item))
            for item in payload.get("models", [])
            if item.get("id")
        ]
    raise ValueError(f"Unsupported provider '{provider}'.")


def _catalog_model(
    provider: str,
    item: dict[str, Any],
    source_url: str,
    verified_at: str,
    *,
    model_key: str = "id",
    display_key: str = "display_name",
    capabilities: tuple[str, ...] = ("text",),
) -> CatalogModel:
    model = str(item[model_key]).removeprefix("models/")
    display_name = str(item.get(display_key) or item.get("name") or model)
    context_window = item.get("inputTokenLimit") or item.get("context_length")
    return CatalogModel(
        provider=provider,
        model=model,
        display_name=display_name,
        status="account-available",
        capabilities=capabilities,
        context_window=int(context_window) if context_window else None,
        source_url=source_url,
        verified_at=verified_at,
        note="Discovered from this workspace's provider account.",
    )


def _xai_capabilities(item: dict[str, Any]) -> tuple[str, ...]:
    modalities = [str(value).lower() for value in item.get("input_modalities", [])]
    return tuple(dict.fromkeys([*modalities, "text"]))
