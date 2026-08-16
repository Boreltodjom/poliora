"""Provider response capture and client wrappers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from poliora.cost.sdk import log_anthropic_response, log_gemini_response, log_openai_response
from poliora.cost.usage import UsageEvent


@dataclass(frozen=True)
class CapturedCall:
    """Result plus the Poliora usage event produced by a tracked call."""

    response: Any
    event: UsageEvent


def track_openai_call(
    func: Callable[..., Any],
    *args: Any,
    operation: str = "chat",
    project: str | None = None,
    user: str | None = None,
    root: str | Path = ".",
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> CapturedCall:
    """Run an OpenAI-style function call and log usage from its response."""
    return track_openai_compatible_call(
        func,
        *args,
        provider="openai",
        operation=operation,
        project=project,
        user=user,
        root=root,
        metadata=metadata,
        **kwargs,
    )


def track_openai_compatible_call(
    func: Callable[..., Any],
    *args: Any,
    provider: str,
    operation: str = "chat",
    project: str | None = None,
    user: str | None = None,
    root: str | Path = ".",
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> CapturedCall:
    """Run and track a call from an OpenAI-compatible provider or gateway."""
    start = time.perf_counter()
    response = func(*args, **kwargs)
    latency_ms = (time.perf_counter() - start) * 1000
    event = log_openai_response(
        response,
        provider=provider,
        operation=operation,
        project=project,
        user=user,
        latency_ms=latency_ms,
        root=root,
        metadata=metadata,
    )
    return CapturedCall(response=response, event=event)


def track_anthropic_call(
    func: Callable[..., Any],
    *args: Any,
    operation: str = "message",
    project: str | None = None,
    user: str | None = None,
    root: str | Path = ".",
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> CapturedCall:
    """Run an Anthropic-style function call and log usage from its response."""
    start = time.perf_counter()
    response = func(*args, **kwargs)
    latency_ms = (time.perf_counter() - start) * 1000
    event = log_anthropic_response(
        response,
        operation=operation,
        project=project,
        user=user,
        latency_ms=latency_ms,
        root=root,
        metadata=metadata,
    )
    return CapturedCall(response=response, event=event)


def track_gemini_call(
    func: Callable[..., Any],
    *args: Any,
    model: str | None = None,
    operation: str = "generate-content",
    project: str | None = None,
    user: str | None = None,
    root: str | Path = ".",
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> CapturedCall:
    """Run a Gemini-style function call and log usage from its response."""
    start = time.perf_counter()
    response = func(*args, **kwargs)
    latency_ms = (time.perf_counter() - start) * 1000
    event = log_gemini_response(
        response,
        model=model,
        operation=operation,
        project=project,
        user=user,
        latency_ms=latency_ms,
        root=root,
        metadata=metadata,
    )
    return CapturedCall(response=response, event=event)


def track_openai_client(
    client: Any,
    *,
    project: str | None = None,
    user: str | None = None,
    root: str | Path = ".",
    capture_responses_api: bool = True,
) -> Any:
    """Return a proxy that auto-logs common OpenAI SDK calls.

    The proxy preserves normal attribute access. It only wraps known terminal
    methods such as ``client.chat.completions.create`` and ``client.responses.create``.
    """
    return track_openai_compatible_client(
        client,
        provider="openai",
        project=project,
        user=user,
        root=root,
        capture_responses_api=capture_responses_api,
    )


def track_openai_compatible_client(
    client: Any,
    *,
    provider: str,
    project: str | None = None,
    user: str | None = None,
    root: str | Path = ".",
    capture_responses_api: bool = True,
) -> Any:
    """Return a proxy for an OpenAI-compatible client under a provider label."""
    wrapped_paths = {("chat", "completions", "create")}
    if capture_responses_api:
        wrapped_paths.add(("responses", "create"))
    return _ClientProxy(
        client,
        provider=provider,
        path=(),
        wrapped_paths=wrapped_paths,
        project=project,
        user=user,
        root=root,
    )


def track_anthropic_client(
    client: Any,
    *,
    project: str | None = None,
    user: str | None = None,
    root: str | Path = ".",
) -> Any:
    """Return a proxy that auto-logs common Anthropic SDK calls."""
    return _ClientProxy(
        client,
        provider="anthropic",
        path=(),
        wrapped_paths={("messages", "create")},
        project=project,
        user=user,
        root=root,
    )


def track_gemini_client(
    client: Any,
    *,
    project: str | None = None,
    user: str | None = None,
    root: str | Path = ".",
) -> Any:
    """Return a proxy that auto-logs ``client.models.generate_content`` calls."""
    return _ClientProxy(
        client,
        provider="google",
        path=(),
        wrapped_paths={("models", "generate_content")},
        project=project,
        user=user,
        root=root,
    )


class _ClientProxy:
    """Small attribute proxy used by provider-specific wrappers."""

    def __init__(
        self,
        target: Any,
        *,
        provider: str,
        path: tuple[str, ...],
        wrapped_paths: set[tuple[str, ...]],
        project: str | None,
        user: str | None,
        root: str | Path,
    ) -> None:
        self._target = target
        self._provider = provider
        self._path = path
        self._wrapped_paths = wrapped_paths
        self._project = project
        self._user = user
        self._root = root

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        path = self._path + (name,)
        if callable(value) and path in self._wrapped_paths:
            return self._wrap_callable(value, operation=".".join(path[:-1]) or name)
        if not any(candidate[: len(path)] == path for candidate in self._wrapped_paths):
            return value
        return _ClientProxy(
            value,
            provider=self._provider,
            path=path,
            wrapped_paths=self._wrapped_paths,
            project=self._project,
            user=self._user,
            root=self._root,
        )

    def _wrap_callable(self, func: Callable[..., Any], *, operation: str) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            response = func(*args, **kwargs)
            latency_ms = (time.perf_counter() - start) * 1000
            if self._provider == "anthropic":
                log_anthropic_response(
                    response,
                    operation=operation,
                    project=self._project,
                    user=self._user,
                    latency_ms=latency_ms,
                    root=self._root,
                )
            elif self._provider == "google":
                log_gemini_response(
                    response,
                    operation=operation,
                    project=self._project,
                    user=self._user,
                    latency_ms=latency_ms,
                    root=self._root,
                )
            else:
                log_openai_response(
                    response,
                    provider=self._provider,
                    operation=operation,
                    project=self._project,
                    user=self._user,
                    latency_ms=latency_ms,
                    root=self._root,
                )
            return response

        return wrapped
