"""Budget checks for local reports and CI gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from poliora.cost.reports import UsageReport


@dataclass(frozen=True)
class BudgetCheck:
    """Result of comparing projected spend against a budget."""

    passed: bool
    projected_monthly_usd: float
    limit_usd: float
    used_pct: float
    remaining_usd: float
    message: str

    def to_dict(self) -> dict[str, object]:
        """Serialize budget check."""
        return asdict(self)


def check_budget(report: UsageReport, *, limit_usd: float, warn_at_pct: float = 80.0) -> BudgetCheck:
    """Check whether projected monthly spend is within a budget."""
    if limit_usd <= 0:
        raise ValueError("Budget limit must be greater than zero.")
    if warn_at_pct <= 0:
        raise ValueError("warn_at_pct must be greater than zero.")

    projected = report.projected_monthly_usd
    used_pct = round(projected / limit_usd * 100, 2)
    remaining = round(limit_usd - projected, 2)
    passed = projected <= limit_usd

    if not passed:
        message = f"Projected AI spend is ${projected:,.2f}, above the ${limit_usd:,.2f} limit."
    elif used_pct >= warn_at_pct:
        message = f"Projected AI spend is at {used_pct:.1f}% of budget. Watch this closely."
    else:
        message = f"Projected AI spend is within budget with ${remaining:,.2f} remaining."

    return BudgetCheck(
        passed=passed,
        projected_monthly_usd=projected,
        limit_usd=limit_usd,
        used_pct=used_pct,
        remaining_usd=remaining,
        message=message,
    )
