"""Provider/model pricing helpers.

Prices are USD per 1 million tokens and carry provenance: every default rate
records the date it was verified and the source it came from.

Rates are **time-scoped**. Providers ship introductory pricing that expires and
scheduled increases that land on a known date, so one model may have several
rate schedules over its life. Pricing a usage event therefore resolves the
schedule that was in effect at the event's own timestamp, never "today's rate" —
otherwise re-running a report silently repriced last month's spend.

Two rules keep those windows honest:

1. **Only close a window when the successor rate is known.** A closed window with
   nothing after it makes usage report as unpriced, and an unpriced event costs
   $0.00 — which reads as free rather than as unknown. When a provider changes
   pricing and the new rate has not been recorded yet, leave the window open and
   drop the rate's confidence instead.
2. **Retirement is not a rate change.** A model going away stops new usage; it
   does not change what already-logged usage should cost. Retired models keep
   open-ended rates so historical imports still price correctly.

Production users should still override these with their vendor contract rates;
a workspace ``pricing.json`` always wins over the public defaults.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PRICING_VERIFIED_ON = "2026-08-16"

ANTHROPIC_PRICING_URL = "https://platform.claude.com/docs/en/pricing"
OPENAI_PRICING_URL = "https://openai.com/api/pricing/"
GOOGLE_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"
XAI_PRICING_URL = "https://docs.x.ai/docs/models"
DEEPSEEK_PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing"
MISTRAL_PRICING_URL = "https://mistral.ai/pricing"

# Confidence in a rate, surfaced in reports so a number is never mistaken for
# a fact it isn't. "carried-forward" means the rate predates the last audit and
# could not be re-confirmed against the provider.
VERIFIED = "verified"
CARRIED_FORWARD = "carried-forward"
SUPERSEDED = "superseded"
CONFIDENCE_LEVELS = (VERIFIED, CARRIED_FORWARD, SUPERSEDED)


@dataclass(frozen=True)
class ModelPricing:
    """Token pricing for one model over one window of time.

    A model with scheduled changes (an introductory rate, an announced
    increase) is represented as several instances that differ only in their
    effective window.
    """

    provider: str
    model: str
    input_per_1m: float
    output_per_1m: float
    note: str = "starter estimate; override with your provider rates"
    cached_input_per_1m: float | None = None
    effective_from: str | None = None
    effective_until: str | None = None
    verified_on: str | None = None
    source_url: str = ""
    confidence: str = CARRIED_FORWARD

    @property
    def key(self) -> str:
        """Provider/model lookup key."""
        return make_pricing_key(self.provider, self.model)

    def covers(self, moment: datetime) -> bool:
        """Return whether this schedule was in effect at ``moment``."""
        if self.effective_from and moment < _parse_boundary(self.effective_from):
            return False
        if self.effective_until and moment >= _parse_boundary(self.effective_until):
            return False
        return True

    def estimate(self, input_tokens: int, output_tokens: int, *, cached_input_tokens: int = 0) -> float:
        """Estimate request cost in USD, accounting for cached input when known."""
        cached_tokens = min(max(cached_input_tokens, 0), max(input_tokens, 0))
        regular_input_tokens = max(input_tokens, 0) - cached_tokens
        cached_rate = self.cached_input_per_1m if self.cached_input_per_1m is not None else self.input_per_1m
        input_cost = regular_input_tokens / 1_000_000 * self.input_per_1m
        cached_cost = cached_tokens / 1_000_000 * cached_rate
        output_cost = output_tokens / 1_000_000 * self.output_per_1m
        return round(input_cost + cached_cost + output_cost, 8)

    def to_dict(self) -> dict[str, object]:
        """Serialize pricing for config files and reports."""
        return asdict(self)


def make_pricing_key(provider: str, model: str) -> str:
    """Normalize a provider/model pair into a stable key."""
    return f"{provider.strip().lower()}:{model.strip().lower()}"


def _parse_boundary(value: str) -> datetime:
    """Parse a schedule boundary, accepting a bare date or a full timestamp."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class PricingGap:
    """A model whose rate could not be resolved for a moment in time."""

    provider: str
    model: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Serialize a pricing gap for the dashboard."""
        return asdict(self)


class PricingRegistry:
    """Lookup table for model token prices, resolved by point in time."""

    def __init__(self, prices: Iterable[ModelPricing] | None = None) -> None:
        self._schedules: dict[str, list[ModelPricing]] = {}
        for pricing in prices if prices is not None else default_pricing():
            self.add(pricing)

    def add(self, pricing: ModelPricing) -> None:
        """Add a rate schedule, replacing any schedule with the same window."""
        schedules = self._schedules.setdefault(pricing.key, [])
        for index, existing in enumerate(schedules):
            if existing.effective_from == pricing.effective_from:
                schedules[index] = pricing
                break
        else:
            schedules.append(pricing)
        schedules.sort(key=lambda item: item.effective_from or "")

    def get(self, provider: str, model: str, *, at: datetime | None = None) -> ModelPricing | None:
        """Return the rate in effect at ``at`` (default: now), if any."""
        schedules = self._schedules.get(make_pricing_key(provider, model))
        if not schedules:
            return None
        moment = _as_utc(at)
        for pricing in reversed(schedules):
            if pricing.covers(moment):
                return pricing
        return None

    def schedules(self, provider: str, model: str) -> list[ModelPricing]:
        """Return every known rate schedule for a model, oldest first."""
        return list(self._schedules.get(make_pricing_key(provider, model), []))

    def estimate(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_input_tokens: int = 0,
        at: datetime | None = None,
    ) -> float:
        """Estimate cost, returning 0.0 when no rate covers the moment.

        A 0.0 here means "unknown", not "free". Call :meth:`explain` when the
        caller needs to tell those apart.
        """
        pricing = self.get(provider, model, at=at)
        if pricing is None:
            return 0.0
        return pricing.estimate(input_tokens, output_tokens, cached_input_tokens=cached_input_tokens)

    def explain(self, provider: str, model: str, *, at: datetime | None = None) -> PricingGap | None:
        """Return why a model has no usable rate, or None when it prices fine."""
        schedules = self._schedules.get(make_pricing_key(provider, model))
        if not schedules:
            return PricingGap(provider, model, "No rate is known for this model.")
        if self.get(provider, model, at=at) is not None:
            return None
        moment = _as_utc(at)
        latest = schedules[-1]
        if latest.effective_until and moment >= _parse_boundary(latest.effective_until):
            return PricingGap(
                provider,
                model,
                f"Known rates end {latest.effective_until}; the provider's newer rate is not recorded yet.",
            )
        return PricingGap(provider, model, "No rate schedule covers this date.")

    def to_list(self) -> list[dict[str, object]]:
        """Serialize all rate schedules."""
        flattened = [pricing for schedules in self._schedules.values() for pricing in schedules]
        return [
            pricing.to_dict()
            for pricing in sorted(flattened, key=lambda item: (item.key, item.effective_from or ""))
        ]

    def save(self, path: str | Path) -> Path:
        """Write registry to JSON."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_list(), indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "PricingRegistry":
        """Load overrides over the current defaults when the file is present.

        This keeps new public catalog prices available after a Poliora update,
        while values in a workspace pricing file always take precedence.
        """
        source = Path(path)
        if not source.exists():
            return cls()

        raw = json.loads(source.read_text(encoding="utf-8"))
        registry = cls()
        for item in raw:
            registry.add(
                ModelPricing(
                    provider=str(item["provider"]),
                    model=str(item["model"]),
                    input_per_1m=float(item["input_per_1m"]),
                    output_per_1m=float(item["output_per_1m"]),
                    cached_input_per_1m=float(item["cached_input_per_1m"])
                    if item.get("cached_input_per_1m") is not None
                    else None,
                    note=str(item.get("note", "custom")),
                    effective_from=_optional_text(item.get("effective_from")),
                    effective_until=_optional_text(item.get("effective_until")),
                    verified_on=_optional_text(item.get("verified_on")),
                    source_url=str(item.get("source_url", "")),
                    confidence=str(item.get("confidence", CARRIED_FORWARD)),
                )
            )
        return registry


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _as_utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def estimate_cost_usd(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    registry: PricingRegistry | None = None,
    cached_input_tokens: int = 0,
    at: datetime | None = None,
) -> float:
    """Estimate token cost in USD."""
    active_registry = registry or PricingRegistry()
    return active_registry.estimate(
        provider,
        model,
        input_tokens,
        output_tokens,
        cached_input_tokens=cached_input_tokens,
        at=at,
    )


def _verified(
    provider: str,
    model: str,
    input_per_1m: float,
    output_per_1m: float,
    source_url: str,
    *,
    cached_input_per_1m: float | None = None,
    note: str = "public list price",
    effective_from: str | None = None,
    effective_until: str | None = None,
) -> ModelPricing:
    """Build a rate confirmed against the provider on PRICING_VERIFIED_ON."""
    return ModelPricing(
        provider=provider,
        model=model,
        input_per_1m=input_per_1m,
        output_per_1m=output_per_1m,
        note=note,
        cached_input_per_1m=cached_input_per_1m,
        effective_from=effective_from,
        effective_until=effective_until,
        verified_on=PRICING_VERIFIED_ON,
        source_url=source_url,
        confidence=VERIFIED,
    )


def _carried(
    provider: str,
    model: str,
    input_per_1m: float,
    output_per_1m: float,
    source_url: str,
    *,
    cached_input_per_1m: float | None = None,
    note: str = "not re-confirmed in the 2026-08-16 audit; verify before billing on it",
) -> ModelPricing:
    """Build a rate that predates the last audit and could not be re-confirmed."""
    return ModelPricing(
        provider=provider,
        model=model,
        input_per_1m=input_per_1m,
        output_per_1m=output_per_1m,
        note=note,
        cached_input_per_1m=cached_input_per_1m,
        source_url=source_url,
        confidence=CARRIED_FORWARD,
    )


def default_pricing() -> list[ModelPricing]:
    """Return the public list-price catalog, verified 2026-08-16.

    These are list prices, not contract prices. Anything marked
    ``CARRIED_FORWARD`` predates this audit and could not be re-confirmed —
    treat those as estimates until someone checks them against the provider.
    """
    return [
        *_anthropic_pricing(),
        *_openai_pricing(),
        *_google_pricing(),
        *_xai_pricing(),
        *_deepseek_pricing(),
        *_other_pricing(),
    ]


def _anthropic_pricing() -> list[ModelPricing]:
    """Anthropic rates. Cache reads bill at ~0.1x the input rate."""
    url = ANTHROPIC_PRICING_URL
    return [
        _verified("anthropic", "claude-fable-5", 10.00, 50.00, url, cached_input_per_1m=1.00),
        _verified("anthropic", "claude-mythos-5", 10.00, 50.00, url, cached_input_per_1m=1.00,
                  note="public list price; Project Glasswing access only"),
        _verified("anthropic", "claude-opus-5", 5.00, 25.00, url, cached_input_per_1m=0.50),
        _verified("anthropic", "claude-opus-4-8", 5.00, 25.00, url, cached_input_per_1m=0.50),
        _verified("anthropic", "claude-opus-4-7", 5.00, 25.00, url, cached_input_per_1m=0.50),
        _verified("anthropic", "claude-opus-4-6", 5.00, 25.00, url, cached_input_per_1m=0.50),
        # Sonnet 5 launched on an introductory rate that expires 2026-08-31.
        # Both windows are recorded so August spend never reprices in September.
        _verified("anthropic", "claude-sonnet-5", 2.00, 10.00, url, cached_input_per_1m=0.20,
                  note="introductory rate through 2026-08-31",
                  effective_until="2026-09-01"),
        _verified("anthropic", "claude-sonnet-5", 3.00, 15.00, url, cached_input_per_1m=0.30,
                  note="standard rate after the introductory period",
                  effective_from="2026-09-01"),
        _verified("anthropic", "claude-sonnet-4-6", 3.00, 15.00, url, cached_input_per_1m=0.30),
        _verified("anthropic", "claude-haiku-4-5", 1.00, 5.00, url, cached_input_per_1m=0.10),
        # Retired and deprecated models keep open-ended rates. A model going
        # away stops new usage; it does not change the rate that already-logged
        # usage should be priced at, and closing the window here would make
        # historical imports report as unpriced.
        _verified("anthropic", "claude-opus-4-1", 15.00, 75.00, url, note="retired 2026-08-05"),
        _verified("anthropic", "claude-opus-4-1-20250805", 15.00, 75.00, url, note="retired 2026-08-05"),
        _verified("anthropic", "claude-opus-4-20250514", 15.00, 75.00, url, note="deprecated"),
        _verified("anthropic", "claude-sonnet-4-20250514", 3.00, 15.00, url, note="deprecated"),
        _verified("anthropic", "claude-3-7-sonnet", 3.00, 15.00, url, note="retired 2026-02-19"),
        _verified("anthropic", "claude-3-7-sonnet-20250219", 3.00, 15.00, url, note="retired 2026-02-19"),
        _verified("anthropic", "claude-3-5-sonnet", 3.00, 15.00, url, note="retired 2025-10-28"),
        _verified("anthropic", "claude-3-5-haiku", 0.80, 4.00, url, note="retired 2026-02-19"),
        _verified("anthropic", "claude-3-5-haiku-20241022", 0.80, 4.00, url, note="retired 2026-02-19"),
        _verified("anthropic", "claude-3-haiku-20240307", 0.25, 1.25, url, note="deprecated; retires 2026-04-19"),
    ]


def _openai_pricing() -> list[ModelPricing]:
    """OpenAI rates. The GPT-5.6 ladder went GA on 2026-07-29."""
    url = OPENAI_PRICING_URL
    return [
        _verified("openai", "gpt-5.6-sol", 5.00, 30.00, url, cached_input_per_1m=0.50),
        _verified("openai", "gpt-5.6-terra", 2.00, 12.00, url, cached_input_per_1m=0.20),
        _verified("openai", "gpt-5.6-luna", 0.20, 1.20, url, cached_input_per_1m=0.02),
        _verified("openai", "gpt-5.6-cyber", 12.50, 75.00, url, cached_input_per_1m=1.25),
        _verified("openai", "gpt-5.5", 5.00, 30.00, url, cached_input_per_1m=0.50),
        _verified("openai", "gpt-5.5-cyber", 12.50, 75.00, url, cached_input_per_1m=1.25),
        _verified("openai", "gpt-5.5-pro", 30.00, 180.00, url),
        _verified("openai", "gpt-5.4", 2.50, 15.00, url, cached_input_per_1m=0.25),
        _verified("openai", "gpt-5.4-mini", 0.75, 4.50, url, cached_input_per_1m=0.075),
        _verified("openai", "gpt-5.4-nano", 0.20, 1.25, url, cached_input_per_1m=0.02),
        _verified("openai", "gpt-5.4-pro", 30.00, 180.00, url),
        # Prior generations, still served and still present in historical usage.
        _carried("openai", "gpt-4.1", 2.00, 8.00, url),
        _carried("openai", "gpt-4.1-mini", 0.40, 1.60, url),
        _carried("openai", "gpt-4o", 2.50, 10.00, url),
        _carried("openai", "gpt-4o-mini", 0.15, 0.60, url),
    ]


def _google_pricing() -> list[ModelPricing]:
    """Google rates. The Flash introductory rates double on 2027-01-01."""
    url = GOOGLE_PRICING_URL
    return [
        _verified("google", "gemini-3.7-flash", 0.75, 3.75, url,
                  note="introductory rate; doubles 2027-01-01",
                  effective_from="2026-08-13", effective_until="2027-01-01"),
        _verified("google", "gemini-3.7-flash", 1.50, 7.50, url,
                  note="standard rate after the introductory period",
                  effective_from="2027-01-01"),
        _verified("google", "gemini-3.6-flash", 0.75, 3.75, url,
                  note="introductory rate; doubles 2027-01-01",
                  effective_until="2027-01-01"),
        _verified("google", "gemini-3.6-flash", 1.50, 7.50, url,
                  note="standard rate after the introductory period",
                  effective_from="2027-01-01"),
        _verified("google", "gemini-3.5-flash", 1.50, 9.00, url, cached_input_per_1m=0.15),
        _verified("google", "gemini-3.1-pro-preview", 2.00, 12.00, url),
        _verified("google", "gemini-2.5-flash-lite", 0.10, 0.40, url),
        _carried("google", "gemini-3.6-pro", 2.00, 10.00, url, cached_input_per_1m=0.20),
        _carried("google", "gemini-3-flash-preview", 0.50, 3.00, url),
        _carried("google", "gemini-3.1-flash-lite", 0.25, 1.50, url),
        _carried("google", "gemini-1.5-pro", 1.25, 5.00, url),
        _carried("google", "gemini-1.5-flash", 0.075, 0.30, url),
    ]


def _xai_pricing() -> list[ModelPricing]:
    """xAI rates. Grok 4.6 reprices the whole request above 200K input tokens."""
    url = XAI_PRICING_URL
    return [
        _verified("xai", "grok-4.6", 2.00, 6.00, url, cached_input_per_1m=0.50,
                  note="rate for prompts under 200K input tokens",
                  effective_from="2026-08-12"),
        # xAI bills the entire request at the higher tier once a prompt crosses
        # 200K tokens. Poliora cannot yet select a rate by prompt size, so the
        # long-context tier is a separate entry the importer must target.
        _verified("xai", "grok-4.6-200k", 4.00, 12.00, url, cached_input_per_1m=1.00,
                  note="rate applied to the whole request at 200K+ input tokens",
                  effective_from="2026-08-12"),
        _verified("xai", "grok-4.5", 2.00, 6.00, url, cached_input_per_1m=0.30),
        _verified("xai", "grok-4.3", 1.25, 2.50, url, cached_input_per_1m=0.20),
        _verified("xai", "grok-4.1", 0.20, 0.50, url),
        _verified("xai", "grok-build-0.1", 1.00, 2.00, url, note="public list price; coding workloads"),
    ]


def _deepseek_pricing() -> list[ModelPricing]:
    """DeepSeek rates.

    DeepSeek replaced flat pricing with a peak/off-peak schedule at 16:00 UTC on
    2026-08-16. The successor rates are not recorded here yet, so by the
    known-successor rule these windows stay open and are marked low-confidence:
    an approximate rate with a visible warning beats a 0.00 that reads as free.
    """
    url = DEEPSEEK_PRICING_URL
    stale = "flat rate superseded by peak/off-peak pricing at 16:00 UTC 2026-08-16; re-verify"
    return [
        _carried("deepseek", "deepseek-v4-flash", 0.14, 0.28, url,
                 cached_input_per_1m=0.0028, note=stale),
        _carried("deepseek", "deepseek-v4-pro", 0.435, 0.87, url,
                 cached_input_per_1m=0.003625, note=stale),
    ]


def _other_pricing() -> list[ModelPricing]:
    """Rates that predate this audit, plus the local-compute placeholder."""
    return [
        _carried("mistral", "mistral-medium-3-5", 1.50, 7.50, MISTRAL_PRICING_URL),
        ModelPricing(
            provider="local", model="local-model",
            input_per_1m=0.00, output_per_1m=0.00,
            note="local model; track compute separately",
            confidence=VERIFIED, verified_on=PRICING_VERIFIED_ON,
        ),
    ]
