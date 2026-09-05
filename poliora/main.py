"""Poliora CLI."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from poliora import __version__

app = typer.Typer(
    name="poliora",
    help="Poliora - find the AI coding usage your tools already recorded locally.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"poliora {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the Poliora version and exit.",
    ),
) -> None:
    """AI cost operations from the terminal or local dashboard."""


def _banner(subtitle: str = "AI spend control and sustainable model operations") -> None:
    """Print a startup banner."""
    title = Text("Poliora", style="bold green")
    subtitle_text = Text(f"v{__version__} - {subtitle}", style="dim")
    console.print(Panel(title + Text("\n") + subtitle_text, border_style="green", expand=False))


@app.command("scan")
def scan_command(
    export_json: Optional[Path] = typer.Option(None, "--json", help="Export system scan report to JSON file."),
) -> None:
    """Check which supported local AI tool launchers are available."""
    from poliora.cost import scan_system_ai_environment

    _banner("Local AI tool availability")
    console.print(
        "[bold cyan]Checking supported launchers without opening tools or reading private history...[/bold cyan]\n"
    )

    report = scan_system_ai_environment(".")

    tool_table = Table(title="Supported local AI tools", show_header=True)
    tool_table.add_column("AI Tool", style="cyan")
    tool_table.add_column("Status", justify="center")
    tool_table.add_column("What Poliora found")
    tool_table.add_column("Next step", style="green")

    for tool in report.scanned_tools:
        status = "[bold green]AVAILABLE[/bold green]" if tool.installed else "[dim]Not found[/dim]"
        tool_table.add_row(tool.name, status, tool.details, tool.next_step)

    console.print(tool_table)
    console.print()

    summary_text = (
        f"[bold white]Active AI Tools Found:[/bold white] {report.total_active_tools}\n"
        "[bold white]Measured spend found:[/bold white] No - a launcher scan cannot see billing or tokens.\n"
        "[bold yellow]Next useful action:[/bold yellow] "
        "Import an authorized usage export or connect a supported source."
    )
    console.print(
        Panel(
            summary_text,
            title="[bold green]What this scan can tell you[/bold green]",
            border_style="green",
        )
    )
    console.print()

    phase_table = Table(title="How Poliora turns data into a savings decision", show_header=True)
    phase_table.add_column("Workflow Phase", style="cyan")
    phase_table.add_column("Action Step")

    for key, data in sorted(report.phase_recommendations.items()):
        phase_table.add_row(data["phase"], data["action"])

    console.print(phase_table)
    console.print()
    console.print(
        "[dim]Run [bold white]poliora dashboard[/bold white] "
        "to open the local visual UI in your browser.[/dim]\n"
    )

    if export_json:
        try:
            export_json.parent.mkdir(parents=True, exist_ok=True)
            export_json.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        except OSError as error:
            console.print(f"[red]Could not save the scan report:[/red] {error}")
            raise typer.Exit(code=1) from error
        console.print(f"[green]Scan report saved to:[/green] {export_json}")


@app.command("init")
def init_command(
    project: str = typer.Option("default", "--project", "-p", help="Local project name."),
    monthly_budget: float = typer.Option(1000.0, "--monthly-budget", help="Monthly AI budget in USD."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing Poliora config and pricing."),
) -> None:
    """Create a local .poliora workspace."""
    from poliora.cost import init_workspace

    workspace = init_workspace(".", project=project, monthly_budget_usd=monthly_budget, overwrite=overwrite)
    console.print(f"[green]Created Poliora workspace:[/green] {workspace.workspace_dir}")
    console.print(f"[cyan]Project:[/cyan] {workspace.project}")
    console.print(f"[cyan]Monthly budget:[/cyan] ${workspace.monthly_budget_usd:,.2f}")
    console.print(f"[cyan]Usage log:[/cyan] {workspace.usage_path}")
    console.print(f"[cyan]Pricing file:[/cyan] {workspace.pricing_path}")


@app.command("pricing")
def pricing_command() -> None:
    """Show the editable starter pricing registry."""
    from poliora.cost import PricingRegistry, load_workspace

    workspace = load_workspace(".")
    registry = PricingRegistry.load(workspace.pricing_path)

    table = Table(title="Model pricing estimates (USD per 1M tokens)", show_header=True)
    table.add_column("Provider", style="cyan")
    table.add_column("Model", style="green")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Note", style="dim")

    for item in registry.to_list():
        table.add_row(
            str(item["provider"]),
            str(item["model"]),
            f"${float(item['input_per_1m']):,.4f}",
            f"${float(item['output_per_1m']):,.4f}",
            str(item["note"]),
        )

    console.print(table)
    console.print(f"\n[dim]Edit {workspace.pricing_path} to match your real vendor rates.[/dim]")


@app.command("models")
def models_command(
    provider: Optional[str] = typer.Option(None, "--provider", help="Only show one provider."),
) -> None:
    """Show the versioned model catalog and available token pricing."""
    from poliora.cost import ModelCatalog, PricingRegistry, load_workspace

    workspace = load_workspace(".")
    catalog = ModelCatalog.load(workspace.catalog_path)
    registry = PricingRegistry.load(workspace.pricing_path)
    selected_provider = provider.lower() if provider else None
    table = Table(title="Poliora Model Catalog", show_header=True)
    table.add_column("Provider", style="cyan")
    table.add_column("Model", style="green")
    table.add_column("Status")
    table.add_column("Input / 1M", justify="right")
    table.add_column("Output / 1M", justify="right")
    table.add_column("Verified", style="dim")

    for item in catalog.to_list():
        if selected_provider and str(item["provider"]).lower() != selected_provider:
            continue
        pricing = registry.get(str(item["provider"]), str(item["model"]))
        table.add_row(
            str(item["provider"]),
            str(item["model"]),
            str(item["status"]),
            f"${pricing.input_per_1m:,.4f}" if pricing else "Needs rate",
            f"${pricing.output_per_1m:,.4f}" if pricing else "Needs rate",
            str(item["verified_at"]),
        )
    console.print(table)


@app.command("model-add")
def model_add_command(
    provider: str = typer.Option(..., "--provider", help="Provider identifier, for example openai."),
    model: str = typer.Option(..., "--model", help="Exact provider model identifier."),
    display_name: Optional[str] = typer.Option(None, "--name", help="Human-friendly model name."),
    status: str = typer.Option("custom", "--status", help="Lifecycle label, for example active or preview."),
    capabilities: str = typer.Option("text", "--capabilities", help="Comma-separated capabilities."),
    context_window: Optional[int] = typer.Option(None, "--context-window", min=1, help="Maximum context tokens."),
    source_url: str = typer.Option("", "--source-url", help="Optional documentation URL."),
    input_per_1m: Optional[float] = typer.Option(None, "--input-per-1m", min=0, help="Contract input price in USD."),
    output_per_1m: Optional[float] = typer.Option(None, "--output-per-1m", min=0, help="Contract output price in USD."),
) -> None:
    """Add a custom model and optionally save its contract token price."""
    from poliora.cost import CatalogModel, ModelCatalog, ModelPricing, PricingRegistry, load_workspace

    if (input_per_1m is None) != (output_per_1m is None):
        raise typer.BadParameter("Set both --input-per-1m and --output-per-1m, or neither.")

    workspace = load_workspace(".")
    catalog = ModelCatalog.load(workspace.catalog_path)
    catalog.add(
        CatalogModel(
            provider=provider,
            model=model,
            display_name=display_name or model,
            status=status,
            capabilities=tuple(item.strip() for item in capabilities.split(",") if item.strip()),
            context_window=context_window,
            source_url=source_url,
            verified_at="custom",
            note="Added by workspace user.",
        )
    )
    catalog.save(workspace.catalog_path)

    if input_per_1m is not None and output_per_1m is not None:
        registry = PricingRegistry.load(workspace.pricing_path)
        registry.add(
            ModelPricing(
                provider=provider,
                model=model,
                input_per_1m=input_per_1m,
                output_per_1m=output_per_1m,
                note="workspace contract rate",
            )
        )
        registry.save(workspace.pricing_path)
        console.print("[green]Custom model and contract pricing saved.[/green]")
    else:
        console.print("[green]Custom model saved.[/green] Add pricing later before using it as a simulation target.")


@app.command("sync-models")
def sync_models_command(
    provider: str = typer.Option(..., "--provider", help="Provider: openai, anthropic, google, mistral, or xai."),
    api_key: str = typer.Option(
        ..., "--api-key", envvar="POLIORA_MODEL_SYNC_KEY", help="Provider API key. It is never saved."
    ),
) -> None:
    """Discover models available to your provider account and update the local catalog."""
    from poliora.cost import ModelCatalog, load_workspace, sync_provider_models

    workspace = load_workspace(".")
    catalog = ModelCatalog.load(workspace.catalog_path)
    result = sync_provider_models(provider, api_key, catalog)
    catalog.save(workspace.catalog_path)
    console.print(f"[green]Synced {result.provider} catalog:[/green] {result.discovered:,} models found")
    console.print(f"[cyan]Added:[/cyan] {result.added:,}  [cyan]Updated:[/cyan] {result.updated:,}")
    console.print(f"[dim]Saved to {workspace.catalog_path}. API keys are not stored.[/dim]")


@app.command("import-csv")
def import_csv_command(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, help="Usage CSV to import."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Default provider when the CSV has none."),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Default project when the CSV has none."),
    preview: bool = typer.Option(False, "--preview", help="Validate and summarize without importing."),
    skip_invalid: bool = typer.Option(False, "--skip-invalid", help="Import valid rows and report rejected rows."),
) -> None:
    """Preview or import existing AI usage data from a CSV file."""
    from poliora.cost import (
        JsonlUsageStore,
        PricingRegistry,
        import_usage_csv,
        load_workspace,
        preview_usage_csv,
    )

    workspace = load_workspace(".")
    registry = PricingRegistry.load(workspace.pricing_path)
    default_project = project or workspace.project
    diagnostic = preview_usage_csv(
        source,
        registry=registry,
        default_provider=provider,
        default_project=default_project,
    )
    console.print(
        f"[cyan]CSV preflight:[/cyan] {diagnostic.valid_rows:,} valid / "
        f"{diagnostic.invalid_rows:,} invalid / {diagnostic.total_rows:,} total rows"
    )
    console.print(f"[cyan]Recognized columns:[/cyan] {', '.join(diagnostic.mapped_columns) or 'None'}")
    if diagnostic.unpriced_models:
        console.print(f"[yellow]Models needing rates:[/yellow] {', '.join(diagnostic.unpriced_models)}")
    for issue in diagnostic.issues[:10]:
        console.print(f"[red]Line {issue.line_number}:[/red] {issue.message}")
    if preview:
        console.print("[green]Preview complete. No usage was written.[/green]")
        return
    if diagnostic.invalid_rows and not skip_invalid:
        console.print("[red]Import stopped before writing. Fix the rows or pass --skip-invalid.[/red]")
        raise typer.Exit(code=2)

    result = import_usage_csv(
        source,
        JsonlUsageStore(workspace.usage_path),
        registry=registry,
        default_provider=provider,
        default_project=default_project,
        skip_invalid=skip_invalid,
    )
    console.print(f"[green]Imported usage rows:[/green] {result.imported_events:,}")
    if result.skipped_rows:
        console.print(f"[yellow]Skipped invalid rows:[/yellow] {result.skipped_rows:,}")
    console.print(f"[cyan]Tracked cost:[/cyan] ${result.estimated_cost_usd:,.4f}")
    console.print(f"[cyan]Usage log:[/cyan] {workspace.usage_path}")


@app.command("record")
def record_command(
    provider: str = typer.Option(..., "--provider", help="AI provider, e.g. openai, anthropic, google."),
    model: str = typer.Option(..., "--model", help="Model name."),
    input_tokens: int = typer.Option(..., "--input-tokens", min=0, help="Prompt/input tokens."),
    output_tokens: int = typer.Option(..., "--output-tokens", min=0, help="Completion/output tokens."),
    cached_input_tokens: int = typer.Option(
        0, "--cached-input-tokens", min=0, help="Cached tokens included in input tokens."
    ),
    reasoning_tokens: int = typer.Option(
        0, "--reasoning-tokens", min=0, help="Reasoning tokens included in output tokens."
    ),
    tool_cost_usd: float = typer.Option(0.0, "--tool-cost-usd", min=0, help="Provider tool charge in USD."),
    operation: str = typer.Option("chat", "--operation", "-o", help="Workflow or operation label."),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project name. Defaults to workspace project."),
    user: Optional[str] = typer.Option(None, "--user", help="User, customer, or client label."),
    latency_ms: Optional[float] = typer.Option(None, "--latency-ms", min=0, help="Request latency in milliseconds."),
    cost_usd: Optional[float] = typer.Option(None, "--cost-usd", min=0, help="Override estimated cost in USD."),
    trace_id: Optional[str] = typer.Option(None, "--trace-id", help="Application trace identifier."),
    provider_request_id: Optional[str] = typer.Option(
        None, "--provider-request-id", help="Provider request or response identifier."
    ),
    metadata: Optional[str] = typer.Option(None, "--metadata", help="Optional JSON metadata."),
) -> None:
    """Record one AI usage event into the local spend log."""
    from poliora.cost import JsonlUsageStore, PricingRegistry, UsageEvent, load_workspace

    workspace = load_workspace(".")
    registry = PricingRegistry.load(workspace.pricing_path)
    estimated_cost = registry.estimate(
        provider,
        model,
        input_tokens,
        output_tokens,
        cached_input_tokens=cached_input_tokens,
    ) + tool_cost_usd
    final_cost = estimated_cost if cost_usd is None else cost_usd

    parsed_metadata = json.loads(metadata) if metadata else {}
    event = UsageEvent(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=final_cost,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        tool_cost_usd=tool_cost_usd,
        operation=operation,
        project=project or workspace.project,
        user=user,
        latency_ms=latency_ms,
        trace_id=trace_id,
        provider_request_id=provider_request_id,
        metadata=parsed_metadata,
    )

    JsonlUsageStore(workspace.usage_path).append(event)
    console.print(f"[green]Recorded usage:[/green] {event.provider}/{event.model}")
    console.print(f"[cyan]Tokens:[/cyan] {event.total_tokens:,}  [cyan]Cost:[/cyan] ${event.cost_usd:.6f}")
    if estimated_cost == 0.0 and cost_usd is None:
        console.print("[yellow]No pricing was found for this model; cost was recorded as $0.00.[/yellow]")


@app.command("report")
def report_command(
    since_days: Optional[int] = typer.Option(None, "--since-days", min=1, help="Only include recent usage."),
    monthly_budget: Optional[float] = typer.Option(None, "--monthly-budget", min=0, help="Override monthly budget."),
    export_json: Optional[Path] = typer.Option(None, "--json", help="Write report JSON."),
    export_csv: Optional[Path] = typer.Option(None, "--csv", help="Write model breakdown CSV."),
    export_html: Optional[Path] = typer.Option(None, "--html", help="Write a standalone executive HTML report."),
    client: str = typer.Option("", "--client", help="Client name shown on the HTML report."),
    prepared_by: str = typer.Option("", "--prepared-by", help="Consultant or team shown on the HTML report."),
    organization: str = typer.Option("Poliora", "--organization", help="Brand shown above the report title."),
    report_title: str = typer.Option("", "--report-title", help="Optional custom HTML report title."),
    accent_color: str = typer.Option("#087c59", "--accent-color", help="Six-digit report accent color."),
) -> None:
    """Summarize AI spend and projected monthly cost."""
    from poliora.cost import JsonlUsageStore, build_usage_report, generate_recommendations, load_workspace

    workspace = load_workspace(".")
    since = None
    if since_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)

    events = JsonlUsageStore(workspace.usage_path).read_since(since)
    budget = monthly_budget if monthly_budget is not None else workspace.monthly_budget_usd
    report = build_usage_report(events, monthly_budget_usd=budget)

    _print_report(report)
    _print_recommendations(generate_recommendations(report))

    if export_json:
        report.write_json(export_json)
        console.print(f"[green]JSON report written:[/green] {export_json}")
    if export_csv:
        report.write_csv(export_csv)
        console.print(f"[green]CSV report written:[/green] {export_csv}")
    if export_html:
        from poliora.cost import DecisionStore, ReportBranding, write_html_report

        branding = ReportBranding(
            organization=organization.strip(),
            client=client.strip(),
            prepared_by=prepared_by.strip(),
            title=report_title.strip(),
            accent_color=accent_color.strip(),
        )
        write_html_report(
            export_html,
            report,
            generate_recommendations(report),
            project=workspace.project,
            branding=branding,
            decisions=DecisionStore(workspace.decisions_path).read_all(),
        )
        console.print(f"[green]HTML report written:[/green] {export_html}")


@app.command("simulate")
def simulate_command(
    source_provider: str = typer.Option(..., "--source-provider", help="Current model provider."),
    source_model: str = typer.Option(..., "--source-model", help="Current model name."),
    target_provider: str = typer.Option(..., "--target-provider", help="Proposed model provider."),
    target_model: str = typer.Option(..., "--target-model", help="Proposed model name."),
    percentage: float = typer.Option(100.0, "--percentage", min=0.01, max=100, help="Traffic percentage to move."),
    since_days: Optional[int] = typer.Option(None, "--since-days", min=1, help="Only include recent usage."),
    export_json: Optional[Path] = typer.Option(None, "--json", help="Write the simulation as JSON."),
) -> None:
    """Estimate savings from routing a workload to a different model."""
    from poliora.cost import JsonlUsageStore, PricingRegistry, load_workspace, simulate_model_switch

    workspace = load_workspace(".")
    since = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None
    events = JsonlUsageStore(workspace.usage_path).read_since(since)
    simulation = simulate_model_switch(
        events,
        source_provider=source_provider,
        source_model=source_model,
        target_provider=target_provider,
        target_model=target_model,
        percentage=percentage,
        registry=PricingRegistry.load(workspace.pricing_path),
    )

    if simulation.matched_requests == 0:
        console.print("[yellow]No matching usage was found for the source model.[/yellow]")
        return

    table = Table(title="Model Routing Simulation", show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Move", f"{simulation.percentage:.1f}% of {source_provider}/{source_model}")
    table.add_row("To", f"{target_provider}/{target_model}")
    table.add_row("Matched requests", f"{simulation.matched_requests:,}")
    table.add_row("Current affected cost", f"${simulation.affected_current_cost_usd:,.4f}")
    table.add_row("Proposed estimated cost", f"${simulation.estimated_target_cost_usd:,.4f}")
    savings_label = f"${simulation.estimated_savings_usd:,.4f} ({simulation.estimated_savings_pct:.1f}%)"
    table.add_row("Estimated savings", savings_label)
    table.add_row("Projected monthly savings", f"${simulation.estimated_monthly_savings_usd:,.2f}")
    console.print(table)

    if export_json:
        export_json.parent.mkdir(parents=True, exist_ok=True)
        export_json.write_text(json.dumps(simulation.to_dict(), indent=2), encoding="utf-8")
        console.print(f"[green]Simulation written:[/green] {export_json}")


@app.command("dashboard")
def dashboard_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface. Keep the default for local use."),
    port: int = typer.Option(8787, "--port", min=1, max=65535, help="Dashboard port."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the dashboard in your browser."),
    project: Optional[str] = typer.Option(None, "--project", help="Project name used only when creating a workspace."),
    monthly_budget: float = typer.Option(
        1000.0,
        "--monthly-budget",
        min=0,
        help="Budget used only when creating a workspace.",
    ),
) -> None:
    """Create a workspace when needed, then start the local dashboard."""
    from poliora.cost import init_workspace, load_workspace
    from poliora.web import run_dashboard

    workspace = load_workspace(".")
    if not workspace.config_path.exists():
        default_project = Path.cwd().name.strip() or "default"
        workspace = init_workspace(
            ".",
            project=project.strip() if project and project.strip() else default_project,
            monthly_budget_usd=monthly_budget,
        )
        console.print(f"[green]Created local workspace:[/green] {workspace.workspace_dir}")
    url = f"http://{host}:{port}"
    console.print(f"[green]Poliora dashboard:[/green] {url}")
    console.print(f"[cyan]Workspace:[/cyan] {workspace.root}")
    console.print("[dim]Press Ctrl+C to stop the local dashboard.[/dim]")
    if open_browser:
        import webbrowser
        from threading import Timer

        Timer(0.35, webbrowser.open_new_tab, args=(url,)).start()
    run_dashboard(workspace.root, host=host, port=port)


@app.command("codex")
def codex_command(
    prompt: str = typer.Argument(..., help="Task to send to Codex. Poliora does not store this text."),
    model: str = typer.Option(..., "--model", "-m", help="Exact model ID used for cost and usage attribution."),
    sandbox: str = typer.Option(
        "read-only",
        "--sandbox",
        help="Codex sandbox: read-only, workspace-write, or danger-full-access.",
    ),
    provider: str = typer.Option("openai", "--provider", help="Pricing provider for API-billed runs."),
    api_billed: bool = typer.Option(
        False,
        "--api-billed/--subscription",
        help="Estimate API cost. Leave off for ChatGPT subscription usage.",
    ),
    operation: str = typer.Option("codex-agent", "--operation", help="Workflow label shown in Poliora."),
) -> None:
    """Run Codex through its JSON stream and record token metadata locally."""
    import subprocess

    from poliora.cost import find_codex_cli, record_codex_exec_event

    if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
        raise typer.BadParameter("--sandbox must be read-only, workspace-write, or danger-full-access.")
    cli = find_codex_cli()
    if cli is None:
        conflicting_launcher = Path(sys.prefix) / "Scripts" / "codex.exe"
        console.print("[red]The official OpenAI Codex CLI was not found.[/red]")
        if conflicting_launcher.exists():
            console.print(f"[yellow]A different Python package is shadowing Codex:[/yellow] {conflicting_launcher}")
            console.print("[dim]Remove it with: python -m pip uninstall codex[/dim]")
        console.print("[dim]Install the official CLI with: npm.cmd install -g @openai/codex[/dim]")
        console.print("[dim]Then run: codex --version[/dim]")
        raise typer.Exit(code=2)

    command = cli.command("exec", "--json", "--model", model, "--sandbox", sandbox, prompt)
    console.print(f"[cyan]Starting Codex:[/cyan] {model} / {sandbox} / {cli.version}")
    process = subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.stdout is None:
        process.kill()
        raise RuntimeError("Could not read the Codex JSON stream.")

    thread_id: str | None = None
    recorded: list[object] = []
    for line in process.stdout:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            console.print(Text(line.rstrip()))
            continue
        event_type = payload.get("type")
        if event_type == "thread.started":
            thread_id = str(payload.get("thread_id") or "") or None
            console.print(f"[dim]Codex thread: {thread_id or 'started'}[/dim]")
        elif event_type == "item.completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                console.print(Text(str(item.get("text") or "")))
        elif event_type == "turn.completed":
            usage_event = record_codex_exec_event(
                payload,
                model=model,
                provider=provider,
                operation=operation,
                thread_id=thread_id,
                api_billed=api_billed,
            )
            if usage_event is not None:
                recorded.append(usage_event)
        elif event_type in {"turn.failed", "error"}:
            console.print(f"[red]Codex reported {event_type}.[/red]")

    return_code = process.wait()
    if return_code != 0:
        console.print(f"[red]Codex exited with status {return_code}.[/red]")
        raise typer.Exit(code=return_code)
    if not recorded:
        console.print("[yellow]Codex finished, but no turn usage event was reported.[/yellow]")
        return

    usage_event = recorded[-1]
    console.print(
        f"[green]Usage recorded:[/green] {usage_event.input_tokens:,} input / "
        f"{usage_event.output_tokens:,} output / {usage_event.cached_input_tokens:,} cached tokens"
    )
    if not api_billed:
        console.print("[dim]ChatGPT subscription usage was recorded without inventing a per-token dollar cost.[/dim]")


@app.command("antigravity-install")
def antigravity_install_command(
    scope: str = typer.Option(
        "workspace",
        "--scope",
        help="Install scope: workspace, editor-global, or cli-global.",
    ),
    force: bool = typer.Option(False, "--force", help="Replace a conflicting Poliora plugin manifest."),
) -> None:
    """Install the Poliora plugin into a documented Antigravity location."""
    from poliora.cost import install_antigravity_plugin

    try:
        result = install_antigravity_plugin(".", scope=scope, force=force)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--scope") from error
    console.print(f"[green]Antigravity plugin installed:[/green] {result.path}")
    console.print("[dim]Restart Antigravity or reload its customizations, then run an agent task.[/dim]")
    console.print("[dim]Activity is recorded locally; exact Gemini API token usage requires the SDK wrapper.[/dim]")


@app.command("antigravity-hook", hidden=True)
def antigravity_hook_command(
    event_name: str = typer.Option(..., "--event", help="Antigravity lifecycle event name."),
    root: Optional[Path] = typer.Option(None, "--root", help="Approved Poliora workspace for the hook."),
) -> None:
    """Receive an official Antigravity hook payload on standard input."""
    from poliora.cost import record_antigravity_hook_event

    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Hook input must be a JSON object.")
        record_antigravity_hook_event(payload, event_name=event_name, root=root)
    except Exception as error:  # Hooks must not break the user's Antigravity task.
        print(f"Poliora hook skipped: {error}", file=sys.stderr)
    print("{}")


@app.command("check")
def check_command(
    max_monthly: Optional[float] = typer.Option(
        None,
        "--max-monthly",
        min=0.01,
        help="Maximum projected monthly AI spend. Defaults to workspace budget.",
    ),
    warn_at: float = typer.Option(80.0, "--warn-at", min=1, max=1000, help="Warn when this percent of budget is used."),
    since_days: Optional[int] = typer.Option(None, "--since-days", min=1, help="Only include recent usage."),
    soft: bool = typer.Option(False, "--soft", help="Print result but do not exit with failure."),
    export_json: Optional[Path] = typer.Option(None, "--json", help="Write budget check JSON."),
) -> None:
    """Check projected spend against a budget; useful for CI/CD gates."""
    from poliora.cost import JsonlUsageStore, build_usage_report, check_budget, load_workspace

    workspace = load_workspace(".")
    since = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None
    events = JsonlUsageStore(workspace.usage_path).read_since(since)
    report = build_usage_report(events, monthly_budget_usd=workspace.monthly_budget_usd)
    limit = max_monthly if max_monthly is not None else workspace.monthly_budget_usd
    result = check_budget(report, limit_usd=limit, warn_at_pct=warn_at)

    status = "PASS" if result.passed else "FAIL"
    style = "green" if result.passed else "red"
    console.print(f"[bold {style}]{status}[/bold {style}] {result.message}")
    console.print(f"[cyan]Projected monthly:[/cyan] ${result.projected_monthly_usd:,.2f}")
    console.print(f"[cyan]Budget limit:[/cyan]      ${result.limit_usd:,.2f}")
    console.print(f"[cyan]Budget used:[/cyan]       {result.used_pct:,.1f}%")

    if export_json:
        export_json.parent.mkdir(parents=True, exist_ok=True)
        export_json.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        console.print(f"[green]Budget check written:[/green] {export_json}")

    if not result.passed and not soft:
        raise typer.Exit(code=1)


@app.command("recommend")
def recommend_command(
    monthly_spend: Optional[float] = typer.Option(None, "--monthly-spend", min=0, help="Current monthly AI spend."),
    target_savings: float = typer.Option(40.0, "--target-savings", min=1, max=90, help="Target savings percent."),
    since_days: Optional[int] = typer.Option(
        None,
        "--since-days",
        min=1,
        help="Use local usage data from recent days.",
    ),
) -> None:
    """Suggest practical ways to reduce AI spend."""
    from poliora.cost import JsonlUsageStore, build_usage_report, generate_recommendations, load_workspace

    workspace = load_workspace(".")
    if monthly_spend is not None:
        target = monthly_spend * (1 - target_savings / 100)
        console.print(f"[cyan]Current spend:[/cyan] ${monthly_spend:,.2f}/month")
        console.print(f"[cyan]Target spend:[/cyan]  ${target:,.2f}/month")
        console.print(f"[cyan]Savings goal:[/cyan]  ${monthly_spend - target:,.2f}/month\n")

    since = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None
    events = JsonlUsageStore(workspace.usage_path).read_since(since)
    report = build_usage_report(events, monthly_budget_usd=workspace.monthly_budget_usd)
    _print_recommendations(generate_recommendations(report, target_savings_pct=target_savings))


@app.command("detect")
def detect_command(
    since_days: Optional[int] = typer.Option(
        30, "--since-days", min=1, help="How far back to read the tools' own logs."
    ),
    import_events: bool = typer.Option(
        False, "--import", help="Add what was found to this workspace's usage log."
    ),
) -> None:
    """Read usage, plan, and model mix from AI tools installed on this computer."""
    from poliora.cost import JsonlUsageStore, init_workspace, load_workspace
    from poliora.cost.local_usage import scan_local_usage

    _banner("Reading what your AI tools already recorded locally")
    console.print(
        "[dim]Poliora reads only token counts, model names, timestamps, and plan type "
        "from each tool's own session logs. Prompts, replies, code, and credentials are "
        "never read.[/dim]\n"
    )

    since = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None
    scans = scan_local_usage(since=since)

    table = Table(title="Detected local AI usage", header_style="bold")
    table.add_column("Tool")
    table.add_column("Plan")
    table.add_column("Requests", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Most used model")
    table.add_column("Equivalent API value", justify="right")

    found = 0
    for scan in scans:
        if not scan.available:
            table.add_row(scan.display_name, "-", "-", "-", "[dim]not found[/dim]", "-")
            continue
        found += 1
        mix = scan.model_mix()
        top = max(mix, key=lambda name: mix[name]) if mix else "-"
        plan = scan.plan.plan_type if scan.plan and scan.plan.plan_type else "not recorded"
        table.add_row(
            scan.display_name,
            plan,
            f"{len(scan.events):,}",
            f"{scan.total_tokens:,}",
            top,
            f"${scan.equivalent_api_cost_usd:,.2f}",
        )
    console.print(table)

    if not found:
        console.print("\n[yellow]No supported tool logs were found on this computer.[/yellow]")
        return

    total_value = sum(scan.equivalent_api_cost_usd for scan in scans if scan.available)
    console.print(
        Panel(
            "These turns were covered by a subscription, so they are recorded at zero spend.\n"
            f"At published API rates the same work would have cost [bold]${total_value:,.2f}[/bold].\n"
            "Compare that against what you pay: far below it means a plan worth downgrading,\n"
            "far above it means the subscription is already earning its keep.",
            title="What this means",
            border_style="green",
        )
    )

    for scan in scans:
        if scan.available and scan.plan and scan.plan.quota_used_pct is not None:
            console.print(
                f"[cyan]{scan.display_name} quota:[/cyan] "
                f"{scan.plan.quota_used_pct:.1f}% used in the current window."
            )

    if import_events:
        workspace = load_workspace(".")
        if not workspace.config_path.exists():
            workspace = init_workspace(".", project="detected")
        store = JsonlUsageStore(workspace.usage_path)
        imported = 0
        for scan in scans:
            for event in scan.events:
                store.append(event)
                imported += 1
        console.print(f"\n[green]Added {imported:,} events to {workspace.usage_path}.[/green]")
    else:
        console.print("\n[dim]Run with --import to add these to your workspace usage log.[/dim]")


@app.command("runway")
def runway_command(
    window: str = typer.Option("five_hour", "--window", help="Which limit window: five_hour or weekly."),
    plan_tokens: Optional[int] = typer.Option(
        None, "--plan-tokens", min=1, help="Published plan ceiling to use until a refusal is observed."
    ),
    statusline: bool = typer.Option(False, "--statusline", help="Emit one compact line for a status bar."),
    as_json: bool = typer.Option(False, "--json", help="Emit the forecast as JSON."),
) -> None:
    """Show how much subscription capacity is left before the next limit."""
    from poliora.cost.capacity import forecast_runway, read_throttle_events
    from poliora.cost.local_usage import read_claude_code_usage, read_codex_usage

    scan = read_claude_code_usage()
    throttles = read_throttle_events()
    forecast = forecast_runway(
        list(scan.events), throttles, window=window, prior_tokens=plan_tokens
    )

    if as_json:
        typer.echo(json.dumps(forecast.to_dict(), indent=2))
        return

    if statusline:
        typer.echo(_statusline(forecast, read_codex_usage()))
        return

    _banner("Subscription capacity")
    if not scan.available:
        console.print("[yellow]No Claude Code session logs were found on this computer.[/yellow]")
        return

    console.print(f"[bold]{forecast.headline()}[/bold]\n")

    table = Table(show_header=False, box=None)
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Window", "5 hours" if forecast.window == "five_hour" else "7 days")
    table.add_row("Used", f"{forecast.used_tokens:,} tokens")
    if forecast.ceiling.is_known:
        table.add_row("Estimated ceiling", f"{forecast.ceiling.tokens:,} tokens")
        table.add_row("Remaining", f"{forecast.remaining_tokens:,} tokens")
    table.add_row("Current burn", f"{forecast.burn_tokens_per_hour:,.0f} tokens/hour")
    # A wall time is only meaningful while capacity remains; once the window is
    # spent the headline already says so, and "wall: now" reads as a glitch.
    if forecast.exhausted_at and (forecast.remaining_tokens or 0) > 0:
        table.add_row("Projected wall", forecast.exhausted_at.astimezone().strftime("%a %H:%M"))
    if forecast.resets_at:
        table.add_row("Window resets", forecast.resets_at.astimezone().strftime("%a %H:%M"))
    console.print(table)

    console.print(f"\n[dim]{forecast.ceiling.describe()}[/dim]")
    if not forecast.ceiling.is_known:
        console.print(
            "[dim]Pass --plan-tokens to estimate against your published plan limit "
            "until Poliora measures one.[/dim]"
        )

    codex = read_codex_usage()
    advice = _arbitrage_advice(forecast, codex)
    if advice:
        console.print(Panel(advice, title="[bold green]Spare capacity elsewhere[/bold green]", border_style="green"))


def _statusline(forecast, codex) -> str:
    """Render one compact line for a status bar."""
    parts = []
    share = forecast.used_pct
    parts.append(f"{share:.0f}% used" if share is not None else f"{forecast.used_tokens/1e6:.1f}M tok")
    if forecast.ceiling.is_known and (forecast.remaining_tokens or 0) <= 0:
        # Past the estimated ceiling: a countdown here would read as "you have
        # time" at exactly the moment the user does not.
        parts.append("window spent")
    elif (remaining := forecast.time_remaining()) is not None and forecast.exhausted_at:
        from poliora.cost.capacity import _humanize

        parts.append(f"~{_humanize(remaining)} left")
    if codex.available and codex.plan and codex.plan.quota_used_pct is not None:
        parts.append(f"Codex {codex.plan.quota_used_pct:.0f}%")
    return "Poliora " + " | ".join(parts)


def _arbitrage_advice(forecast, codex) -> str:
    """Suggest shifting work when one plan is strained and another is idle.

    This is the comparison no single vendor can make: Anthropic cannot see a
    Codex quota, and OpenAI cannot see a Claude window.
    """
    if not codex.available or codex.plan is None or codex.plan.quota_used_pct is None:
        return ""
    codex_used = codex.plan.quota_used_pct
    claude_used = forecast.used_pct
    if claude_used is None or claude_used < 60 or codex_used > 40:
        return ""
    return (
        f"Claude is at {claude_used:.0f}% of this window while Codex sits at {codex_used:.0f}%.\n"
        "Routing mechanical work -- tests, refactors, boilerplate -- to Codex today\n"
        "extends your Claude runway without changing the quality of the hard parts."
    )


@app.command()
def train(
    model: str = typer.Option(
        "microsoft/phi-3-mini-4k-instruct",
        "--model",
        "-m",
        help="Hugging Face model name or local path.",
    ),
    dataset: Path = typer.Option(
        ...,
        "--dataset",
        "-d",
        exists=True,
        help="Path to a CSV dataset.",
    ),
    output: Path = typer.Option(
        Path("tuned_model"),
        "--output",
        "-o",
        help="Output directory for the fine-tuned model.",
    ),
    epochs: int = typer.Option(3, "--epochs", "-e", min=1, help="Training epochs."),
    batch_size: int = typer.Option(4, "--batch-size", "-b", min=1, help="Per-device batch size."),
    lr: float = typer.Option(2e-4, "--lr", help="Learning rate."),
    lora: bool = typer.Option(True, "--lora/--no-lora", help="Enable LoRA adapters."),
    lora_rank: int = typer.Option(8, "--lora-rank", min=1, help="LoRA rank."),
    quantize: bool = typer.Option(True, "--quantize/--no-quantize", help="Quantise the model."),
    quant_bits: int = typer.Option(4, "--quant-bits", help="Quantisation bitwidth (4 or 8)."),
    low_memory: bool = typer.Option(False, "--low-memory/--no-low-memory", help="Aggressive memory saving."),
    track_carbon: bool = typer.Option(True, "--carbon/--no-carbon", help="Track energy and CO2."),
    country: str = typer.Option("USA", "--country", help="ISO country code for carbon intensity."),
    eval_split: float = typer.Option(0.1, "--eval-split", help="Fraction held out for eval (0 = skip)."),
) -> None:
    """Fine-tune a Hugging Face model with eco-friendly optimisations."""
    try:
        from poliora.config import PolioraConfig
        from poliora.trainer.core import EcoTrainer
    except ModuleNotFoundError as error:
        console.print("[red]The optional training toolkit is not installed.[/red]")
        console.print(Text('Install it with: pip install "poliora[training]"', style="dim"))
        raise typer.Exit(code=2) from error

    _banner("sustainable model fine-tuning")

    config = PolioraConfig(
        model_name=model,
        dataset_path=dataset,
        output_dir=output,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=lr,
        use_lora=lora,
        lora_rank=lora_rank,
        use_quantization=quantize,
        quant_bits=quant_bits,  # type: ignore[arg-type]
        low_memory=low_memory,
        track_carbon=track_carbon,
        country_iso_code=country,
        eval_split=eval_split,
    )

    console.print(f"[cyan]Model:[/cyan]       {config.model_name}")
    console.print(f"[cyan]Dataset:[/cyan]     {config.dataset_path}")
    console.print(f"[cyan]Output:[/cyan]      {config.output_dir}")
    console.print(f"[cyan]Epochs:[/cyan]      {config.epochs}")
    console.print(f"[cyan]Batch:[/cyan]       {config.batch_size}")
    console.print(f"[cyan]LoRA:[/cyan]        {'yes r=' + str(config.lora_rank) if config.use_lora else 'no'}")
    quant_status = f"yes {config.quant_bits}-bit" if config.use_quantization else "no"
    console.print(f"[cyan]Quantise:[/cyan]    {quant_status}")
    console.print(f"[cyan]Low memory:[/cyan]  {'yes' if config.low_memory else 'no'}")
    console.print(f"[cyan]Carbon:[/cyan]      {'yes' if config.track_carbon else 'no'}")
    console.print(f"[cyan]Eval split:[/cyan]  {config.eval_split:.0%}")
    console.print()

    trainer = EcoTrainer(config=config)
    trainer.track_carbon("start")

    try:
        trainer.load_model()
        trainer.apply_quant_and_lora()
        trainer.load_dataset()
        trainer.train(epochs=epochs, batch=batch_size)

        eval_metrics = trainer.evaluate()
        if eval_metrics:
            console.print(f"[cyan]Eval metrics:[/cyan] {eval_metrics}")

        trainer.export()
    finally:
        trainer.track_carbon("stop")

    console.print("[bold green]Done. Your eco-tuned model is ready.[/bold green]")


@app.command()
def benchmark(
    model: str = typer.Option(
        "microsoft/phi-3-mini-4k-instruct",
        "--model",
        "-m",
        help="Hugging Face model name or local path.",
    ),
    dataset: Path = typer.Option(
        ...,
        "--dataset",
        "-d",
        exists=True,
        help="Path to a CSV dataset.",
    ),
    epochs: int = typer.Option(1, "--epochs", "-e", min=1, help="Epochs per run."),
    batch_size: int = typer.Option(2, "--batch-size", "-b", min=1, help="Per-device batch size."),
    lora_rank: int = typer.Option(8, "--lora-rank", min=1, help="LoRA rank for eco run."),
    quant_bits: int = typer.Option(4, "--quant-bits", help="Quantisation bits for eco run (4 or 8)."),
    eval_split: float = typer.Option(0.2, "--eval-split", help="Eval fraction."),
    output_dir: Path = typer.Option(
        Path("benchmark_output"),
        "--output",
        "-o",
        help="Directory for benchmark outputs.",
    ),
    export_json: Optional[Path] = typer.Option(None, "--json", help="Export results to JSON file."),
    export_csv: Optional[Path] = typer.Option(None, "--csv", help="Export results to CSV file."),
    grok_key: Optional[str] = typer.Option(
        None,
        "--grok-key",
        help="Grok API key for eco-tips.",
        envvar="POLIORA_GROK_API_KEY",
    ),
    electricity_key: Optional[str] = typer.Option(
        None,
        "--electricity-key",
        help="Electricity Maps API key.",
        envvar="POLIORA_ELECTRICITY_MAPS_KEY",
    ),
) -> None:
    """Benchmark eco-optimised vs baseline training and compare results."""
    try:
        from poliora.utils.benchmark import benchmark_training
    except ModuleNotFoundError as error:
        console.print("[red]The optional training toolkit is not installed.[/red]")
        console.print(Text('Install it with: pip install "poliora[training]"', style="dim"))
        raise typer.Exit(code=2) from error

    _banner("training efficiency benchmark")
    console.print("[bold]Starting benchmark...[/bold]\n")

    result = benchmark_training(
        model_name=model,
        dataset_path=dataset,
        epochs=epochs,
        batch_size=batch_size,
        lora_rank=lora_rank,
        quant_bits=quant_bits,
        eval_split=eval_split,
        output_dir=output_dir,
        export_json=export_json,
        export_csv=export_csv,
        grok_api_key=grok_key,
        electricity_api_key=electricity_key,
    )

    console.print(
        f"\n[bold green]Benchmark complete.[/bold green]  "
        f"Energy saved: [cyan]{result.energy_saved_pct:.1f}%[/cyan]  "
        f"Emissions saved: [cyan]{result.emissions_saved_pct:.1f}%[/cyan]"
    )


@app.command()
def version() -> None:
    """Print the Poliora version."""
    console.print(f"poliora [green]{__version__}[/green]")


def _print_report(report) -> None:
    table = Table(title="AI Spend Report", show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Requests", f"{report.requests:,}")
    table.add_row("Input tokens", f"{report.input_tokens:,}")
    table.add_row("Output tokens", f"{report.output_tokens:,}")
    if report.cached_input_tokens:
        table.add_row("Cached input tokens", f"{report.cached_input_tokens:,}")
    if report.reasoning_tokens:
        table.add_row("Reasoning tokens", f"{report.reasoning_tokens:,}")
    if report.tool_cost_usd:
        table.add_row("Tool charges", f"${report.tool_cost_usd:,.4f}")
    table.add_row("Tracked cost", f"${report.cost_usd:,.4f}")
    if report.non_dollar_requests:
        table.add_row("Non-dollar activity", f"{report.non_dollar_requests:,} excluded")
    table.add_row("Projected monthly", f"${report.projected_monthly_usd:,.2f}")
    table.add_row("Forecast confidence", report.forecast_confidence)
    if report.monthly_budget_usd is not None:
        table.add_row("Monthly budget", f"${report.monthly_budget_usd:,.2f}")
        table.add_row("Budget delta", f"${report.budget_delta_usd:,.2f}")
        table.add_row("Budget used", f"{report.budget_used_pct:,.1f}%")
    console.print(table)

    model_table = Table(title="Top Models", show_header=True)
    model_table.add_column("Model", style="green")
    model_table.add_column("Requests", justify="right")
    model_table.add_column("Tokens", justify="right")
    model_table.add_column("Cost", justify="right")
    model_table.add_column("Share", justify="right")
    for row in report.by_model[:5]:
        model_table.add_row(
            row.name,
            f"{row.requests:,}",
            f"{row.total_tokens:,}",
            f"${row.cost_usd:,.4f}",
            f"{row.share_pct:.1f}%",
        )
    console.print(model_table)


def _print_recommendations(recommendations) -> None:
    table = Table(title="Savings Recommendations", show_header=True)
    table.add_column("Priority", style="cyan")
    table.add_column("Recommendation", style="green")
    table.add_column("Action")
    table.add_column("Savings", justify="right")
    table.add_column("Monthly", justify="right")

    for item in recommendations:
        table.add_row(
            item.priority,
            item.title,
            item.action,
            f"~{item.estimated_savings_pct:.0f}%",
            f"${item.estimated_monthly_savings_usd:,.2f}",
        )
    console.print(table)


if __name__ == "__main__":
    app()
