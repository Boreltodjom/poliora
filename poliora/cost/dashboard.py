"""Standalone HTML report generation for Poliora."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

from poliora.cost.decisions import SavingsDecision, summarize_decisions
from poliora.cost.recommendations import Recommendation
from poliora.cost.reports import BreakdownRow, UsageReport


@dataclass(frozen=True)
class ReportBranding:
    """Optional identity applied to a client-facing report."""

    organization: str = "Poliora"
    client: str = ""
    prepared_by: str = ""
    title: str = ""
    accent_color: str = "#087c59"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.accent_color):
            raise ValueError("Report accent color must be a six-digit hex color such as #087c59.")
        for field_name in ("organization", "client", "prepared_by", "title"):
            if len(getattr(self, field_name)) > 120:
                raise ValueError(f"Report {field_name.replace('_', ' ')} must be 120 characters or fewer.")


def write_html_report(
    path: str | Path,
    report: UsageReport,
    recommendations: list[Recommendation],
    *,
    project: str,
    branding: ReportBranding | None = None,
    decisions: list[SavingsDecision] | None = None,
) -> Path:
    """Write a dependency-free, shareable executive AI spend report."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_html_report(
            report,
            recommendations,
            project=project,
            branding=branding,
            decisions=decisions,
        ),
        encoding="utf-8",
    )
    return target


def render_html_report(
    report: UsageReport,
    recommendations: list[Recommendation],
    *,
    project: str,
    branding: ReportBranding | None = None,
    decisions: list[SavingsDecision] | None = None,
) -> str:
    """Render a polished static HTML report with no external assets."""
    identity = branding or ReportBranding()
    report_subject = identity.client or project
    report_title = identity.title or f"{report_subject} AI spend report"
    prepared_for = f"Prepared for {identity.client}" if identity.client else f"Workspace: {project}"
    prepared_by = f"Prepared by {identity.prepared_by}" if identity.prepared_by else "Prepared locally with Poliora"
    budget = _money(report.monthly_budget_usd) if report.monthly_budget_usd is not None else "Not set"
    budget_used = f"{report.budget_used_pct:.1f}%" if report.budget_used_pct is not None else "Not set"
    remaining = _money(report.budget_delta_usd) if report.budget_delta_usd is not None else "Not set"
    period = _period_label(report)
    decision_rows = decisions or []
    ledger = summarize_decisions(decision_rows)
    metrics = "".join(
        [
            _metric("Projected monthly spend", _money(report.projected_monthly_usd), "Based on observed usage"),
            _metric(
                "Tracked spend",
                _money(report.cost_usd),
                f"{report.requests:,} requests"
                + (
                    f" / {report.non_dollar_requests:,} subscription turns excluded"
                    if report.non_dollar_requests
                    else ""
                ),
            ),
            _metric(
                "Total tokens",
                f"{report.total_tokens:,}",
                f"{report.input_tokens:,} input / {report.output_tokens:,} output",
            ),
            _metric("Monthly budget", budget, f"{budget_used} used"),
        ]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(report_title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17212b;
      --muted: #5b6875;
      --line: #d9e1e7;
      --paper: #f7f9fa;
      --card: #fff;
      --green: {identity.accent_color};
      --green-soft: #dff5ea;
      --blue: #2864c7;
      --amber-soft: #fff1cc;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 40px 24px 56px; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-start;
      padding-bottom: 30px;
      border-bottom: 1px solid var(--line);
    }}
    .brand {{
      color: var(--green);
      font-size: 14px;
      font-weight: 800;
      letter-spacing: 1.2px;
      text-transform: uppercase;
    }}
    h1 {{ margin: 8px 0 6px; font-size: 32px; line-height: 1.1; letter-spacing: 0; }}
    .subtitle {{ margin: 0; color: var(--muted); }}
    .generated {{ color: var(--muted); font-size: 13px; text-align: right; white-space: nowrap; }}
    .report-context {{ margin-top: 10px; color: var(--muted); font-size: 13px; }}
    section {{ margin-top: 32px; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .metric, .panel {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; }}
    .metric {{ padding: 18px; min-height: 126px; }}
    .metric label {{ color: var(--muted); display: block; font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 9px; font-size: 24px; letter-spacing: 0; }}
    .metric span {{ color: var(--muted); display: block; margin-top: 5px; font-size: 12px; }}
    .panel {{ padding: 20px; }}
    .ledger {{
      display: grid;
      grid-template-columns: 1.2fr repeat(3, minmax(120px, .7fr));
      background: var(--ink);
      color: #fff;
      border: 1px solid var(--ink);
    }}
    .ledger > div {{ padding: 16px; border-right: 1px solid #40505b; }}
    .ledger > div:last-child {{ border-right: 0; }}
    .ledger label {{ display: block; color: #aebac1; font-size: 10px; text-transform: uppercase; }}
    .ledger strong {{ display: block; margin-top: 5px; font-size: 21px; }}
    .ledger-copy strong {{ margin: 0; color: #51d7a7; font-size: 12px; text-transform: uppercase; }}
    .ledger-copy p {{ margin: 5px 0 0; color: #c1cbd0; font-size: 12px; }}
    .executive {{ margin-top: 22px; padding: 18px 20px; border-left: 4px solid var(--green); background: var(--card); }}
    .executive h2 {{ margin-bottom: 7px; }}
    .executive p {{ margin: 0; color: #364554; }}
    .grid {{ display: grid; grid-template-columns: 1.25fr 0.75fr; gap: 16px; }}
    .row {{ margin: 15px 0; }}
    .row-head {{ display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }}
    .row-head span {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar {{
      height: 8px;
      margin-top: 7px;
      background: #eaf0f3;
      border-radius: 99px;
      overflow: hidden;
    }}
    .bar > i {{ display: block; height: 100%; background: var(--blue); border-radius: inherit; }}
    .budget {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 215px;
      text-align: center;
      background: var(--green-soft);
      border: 1px solid #b9e6cd;
      border-radius: 8px;
    }}
    .budget strong {{ display: block; color: var(--green); font-size: 38px; line-height: 1; }}
    .budget span {{ display: block; margin-top: 9px; color: #285f48; font-size: 14px; }}
    .budget small {{ display: block; margin-top: 16px; color: #285f48; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 600; text-align: left; text-transform: uppercase; }}
    th, td {{ padding: 11px 8px; border-bottom: 1px solid var(--line); }}
    th:not(:first-child), td:not(:first-child) {{ text-align: right; }}
    .rec {{
      display: grid;
      grid-template-columns: 110px 1fr auto;
      gap: 14px;
      padding: 15px 0;
      border-bottom: 1px solid var(--line);
      align-items: start;
    }}
    .rec:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .tag {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 99px;
      color: #765000;
      background: var(--amber-soft);
      font-size: 12px;
      font-weight: 700;
      text-align: center;
    }}
    .rec h3 {{ margin: 0; font-size: 15px; }}
    .rec p {{ margin: 4px 0 0; color: var(--muted); font-size: 13px; }}
    .savings {{ color: var(--green); font-size: 14px; font-weight: 750; text-align: right; white-space: nowrap; }}
    footer {{ margin-top: 32px; color: var(--muted); font-size: 12px; }}
    .disclosure {{
      padding: 16px 18px;
      border: 1px solid var(--line);
      background: #f0f4f6;
      color: var(--muted);
      font-size: 12px;
    }}
    .disclosure strong {{ color: var(--ink); }}
    @media print {{
      body {{ background: #fff; }}
      main {{ max-width: none; padding: 20px; }}
      .metric, .panel, .budget, .executive, .disclosure {{ break-inside: avoid; }}
    }}
    @media (max-width: 760px) {{
      main {{ padding: 28px 16px 42px; }}
      header {{ display: block; }}
      .generated {{ margin-top: 16px; text-align: left; white-space: normal; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .ledger {{ grid-template-columns: 1fr; }}
      .ledger > div {{ border-right: 0; border-bottom: 1px solid #40505b; }}
      .grid {{ grid-template-columns: 1fr; }}
      .rec {{ grid-template-columns: 1fr; gap: 6px; }}
      .savings {{ text-align: left; }}
      h1 {{ font-size: 27px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="brand">{escape(identity.organization or 'Poliora')}</div>
        <h1>{escape(report_title)}</h1>
        <p class="subtitle">{period} of tracked AI usage, cost drivers, and savings opportunities.</p>
        <p class="report-context">{escape(prepared_for)}<br>{escape(prepared_by)}</p>
      </div>
      <div class="generated">
        Generated {escape(_display_date(report.generated_at))}<br>
        All token rates are editable estimates.
      </div>
    </header>
    <section class="executive">
      <h2>Executive summary</h2>
      <p>{escape(_executive_summary(report, recommendations))}</p>
    </section>
    <section class="metrics">{metrics}</section>
    <section class="ledger">
      <div class="ledger-copy"><strong>Savings proof ledger</strong><p>{ledger.decisions:,} tracked decisions /
        {ledger.validated:,} quality validated. Modeled value is not money already saved.</p></div>
      <div><label>Modeled monthly</label><strong>{_money(ledger.modeled_monthly_savings_usd)}</strong></div>
      <div><label>Active tests</label><strong>{ledger.active_tests:,}</strong></div>
      <div><label>Realized monthly</label><strong>{_money(ledger.realized_monthly_savings_usd)}</strong></div>
    </section>
    <section class="grid">
      <div class="panel"><h2>Cost drivers by model</h2>{_breakdown_bars(report.by_model)}</div>
      <div class="budget"><div><strong>{budget_used}</strong><span>of monthly budget used</span>
        <small>Budget remaining: {remaining}<br>Forecast confidence: {escape(report.forecast_confidence)}</small>
      </div></div>
    </section>
    <section class="panel"><h2>Model breakdown</h2>{_breakdown_table(report.by_model)}</section>
    <section class="panel"><h2>Savings decisions</h2>{_decision_table(decision_rows)}</section>
    <section class="panel"><h2>Recommended next moves</h2>{_recommendations(recommendations)}</section>
    <section class="disclosure">
      <strong>How to read this report:</strong> tracked spend is the cost recorded or estimated for observed
      requests. Projected monthly spend extrapolates the observed period and is not an invoice. Savings
      opportunities are modeled scenarios, may overlap, and become measured savings only after a production
      change is validated. ChatGPT subscription turns are excluded from dollar spend.
    </section>
    <footer>
      Poliora uses usage data recorded by your application. Validate rates against your provider invoice before
      making purchasing decisions.
    </footer>
  </main>
</body>
</html>
"""


def _metric(label: str, value: str, detail: str) -> str:
    return (
        f'<article class="metric"><label>{escape(label)}</label><strong>{escape(value)}</strong>'
        f"<span>{escape(detail)}</span></article>"
    )


def _breakdown_bars(rows: list[BreakdownRow]) -> str:
    if not rows:
        return '<p class="subtitle">No model data yet.</p>'
    return "".join(
        (
            '<div class="row"><div class="row-head">'
            f"<span>{escape(row.name)}</span><strong>{_money(row.cost_usd)}</strong></div>"
            f'<div class="bar"><i style="width:{min(max(row.share_pct, 0), 100):.2f}%"></i></div></div>'
        )
        for row in rows[:6]
    )


def _breakdown_table(rows: list[BreakdownRow]) -> str:
    if not rows:
        return '<p class="subtitle">No usage has been recorded yet.</p>'
    table_rows = "".join(
        (
            f"<tr><td>{escape(row.name)}</td><td>{row.requests:,}</td><td>{row.total_tokens:,}</td>"
            f"<td>{_money(row.cost_usd)}</td><td>{row.share_pct:.1f}%</td></tr>"
        )
        for row in rows
    )
    return (
        "<table><thead><tr><th>Model</th><th>Requests</th><th>Tokens</th><th>Cost</th>"
        f"<th>Share</th></tr></thead><tbody>{table_rows}</tbody></table>"
    )


def _recommendations(items: list[Recommendation]) -> str:
    if not items:
        return '<p class="subtitle">Record more usage to unlock recommendations.</p>'
    return "".join(
        (
            '<article class="rec"><div><span class="tag">'
            f"{escape(item.priority)} priority</span></div><div><h3>{escape(item.title)}</h3>"
            f"<p>{escape(item.action)}</p></div><div class=\"savings\">Save about "
            f"{_money(item.estimated_monthly_savings_usd)} / mo<br><small>"
            f"{item.estimated_savings_pct:.0f}% opportunity</small></div></article>"
        )
        for item in items
    )


def _decision_table(items: list[SavingsDecision]) -> str:
    if not items:
        return '<p class="subtitle">No optimization decisions are being tracked yet.</p>'
    table_rows = "".join(
        (
            f"<tr><td>{escape(item.name)}</td><td>{escape(item.status.replace('-', ' '))}</td>"
            f"<td>{escape(item.quality_status)}</td><td>{_money(item.estimated_monthly_savings_usd)}</td>"
            f"<td>{_measured_value(item)}</td></tr>"
        )
        for item in items
    )
    return (
        "<table><thead><tr><th>Decision</th><th>Status</th><th>Quality</th><th>Modeled / mo</th>"
        f"<th>Measured / mo</th></tr></thead><tbody>{table_rows}</tbody></table>"
    )


def _measured_value(item: SavingsDecision) -> str:
    if item.measured_monthly_savings_usd is None:
        return "Not measured"
    return _money(item.measured_monthly_savings_usd)


def _executive_summary(report: UsageReport, recommendations: list[Recommendation]) -> str:
    if report.requests == 0:
        return (
            "No usage has been recorded for this period. Connect a source or import usage before making a "
            "cost decision."
        )
    top_model = report.by_model[0] if report.by_model else None
    top_model_text = (
        f" The largest cost driver is {top_model.name}, representing {top_model.share_pct:.1f}% of tracked spend."
        if top_model
        else ""
    )
    largest_opportunity = max(
        (item.estimated_monthly_savings_usd for item in recommendations),
        default=0.0,
    )
    opportunity_text = (
        f" The largest individual modeled opportunity is about {_money(largest_opportunity)} per month; "
        "validate quality before changing traffic."
        if largest_opportunity > 0
        else " More usage is needed before Poliora can quantify a savings opportunity."
    )
    return (
        f"Poliora tracked {report.requests:,} requests and {_money(report.cost_usd)} during the observed period. "
        f"The current run-rate projects {_money(report.projected_monthly_usd)} per month at "
        f"{report.forecast_confidence.lower()} confidence."
        f"{top_model_text}{opportunity_text}"
    )


def _money(value: float | None) -> str:
    return "Not set" if value is None else f"${value:,.2f}"


def _period_label(report: UsageReport) -> str:
    if not report.period_start or not report.period_end:
        return "No usage period"
    return f"{_display_date(report.period_start)} to {_display_date(report.period_end)}"


def _display_date(value: str) -> str:
    return value.replace("T", " ").replace("+00:00", " UTC").split(".")[0]
