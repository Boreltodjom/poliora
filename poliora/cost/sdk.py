"""Lightweight SDK helpers for recording AI usage from application code."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from poliora.cost.pricing import PricingRegistry
from poliora.cost.usage import JsonlUsageStore, UsageEvent
from poliora.cost.workspace import load_workspace


def log_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    operation: str = "chat",
    project: str | None = None,
    user: str | None = None,
    latency_ms: float | None = None,
    cost_usd: float | None = None,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    tool_cost_usd: float = 0.0,
    trace_id: str | None = None,
    provider_request_id: str | None = None,
    root: str | Path = ".",
    metadata: dict[str, Any] | None = None,
) -> UsageEvent:
    """Record one AI usage event and return it."""
    workspace = load_workspace(root)
    registry = PricingRegistry.load(workspace.pricing_path)
    estimated_token_cost = registry.estimate(
        provider,
        model,
        input_tokens,
        output_tokens,
        cached_input_tokens=cached_input_tokens,
    )
    final_cost = estimated_token_cost + tool_cost_usd if cost_usd is None else cost_usd

    event = UsageEvent(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=final_cost,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        tool_cost_usd=tool_cost_usd,
        operation=operation,
        project=project or workspace.project,
        user=user,
        latency_ms=latency_ms,
        trace_id=trace_id,
        provider_request_id=provider_request_id,
        metadata=metadata or {},
    )
    JsonlUsageStore(workspace.usage_path).append(event)
    return event


def log_openai_response(
    response: Any,
    *,
    provider: str = "openai",
    model: str | None = None,
    operation: str = "chat",
    project: str | None = None,
    user: str | None = None,
    latency_ms: float | None = None,
    root: str | Path = ".",
    metadata: dict[str, Any] | None = None,
) -> UsageEvent:
    """Record usage from an OpenAI-compatible response object or dict."""
    usage = _get_value(response, "usage") or {}
    provider_model = model or _get_value(response, "model") or "unknown"
    input_tokens = int(_get_value(usage, "prompt_tokens") or _get_value(usage, "input_tokens") or 0)
    output_tokens = int(_get_value(usage, "completion_tokens") or _get_value(usage, "output_tokens") or 0)
    prompt_details = _get_value(usage, "prompt_tokens_details") or _get_value(usage, "input_tokens_details") or {}
    completion_details = (
        _get_value(usage, "completion_tokens_details") or _get_value(usage, "output_tokens_details") or {}
    )
    cached_input_tokens = int(_get_value(prompt_details, "cached_tokens") or 0)
    reasoning_tokens = int(_get_value(completion_details, "reasoning_tokens") or 0)

    return log_usage(
        provider=provider,
        model=str(provider_model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        operation=operation,
        project=project,
        user=user,
        latency_ms=latency_ms,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        provider_request_id=_get_value(response, "_request_id") or _get_value(response, "id"),
        root=root,
        metadata=metadata,
    )


def log_anthropic_response(
    response: Any,
    *,
    model: str | None = None,
    operation: str = "message",
    project: str | None = None,
    user: str | None = None,
    latency_ms: float | None = None,
    root: str | Path = ".",
    metadata: dict[str, Any] | None = None,
) -> UsageEvent:
    """Record usage from an Anthropic-style response object or dict."""
    usage = _get_value(response, "usage") or {}
    provider_model = model or _get_value(response, "model") or "unknown"
    standard_input_tokens = int(_get_value(usage, "input_tokens") or 0)
    cached_input_tokens = int(_get_value(usage, "cache_read_input_tokens") or 0)
    cache_creation_input_tokens = int(_get_value(usage, "cache_creation_input_tokens") or 0)
    input_tokens = standard_input_tokens + cached_input_tokens + cache_creation_input_tokens
    output_tokens = int(_get_value(usage, "output_tokens") or 0)

    combined_metadata = dict(metadata or {})
    if cache_creation_input_tokens:
        combined_metadata["cache_creation_input_tokens"] = cache_creation_input_tokens

    return log_usage(
        provider="anthropic",
        model=str(provider_model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        operation=operation,
        project=project,
        user=user,
        latency_ms=latency_ms,
        cached_input_tokens=cached_input_tokens,
        provider_request_id=_get_value(response, "_request_id") or _get_value(response, "id"),
        root=root,
        metadata=combined_metadata,
    )


def log_gemini_response(
    response: Any,
    *,
    model: str | None = None,
    operation: str = "generate-content",
    project: str | None = None,
    user: str | None = None,
    latency_ms: float | None = None,
    root: str | Path = ".",
    metadata: dict[str, Any] | None = None,
) -> UsageEvent:
    """Record usage from a Gemini GenerateContent-style response."""
    usage = _get_alias(response, "usage_metadata", "usageMetadata") or {}
    provider_model = model or _get_alias(response, "model", "model_version", "modelVersion") or "unknown"
    prompt_tokens = _as_int(_get_alias(usage, "prompt_token_count", "promptTokenCount"))
    cached_input_tokens = _as_int(_get_alias(usage, "cached_content_token_count", "cachedContentTokenCount"))
    tool_prompt_tokens = _as_int(_get_alias(usage, "tool_use_prompt_token_count", "toolUsePromptTokenCount"))
    output_tokens = _as_int(_get_alias(usage, "candidates_token_count", "candidatesTokenCount"))
    reasoning_tokens = _as_int(_get_alias(usage, "thoughts_token_count", "thoughtsTokenCount"))

    combined_metadata = dict(metadata or {})
    if tool_prompt_tokens:
        combined_metadata["tool_use_prompt_tokens"] = tool_prompt_tokens

    return log_usage(
        provider="google",
        model=str(provider_model),
        input_tokens=prompt_tokens + tool_prompt_tokens,
        output_tokens=output_tokens + reasoning_tokens,
        operation=operation,
        project=project,
        user=user,
        latency_ms=latency_ms,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        provider_request_id=_get_alias(response, "response_id", "responseId", "id"),
        root=root,
        metadata=combined_metadata,
    )


def _get_value(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def _get_alias(source: Any, *keys: str) -> Any:
    for key in keys:
        value = _get_value(source, key)
        if value is not None:
            return value
    return None


def _as_int(value: Any) -> int:
    return int(value or 0)
