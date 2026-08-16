"""Eco-tips — optional LLM-powered sustainability suggestions.

When a Grok (or compatible) API key is configured, this module sends
benchmark results to the LLM and returns actionable eco-tips for reducing
training emissions.  Without a key it returns a curated set of static tips.
"""

from __future__ import annotations

import logging
from typing import Optional

from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)
console = Console()

# ── Static tips (always available) ───────────────────────────────────────

_STATIC_TIPS: list[str] = [
    "🔋 Use LoRA (r=8) to reduce trainable parameters by ~99% — same quality, fraction of the energy.",
    "📦 4-bit quantisation (NF4) cuts memory footprint by ~75% with minimal accuracy loss.",
    "🌙 Schedule training for off-peak hours when the grid uses more renewables.",
    "🌍 Choose a cloud region powered by renewables (e.g. GCP us-central1 or europe-north1).",
    "📉 Reduce max sequence length — shorter sequences train quadratically faster.",
    "🔄 Use gradient accumulation instead of large batches to fit in less VRAM.",
    "💾 Enable gradient checkpointing — trades ~30% speed for ~60% memory savings.",
    "🧊 Early stopping prevents wasted epochs once the model has converged.",
    "📊 Track emissions with CodeCarbon and compare runs — you can't improve what you don't measure.",
    "🌿 Consider smaller base models (Phi-3, Gemma-2B) — they often rival 7B+ models after fine-tuning.",
]


def get_eco_tips(
    benchmark_summary: dict,
    *,
    api_key: Optional[str] = None,
    api_base: str = "https://api.x.ai/v1",
    model: str = "grok-3-mini",
    max_tips: int = 5,
) -> list[str]:
    """Return eco-friendly tips based on *benchmark_summary*.

    If *api_key* is provided, queries the Grok API (or any OpenAI-compatible
    endpoint) for personalised tips.  Otherwise returns a curated static set.

    Args:
        benchmark_summary: Dict from :func:`benchmark_training` containing
            emissions, energy, duration, perplexity, etc.
        api_key: Grok / xAI API key (or ``POLIORA_GROK_API_KEY`` env var).
        api_base: Base URL for the chat completions endpoint.
        model: Model identifier for the chat API.
        max_tips: Maximum number of tips to return.

    Returns:
        List of human-readable tip strings.
    """
    if api_key:
        llm_tips = _query_llm(benchmark_summary, api_key=api_key, api_base=api_base, model=model)
        if llm_tips:
            return llm_tips[:max_tips]
        console.print("[yellow]⚠ LLM eco-tips unavailable — using static tips.[/yellow]")

    return _STATIC_TIPS[:max_tips]


def print_eco_tips(tips: list[str]) -> None:
    """Pretty-print eco-tips in a Rich panel."""
    lines = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(tips))
    console.print()
    console.print(Panel(
        f"[bold green]🌿 Eco Tips[/bold green]\n\n{lines}",
        border_style="green",
        expand=False,
        title="Sustainability Suggestions",
        title_align="left",
    ))
    console.print()


# ── Private: LLM query ──────────────────────────────────────────────────


def _query_llm(
    summary: dict,
    *,
    api_key: str,
    api_base: str,
    model: str,
) -> Optional[list[str]]:
    """Send benchmark results to an OpenAI-compatible chat API and parse tips."""
    try:
        import httpx
    except ImportError:
        logger.debug("httpx not installed — skipping LLM eco-tips.")
        return None

    prompt = (
        "You are an expert in sustainable machine learning. "
        "Given the following training benchmark results, provide 5 concise, "
        "actionable tips to reduce energy consumption and carbon emissions "
        "for future training runs — each tip on its own line, prefixed with "
        "an emoji.\n\n"
        f"Benchmark results:\n{summary}\n\n"
        "Tips:"
    )

    try:
        resp = httpx.post(
            f"{api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 500,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        content: str = data["choices"][0]["message"]["content"]
        tips = [line.strip() for line in content.strip().splitlines() if line.strip()]
        return tips if tips else None
    except Exception as exc:
        logger.warning("LLM eco-tips request failed: %s", exc)
        return None
