"""Rule-based AI spend recommendations."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from poliora.cost.reports import UsageReport


@dataclass(frozen=True)
class Recommendation:
    """Actionable cost-saving recommendation."""

    title: str
    reason: str
    action: str
    estimated_savings_pct: float
    estimated_monthly_savings_usd: float = 0.0
    priority: str = "medium"

    def to_dict(self) -> dict[str, object]:
        """Serialize recommendation."""
        return asdict(self)


def generate_recommendations(report: UsageReport, *, target_savings_pct: float = 40.0) -> list[Recommendation]:
    """Generate practical savings recommendations from a report."""
    if report.requests == 0:
        return [
            Recommendation(
                title="Start collecting usage data",
                reason="No AI usage events were found, so Poliora cannot identify waste yet.",
                action="Run `poliora record` manually or add the Python SDK around your AI calls.",
                estimated_savings_pct=0.0,
                estimated_monthly_savings_usd=0.0,
                priority="high",
            )
        ]

    recommendations: list[Recommendation] = []

    if report.monthly_budget_usd and report.projected_monthly_usd > report.monthly_budget_usd:
        over_by = report.projected_monthly_usd - report.monthly_budget_usd
        recommendations.append(
            _make_recommendation(
                report,
                title="Add a monthly AI budget gate",
                reason=f"Projected spend is ${over_by:.2f} above the configured monthly budget.",
                action="Set a per-project budget and alert when projected spend crosses 80 percent.",
                estimated_savings_pct=min(target_savings_pct, 25.0),
                priority="high",
            )
        )

    top_model = report.by_model[0] if report.by_model else None
    if top_model and top_model.share_pct >= 50:
        recommendations.append(
            _make_recommendation(
                report,
                title="Benchmark the top cost model",
                reason=f"{top_model.name} accounts for {top_model.share_pct:.1f}% of tracked spend.",
                action="Run a cheaper model on 50 real tasks and compare quality before switching simple workflows.",
                estimated_savings_pct=20.0,
                priority="high" if top_model.share_pct >= 70 else "medium",
            )
        )

    output_share = report.output_tokens / max(report.total_tokens, 1) * 100
    if output_share >= 45:
        recommendations.append(
            _make_recommendation(
                report,
                title="Cap verbose responses",
                reason=f"Output tokens are {output_share:.1f}% of total usage, which often means replies are too long.",
                action=(
                    "Set max output tokens, ask for structured answers, "
                    "and summarize long responses in a second pass."
                ),
                estimated_savings_pct=10.0,
                priority="medium",
            )
        )

    avg_tokens = report.total_tokens / max(report.requests, 1)
    if avg_tokens >= 4000:
        recommendations.append(
            _make_recommendation(
                report,
                title="Trim prompt context",
                reason=f"Average request size is {avg_tokens:.0f} tokens.",
                action="Use retrieval filters, context compression, and prompt templates with strict field selection.",
                estimated_savings_pct=15.0,
                priority="medium",
            )
        )

    if report.requests >= 100:
        recommendations.append(
            _make_recommendation(
                report,
                title="Cache repeated work",
                reason=f"{report.requests} requests were recorded in the reporting window.",
                action="Cache deterministic prompts and repeated retrieval results before calling premium models.",
                estimated_savings_pct=10.0,
                priority="medium",
            )
        )

    if not recommendations:
        recommendations.append(
            _make_recommendation(
                report,
                title="Create a model routing policy",
                reason="Spend is currently modest, so the best next move is preventing future drift.",
                action="Route easy tasks to cheaper models and reserve premium models for complex or high-risk calls.",
                estimated_savings_pct=10.0,
                priority="low",
            )
        )

    return recommendations[:5]


def _make_recommendation(
    report: UsageReport,
    *,
    title: str,
    reason: str,
    action: str,
    estimated_savings_pct: float,
    priority: str,
) -> Recommendation:
    monthly_savings = round(report.projected_monthly_usd * estimated_savings_pct / 100, 2)
    return Recommendation(
        title=title,
        reason=reason,
        action=action,
        estimated_savings_pct=estimated_savings_pct,
        estimated_monthly_savings_usd=monthly_savings,
        priority=priority,
    )
