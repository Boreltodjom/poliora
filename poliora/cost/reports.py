"""Usage reporting for AI spend tracking."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from poliora.cost.usage import UsageEvent, parse_timestamp


@dataclass(frozen=True)
class BreakdownRow:
    """Aggregated usage row for a model, operation, provider, or project."""

    name: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cost_usd: float
    tool_cost_usd: float
    share_pct: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DailySpendRow:
    """Spend, request volume, and optional subscription value for one UTC day."""

    date: str
    requests: int
    total_tokens: int
    cost_usd: float
    equivalent_api_value_usd: float = 0.0
    subscription_requests: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SpendAnomaly:
    """A daily spend increase that materially exceeds prior daily spend."""

    date: str
    cost_usd: float
    baseline_cost_usd: float
    increase_pct: float
    severity: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class UsageReport:
    """Aggregated spend report."""

    generated_at: str
    period_start: str | None
    period_end: str | None
    observed_days: float
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cost_usd: float
    tool_cost_usd: float
    projected_monthly_usd: float
    forecast_confidence: str
    forecast_confidence_reason: str
    monthly_budget_usd: float | None
    budget_delta_usd: float | None
    budget_used_pct: float | None
    non_dollar_requests: int
    by_model: list[BreakdownRow] = field(default_factory=list)
    by_provider: list[BreakdownRow] = field(default_factory=list)
    by_operation: list[BreakdownRow] = field(default_factory=list)
    by_project: list[BreakdownRow] = field(default_factory=list)
    by_user: list[BreakdownRow] = field(default_factory=list)
    daily_spend: list[DailySpendRow] = field(default_factory=list)
    spend_anomalies: list[SpendAnomaly] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "observed_days": self.observed_days,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "tool_cost_usd": self.tool_cost_usd,
            "projected_monthly_usd": self.projected_monthly_usd,
            "forecast_confidence": self.forecast_confidence,
            "forecast_confidence_reason": self.forecast_confidence_reason,
            "monthly_budget_usd": self.monthly_budget_usd,
            "budget_delta_usd": self.budget_delta_usd,
            "budget_used_pct": self.budget_used_pct,
            "non_dollar_requests": self.non_dollar_requests,
            "by_model": [row.to_dict() for row in self.by_model],
            "by_provider": [row.to_dict() for row in self.by_provider],
            "by_operation": [row.to_dict() for row in self.by_operation],
            "by_project": [row.to_dict() for row in self.by_project],
            "by_user": [row.to_dict() for row in self.by_user],
            "daily_spend": [row.to_dict() for row in self.daily_spend],
            "spend_anomalies": [item.to_dict() for item in self.spend_anomalies],
        }

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target

    def write_csv(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = [
                "name", "requests", "input_tokens", "output_tokens",
                "cached_input_tokens", "reasoning_tokens", "total_tokens",
                "cost_usd", "tool_cost_usd", "share_pct",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(row.to_dict() for row in self.by_model)
        return target


def build_usage_report(events: Iterable[UsageEvent], *, monthly_budget_usd: float | None = None) -> UsageReport:
    """Aggregate usage events into a report."""
    event_list = list(events)
    now = datetime.now(timezone.utc).isoformat()
    if not event_list:
        return UsageReport(
            generated_at=now, period_start=None, period_end=None, observed_days=0.0,
            requests=0, input_tokens=0, output_tokens=0, cached_input_tokens=0,
            reasoning_tokens=0, total_tokens=0, cost_usd=0.0, tool_cost_usd=0.0,
            projected_monthly_usd=0.0, forecast_confidence="No data",
            forecast_confidence_reason="Record usage across several days before relying on a forecast.",
            monthly_budget_usd=monthly_budget_usd,
            budget_delta_usd=monthly_budget_usd if monthly_budget_usd is not None else None,
            budget_used_pct=0.0 if monthly_budget_usd else None,
            non_dollar_requests=0,
        )

    timestamps = [parse_timestamp(event.timestamp) for event in event_list]
    start, end = min(timestamps), max(timestamps)
    observed_days = max((end - start).total_seconds() / 86400, 1.0)
    total_cost = round(sum(event.cost_usd for event in event_list), 6)
    projected_monthly = round(total_cost / observed_days * 30.44, 2)
    budget_delta = round(monthly_budget_usd - projected_monthly, 2) if monthly_budget_usd is not None else None
    budget_used_pct = round(projected_monthly / monthly_budget_usd * 100, 2) if monthly_budget_usd else None
    daily_spend = _daily_spend(event_list)
    confidence, confidence_reason = _forecast_confidence(observed_days)

    return UsageReport(
        generated_at=now, period_start=start.isoformat(), period_end=end.isoformat(),
        observed_days=round(observed_days, 2), requests=len(event_list),
        input_tokens=sum(event.input_tokens for event in event_list),
        output_tokens=sum(event.output_tokens for event in event_list),
        cached_input_tokens=sum(event.cached_input_tokens for event in event_list),
        reasoning_tokens=sum(event.reasoning_tokens for event in event_list),
        total_tokens=sum(event.total_tokens for event in event_list), cost_usd=total_cost,
        tool_cost_usd=round(sum(event.tool_cost_usd for event in event_list), 6),
        projected_monthly_usd=projected_monthly, forecast_confidence=confidence,
        forecast_confidence_reason=confidence_reason, monthly_budget_usd=monthly_budget_usd,
        budget_delta_usd=budget_delta, budget_used_pct=budget_used_pct,
        non_dollar_requests=sum(_is_non_dollar_activity(event) for event in event_list),
        by_model=_breakdown(event_list, lambda event: f"{event.provider}/{event.model}"),
        by_provider=_breakdown(event_list, lambda event: event.provider),
        by_operation=_breakdown(event_list, lambda event: event.operation),
        by_project=_breakdown(event_list, lambda event: event.project),
        by_user=_breakdown(event_list, lambda event: event.user or "Unassigned"),
        daily_spend=daily_spend, spend_anomalies=_spend_anomalies(daily_spend),
    )


def _is_non_dollar_activity(event: UsageEvent) -> bool:
    return event.metadata.get("billing_basis") in {
        "chatgpt-subscription", "subscription-included", "antigravity-subscription-activity",
    }


def _breakdown(events: list[UsageEvent], key_fn) -> list[BreakdownRow]:
    total_cost = sum(event.cost_usd for event in events)
    groups: dict[str, list[UsageEvent]] = {}
    for event in events:
        groups.setdefault(str(key_fn(event)), []).append(event)
    rows = [
        BreakdownRow(
            name=name, requests=len(group),
            input_tokens=sum(event.input_tokens for event in group),
            output_tokens=sum(event.output_tokens for event in group),
            cached_input_tokens=sum(event.cached_input_tokens for event in group),
            reasoning_tokens=sum(event.reasoning_tokens for event in group),
            total_tokens=sum(event.total_tokens for event in group),
            cost_usd=round(sum(event.cost_usd for event in group), 6),
            tool_cost_usd=round(sum(event.tool_cost_usd for event in group), 6),
            share_pct=round(sum(event.cost_usd for event in group) / total_cost * 100, 2) if total_cost else 0.0,
        )
        for name, group in groups.items()
    ]
    return sorted(rows, key=lambda row: row.cost_usd, reverse=True)


def _daily_spend(events: list[UsageEvent]) -> list[DailySpendRow]:
    groups: dict[str, list[UsageEvent]] = {}
    for event in events:
        day = parse_timestamp(event.timestamp).date().isoformat()
        groups.setdefault(day, []).append(event)
    return [
        DailySpendRow(
            date=day, requests=len(group), total_tokens=sum(event.total_tokens for event in group),
            cost_usd=round(sum(event.cost_usd for event in group), 6),
            equivalent_api_value_usd=round(sum(_equivalent_api_value(event) for event in group), 6),
            subscription_requests=sum(_is_non_dollar_activity(event) for event in group),
        )
        for day, group in sorted(groups.items())
    ]


def _equivalent_api_value(event: UsageEvent) -> float:
    raw = event.metadata.get("equivalent_api_cost_usd", 0.0)
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _forecast_confidence(observed_days: float) -> tuple[str, str]:
    if observed_days >= 21:
        return "High", f"Based on {observed_days:.0f} days of tracked usage."
    if observed_days >= 7:
        return "Medium", f"Based on {observed_days:.0f} days of tracked usage."
    return "Low", f"Based on only {observed_days:.0f} day(s) of tracked usage."


def _spend_anomalies(rows: list[DailySpendRow]) -> list[SpendAnomaly]:
    """Flag large daily jumps only after enough historical daily data exists."""
    anomalies: list[SpendAnomaly] = []
    for index, row in enumerate(rows):
        history = rows[max(0, index - 14) : index]
        if len(history) < 4:
            continue
        baseline = sum(item.cost_usd for item in history) / len(history)
        if baseline <= 0 or row.cost_usd <= baseline * 1.5:
            continue
        increase_pct = round((row.cost_usd / baseline - 1) * 100, 1)
        anomalies.append(
            SpendAnomaly(
                date=row.date,
                cost_usd=row.cost_usd,
                baseline_cost_usd=round(baseline, 6),
                increase_pct=increase_pct,
                severity="high" if increase_pct >= 100 else "medium",
            )
        )
    return anomalies
