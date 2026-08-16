"""What-if model routing simulations for AI spend."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from poliora.cost.pricing import CARRIED_FORWARD, ModelPricing, PricingRegistry
from poliora.cost.reports import build_usage_report
from poliora.cost.usage import UsageEvent


@dataclass(frozen=True)
class ModelSwitchSimulation:
    """Estimated impact of routing part of a workload to another model."""

    source_provider: str
    source_model: str
    target_provider: str
    target_model: str
    percentage: float
    matched_requests: int
    affected_requests: float
    source_cost_usd: float
    affected_current_cost_usd: float
    estimated_target_cost_usd: float
    estimated_savings_usd: float
    estimated_savings_pct: float
    observed_days: float
    estimated_monthly_savings_usd: float
    target_rate_warning: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize the simulation result."""
        return asdict(self)


def simulate_model_switch(
    events: Iterable[UsageEvent],
    *,
    source_provider: str,
    source_model: str,
    target_provider: str,
    target_model: str,
    percentage: float = 100.0,
    registry: PricingRegistry | None = None,
) -> ModelSwitchSimulation:
    """Estimate savings from routing a portion of one model's traffic elsewhere.

    Current cost comes from the usage log. Proposed cost is based on the
    editable Poliora pricing registry, so vendor contract prices are visible.
    """
    if not 0 < percentage <= 100:
        raise ValueError("percentage must be greater than 0 and no more than 100")

    active_registry = registry or PricingRegistry()
    target_pricing = active_registry.get(target_provider, target_model)
    if target_pricing is None:
        raise ValueError(
            f"No pricing found for {target_provider}/{target_model}. "
            "Add it to .poliora/pricing.json before running a simulation."
        )

    matched = [
        event
        for event in events
        if event.provider.strip().lower() == source_provider.strip().lower()
        and event.model.strip().lower() == source_model.strip().lower()
    ]
    ratio = percentage / 100
    source_cost = round(sum(event.cost_usd for event in matched), 6)
    affected_current_cost = round(source_cost * ratio, 6)
    estimated_target_cost = round(
        sum(
            target_pricing.estimate(
                round(event.input_tokens * ratio),
                round(event.output_tokens * ratio),
                cached_input_tokens=round(event.cached_input_tokens * ratio),
            )
            + event.tool_cost_usd * ratio
            for event in matched
        ),
        6,
    )
    savings = round(affected_current_cost - estimated_target_cost, 6)
    savings_pct = round(savings / affected_current_cost * 100, 2) if affected_current_cost else 0.0
    source_report = build_usage_report(matched)
    monthly_savings = round(savings / source_report.observed_days * 30.44, 2) if matched else 0.0

    return ModelSwitchSimulation(
        source_provider=source_provider,
        source_model=source_model,
        target_provider=target_provider,
        target_model=target_model,
        percentage=percentage,
        matched_requests=len(matched),
        affected_requests=round(len(matched) * ratio, 2),
        source_cost_usd=source_cost,
        affected_current_cost_usd=affected_current_cost,
        estimated_target_cost_usd=estimated_target_cost,
        estimated_savings_usd=savings,
        estimated_savings_pct=savings_pct,
        observed_days=source_report.observed_days,
        estimated_monthly_savings_usd=monthly_savings,
        target_rate_warning=_target_rate_warning(target_pricing, active_registry, target_provider, target_model),
    )


def _target_rate_warning(
    target_pricing: ModelPricing,
    registry: PricingRegistry,
    provider: str,
    model: str,
) -> str:
    """Warn when a projection rests on a rate that is about to change.

    Monthly savings are projected forward, so an introductory rate that expires
    inside the projection window would quietly inflate the result.
    """
    if target_pricing.confidence == CARRIED_FORWARD:
        return (
            f"{provider}/{model} pricing was not confirmed in the last rate audit. "
            "Verify it against your provider before acting on this projection."
        )
    if not target_pricing.effective_until:
        return ""
    successor = next(
        (
            item
            for item in registry.schedules(provider, model)
            if item.effective_from == target_pricing.effective_until
        ),
        None,
    )
    if successor is None:
        return (
            f"{provider}/{model} pricing changes on {target_pricing.effective_until}; "
            "the new rate is not recorded yet."
        )
    return (
        f"This projection uses the rate in effect until {target_pricing.effective_until}. "
        f"After that {model} bills ${successor.input_per_1m:.2f}/${successor.output_per_1m:.2f} per 1M tokens, "
        "so realized savings will be lower."
    )
