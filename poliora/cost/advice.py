"""Turn capacity, attribution, and spare plans into one decision.

Separate reports are not advice. A person who is about to lose an afternoon to a
rate limit does not want three dashboards, they want a sentence telling them
what to do about it.

Every suggestion here is derived from something already measured. Nothing is
generated when the evidence is missing: an empty list is the correct output for
a quiet day, and saying nothing is better than manufacturing an insight to fill
the space.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from poliora.cost.capacity import PeakContext, RunwayForecast
from poliora.cost.local_usage import LocalUsageScan
from poliora.cost.workflows import WorkflowReport

# How urgent an action is. "now" means the wall is close enough to change what
# you do next; "soon" is worth planning around; "note" is context.
NOW = "now"
SOON = "soon"
NOTE = "note"


@dataclass(frozen=True)
class Suggestion:
    """One thing to do, and the measurement that justifies it."""

    urgency: str
    headline: str
    because: str
    action: str

    def to_dict(self) -> dict[str, object]:
        """Serialize a suggestion."""
        return asdict(self)


def build_advice(
    *,
    forecast: RunwayForecast,
    context: PeakContext | None = None,
    workflows: WorkflowReport | None = None,
    other_plan: LocalUsageScan | None = None,
) -> list[Suggestion]:
    """Assemble the day's suggestions, most urgent first."""
    suggestions: list[Suggestion] = []
    suggestions.extend(_runway_suggestions(forecast, context))
    suggestions.extend(_arbitrage_suggestions(forecast, other_plan))
    suggestions.extend(_attribution_suggestions(forecast, workflows))
    order = {NOW: 0, SOON: 1, NOTE: 2}
    suggestions.sort(key=lambda item: order.get(item.urgency, 3))
    return suggestions


def _runway_suggestions(forecast: RunwayForecast, context: PeakContext | None) -> list[Suggestion]:
    """Warn about the wall only when there is a measured basis for it."""
    remaining = forecast.remaining_tokens
    time_left = forecast.time_remaining()

    if forecast.ceiling.is_known and remaining is not None and remaining <= 0:
        return [
            Suggestion(
                urgency=NOW,
                headline="This window is spent.",
                because=forecast.ceiling.describe(),
                action=(
                    f"Wait for the reset at {forecast.resets_at.astimezone():%H:%M}"
                    if forecast.resets_at
                    else "Wait for the window to roll, or move this work to another plan."
                ),
            )
        ]

    if time_left is not None and time_left.total_seconds() < 3600:
        minutes = int(time_left.total_seconds() // 60)
        return [
            Suggestion(
                urgency=NOW,
                headline=f"About {minutes} minutes of capacity left at the current pace.",
                because=f"{forecast.used_pct:.0f}% of the window is used and burn is "
                f"{forecast.burn_tokens_per_hour:,.0f} tokens/hour.",
                action="Finish the task in flight before starting anything large.",
            )
        ]

    # No ceiling: the user's own history is still a usable reference point.
    if not forecast.ceiling.is_known and context is not None and context.is_meaningful:
        if context.percentile is not None and context.percentile >= 85:
            return [
                Suggestion(
                    urgency=SOON,
                    headline="This is one of your heaviest windows.",
                    because=context.describe(),
                    action="Poliora has not measured your limit yet, but this pace is unusual for you.",
                )
            ]
    return []


def _arbitrage_suggestions(forecast: RunwayForecast, other: LocalUsageScan | None) -> list[Suggestion]:
    """Point at spare capacity on a plan the user already pays for.

    No single vendor can make this comparison: Anthropic cannot see a Codex
    quota and OpenAI cannot see a Claude window.
    """
    if other is None or not other.available or other.plan is None:
        return []
    spare = other.plan.quota_used_pct
    used = forecast.used_pct
    if spare is None or used is None or used < 60 or spare > 40:
        return []
    return [
        Suggestion(
            urgency=SOON,
            headline=f"{other.display_name} has spare capacity you already pay for.",
            because=f"This window is {used:.0f}% used while {other.display_name} sits at {spare:.0f}%.",
            action="Send mechanical work -- tests, refactors, boilerplate -- there and keep this plan "
            "for the parts that need it.",
        )
    ]


def _attribution_suggestions(forecast: RunwayForecast, workflows: WorkflowReport | None) -> list[Suggestion]:
    """Name the project consuming the capacity, when one clearly dominates."""
    if workflows is None:
        return []
    top = workflows.dominant
    if top is None or top.share_pct < 50 or len(workflows.projects) < 2:
        return []
    return [
        Suggestion(
            urgency=NOTE,
            headline=f"{top.project} is where your capacity goes.",
            because=f"It used {top.share_pct:.0f}% of tracked tokens over the last "
            f"{workflows.period_days} days.",
            action="If that project does not need your most expensive model, it is the highest-leverage "
            "place to change routing.",
        )
    ]
