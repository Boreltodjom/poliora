"""Local AI subscription plans and value summaries.

A subscription is a person-approved monthly payment. Poliora compares that
payment with content-free usage metadata observed locally. "Equivalent API
value" is a comparison at public API rates, never a claim about a provider
invoice or a recommendation to cancel automatically.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from poliora.cost.reports import UsageReport
from poliora.cost.usage import UsageEvent


@dataclass(frozen=True)
class SubscriptionPlan:
    """One person-confirmed recurring AI plan stored only in the workspace."""

    id: str
    tool: str
    display_name: str
    monthly_cost_usd: float
    plan_name: str = ""
    renewal_day: int | None = None

    def __post_init__(self) -> None:
        if not self.tool.strip():
            raise ValueError("Choose the AI tool for this plan.")
        if not self.display_name.strip():
            raise ValueError("Give this plan a name.")
        if not 0 <= self.monthly_cost_usd <= 100_000:
            raise ValueError("Monthly price must be between $0 and $100,000.")
        if self.renewal_day is not None and not 1 <= self.renewal_day <= 31:
            raise ValueError("Renewal day must be between 1 and 31.")

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SubscriptionPlan":
        renewal = data.get("renewal_day")
        return cls(
            id=str(data.get("id") or uuid4().hex),
            tool=str(data.get("tool") or "").strip(),
            display_name=str(data.get("display_name") or "").strip(),
            monthly_cost_usd=round(float(data.get("monthly_cost_usd", 0.0)), 2),
            plan_name=str(data.get("plan_name") or "").strip(),
            renewal_day=int(renewal) if renewal not in (None, "") else None,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SubscriptionStore:
    """Small atomic JSON store for local plan details."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> list[SubscriptionPlan]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        plans: list[SubscriptionPlan] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                plans.append(SubscriptionPlan.from_dict(item))
            except (TypeError, ValueError):
                continue
        return plans

    def save(self, plan: SubscriptionPlan) -> SubscriptionPlan:
        plans = self.read_all()
        replacement = [item for item in plans if item.id != plan.id]
        replacement.append(plan)
        self._write(replacement)
        return plan

    def delete(self, plan_id: str) -> bool:
        plans = self.read_all()
        remaining = [item for item in plans if item.id != plan_id]
        if len(remaining) == len(plans):
            return False
        self._write(remaining)
        return True

    def _write(self, plans: list[SubscriptionPlan]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps([item.to_dict() for item in plans], indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def new_subscription_plan(payload: dict[str, object]) -> SubscriptionPlan:
    """Validate dashboard input and assign an opaque local identifier."""
    data = dict(payload)
    data["id"] = str(data.get("id") or uuid4().hex)
    return SubscriptionPlan.from_dict(data)


def summarize_plan_stack(
    plans: list[SubscriptionPlan],
    events: list[UsageEvent],
    report: UsageReport,
) -> dict[str, object]:
    """Return transparent plan value context for the daily companion.

    The function deliberately refuses to label a plan "cancel". Local usage is
    a useful signal, but service quality, limits, and the person's workflow are
    part of the decision and cannot be inferred from token counts.
    """
    observed_values: dict[str, float] = {}
    observed_requests: dict[str, int] = {}
    for event in events:
        tool = _tool_for_event(event)
        observed_values[tool] = observed_values.get(tool, 0.0) + _equivalent_value(event)
        observed_requests[tool] = observed_requests.get(tool, 0) + 1

    rows: list[dict[str, object]] = []
    for plan in plans:
        equivalent = round(observed_values.get(plan.tool, 0.0), 2)
        requests = observed_requests.get(plan.tool, 0)
        monthly_equivalent = round(equivalent / report.observed_days * 30.44, 2) if report.observed_days else 0.0
        ratio = round(monthly_equivalent / plan.monthly_cost_usd, 2) if plan.monthly_cost_usd else None
        status, explanation = _plan_status(plan, requests, monthly_equivalent, report.observed_days)
        rows.append(
            {
                **plan.to_dict(),
                "requests_observed": requests,
                "equivalent_api_value_usd": equivalent,
                "monthly_equivalent_api_value_usd": monthly_equivalent,
                "value_multiple": ratio,
                "status": status,
                "explanation": explanation,
            }
        )

    monthly_cost = round(sum(plan.monthly_cost_usd for plan in plans), 2)
    equivalent_total = round(sum(_equivalent_value(event) for event in events), 2)
    monthly_equivalent = round(equivalent_total / report.observed_days * 30.44, 2) if report.observed_days else 0.0
    plan_count = len(plans)
    if not plans:
        next_action = "Add the plans you pay for so Poliora can turn usage into a monthly value decision."
        headline = "Your plan stack is not set up yet"
    elif report.requests == 0:
        next_action = "Refresh local history or import an approved usage export before reviewing plan value."
        headline = f"${monthly_cost:,.2f}/mo across {plan_count} AI plan{'s' if plan_count != 1 else ''}"
    else:
        next_action = "Review any plan marked for attention. Equivalent API value is a comparison, not your bill."
        headline = f"${monthly_cost:,.2f}/mo across {plan_count} AI plan{'s' if plan_count != 1 else ''}"

    return {
        "monthly_cost_usd": monthly_cost,
        "plans": rows,
        "plan_count": plan_count,
        "equivalent_api_value_usd": equivalent_total,
        "monthly_equivalent_api_value_usd": monthly_equivalent,
        "observed_days": report.observed_days,
        "headline": headline,
        "next_action": next_action,
        "notice": (
            "Equivalent API value uses public API list rates for comparison. It is not provider "
            "billing, invoice data, or an automatic cancellation recommendation."
        ),
    }


def _tool_for_event(event: UsageEvent) -> str:
    operation = event.operation.strip().lower()
    if operation in {"claude-code", "claude_code"}:
        return "claude-code"
    if operation in {"codex", "codex-cli", "codex_cli"}:
        return "codex"
    if "cursor" in operation:
        return "cursor"
    if "antigravity" in operation:
        return "antigravity"
    return operation or event.provider.strip().lower()


def _equivalent_value(event: UsageEvent) -> float:
    raw = event.metadata.get("equivalent_api_cost_usd", 0.0)
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _plan_status(
    plan: SubscriptionPlan,
    requests: int,
    monthly_equivalent: float,
    observed_days: float,
) -> tuple[str, str]:
    if not requests:
        return "needs-usage", "No matching local usage has been observed for this plan yet."
    if observed_days < 21:
        return "watch", "Keep collecting usage before making a plan decision."
    if plan.monthly_cost_usd and monthly_equivalent < plan.monthly_cost_usd * 0.6:
        return (
            "review",
            "Usage is low relative to this plan's monthly price. Check overlap, limits, and whether "
            "the plan still fits your workflow.",
        )
    if plan.monthly_cost_usd and monthly_equivalent < plan.monthly_cost_usd:
        return (
            "watch",
            "Usage is below the plan price at public API list rates. Review again after another billing cycle.",
        )
    return "healthy", "Observed usage is substantial relative to this plan's monthly price."
