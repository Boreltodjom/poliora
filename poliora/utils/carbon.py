"""CarbonTracker — lightweight wrapper around CodeCarbon for eco-stats.

Supports optional Electricity Maps API integration for real-time
grid carbon intensity.  Falls back to CodeCarbon's built-in averages.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


# ── Report data class ────────────────────────────────────────────────────


@dataclass
class CarbonReport:
    """Plain-object snapshot of a single tracking session."""

    duration_s: float = 0.0
    energy_kwh: float = 0.0
    emissions_kg: float = 0.0
    grid_source: str = "codecarbon"  # "codecarbon" | "api" | "fallback"
    grid_intensity_gco2_kwh: Optional[float] = None

    # ── Derived metrics ──────────────────────────────────────────────

    @property
    def energy_wh(self) -> float:
        """Energy in watt-hours."""
        return self.energy_kwh * 1_000

    @property
    def emissions_g(self) -> float:
        """Emissions in grams CO2eq."""
        return self.emissions_kg * 1_000

    @property
    def km_driven_equiv(self) -> float:
        """Equivalent km driven by an average car (≈0.21 kg CO2/km)."""
        return self.emissions_kg / 0.21 if self.emissions_kg else 0.0

    @property
    def lightbulb_hours(self) -> float:
        """Hours a 60 W incandescent bulb could run on the same energy."""
        return self.energy_kwh / 0.06 if self.energy_kwh else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise all metrics to a flat dict."""
        return {
            "duration_s": round(self.duration_s, 2),
            "energy_wh": round(self.energy_wh, 4),
            "emissions_g": round(self.emissions_g, 4),
            "km_driven_equiv": round(self.km_driven_equiv, 4),
            "lightbulb_hours": round(self.lightbulb_hours, 2),
            "grid_source": self.grid_source,
            "grid_intensity_gco2_kwh": self.grid_intensity_gco2_kwh,
        }


# ── Tracker ──────────────────────────────────────────────────────────────


class CarbonTracker:
    """Context-manager wrapper around :class:`codecarbon.EmissionsTracker`.

    Supports optional Electricity Maps API integration for location-aware
    carbon intensity.

    Usage::

        tracker = CarbonTracker(country_iso_code="USA")
        tracker.start()
        # ... training ...
        tracker.stop()
        tracker.print_report()

    Or compare two runs::

        CarbonTracker.compare(eco_report, baseline_report)
    """

    def __init__(
        self,
        *,
        country_iso_code: str = "USA",
        project_name: str = "poliora",
        electricity_api_key: Optional[str] = None,
        electricity_zone: Optional[str] = None,
    ) -> None:
        self.country_iso_code = country_iso_code
        self.project_name = project_name
        self._tracker: Optional[object] = None  # lazily imported
        self._start_time: float = 0.0
        self._report: CarbonReport = CarbonReport()
        self._electricity_api_key = electricity_api_key
        self._electricity_zone = electricity_zone or country_iso_code

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin tracking energy consumption and carbon emissions."""
        try:
            from codecarbon import EmissionsTracker

            self._tracker = EmissionsTracker(
                project_name=self.project_name,
                country_iso_code=self.country_iso_code,
                save_to_file=True,
                output_dir="emissions",
                log_level="warning",
            )
            self._tracker.start()  # type: ignore[union-attr]
            console.print("[green]✓[/green] Carbon tracking started (CodeCarbon)")
        except Exception as exc:
            console.print(f"[yellow]⚠ CodeCarbon unavailable ({exc}); carbon tracking disabled.[/yellow]")
            self._tracker = None
        self._start_time = time.time()

    def stop(self) -> CarbonReport:
        """Stop tracking and return a :class:`CarbonReport`.

        If an Electricity Maps API key was provided, the emissions are
        re-calculated using the real-time grid carbon intensity.

        Returns:
            CarbonReport: Snapshot of energy and emission metrics.
        """
        duration = time.time() - self._start_time
        emissions_kg = 0.0
        energy_kwh = 0.0

        if self._tracker is not None:
            try:
                emissions_kg = float(self._tracker.stop())  # type: ignore[union-attr]
                energy_kwh = float(getattr(self._tracker, "_total_energy", 0.0) or 0.0)
            except Exception:
                emissions_kg = 0.0
                energy_kwh = 0.0

        grid_source = "codecarbon"
        grid_intensity = None

        # ── Electricity Maps override ────────────────────────────────
        if self._electricity_api_key and energy_kwh > 0:
            try:
                from poliora.utils.electricity import adjust_emissions

                adj_kg, grid_info = adjust_emissions(
                    energy_kwh,
                    zone=self._electricity_zone,
                    api_key=self._electricity_api_key,
                )
                emissions_kg = adj_kg
                grid_source = grid_info.source
                grid_intensity = grid_info.carbon_intensity_gco2_kwh
                console.print(
                    f"[green]✓[/green] Grid-adjusted emissions: {adj_kg*1000:.4f} g CO2 "
                    f"(source={grid_source}, {grid_intensity:.0f} gCO2/kWh)"
                )
            except Exception as exc:
                console.print(f"[yellow]⚠ Electricity Maps adjustment failed: {exc}[/yellow]")

        self._report = CarbonReport(
            duration_s=duration,
            energy_kwh=energy_kwh,
            emissions_kg=emissions_kg,
            grid_source=grid_source,
            grid_intensity_gco2_kwh=grid_intensity,
        )
        return self._report

    @property
    def report(self) -> CarbonReport:
        """Access the most recent :class:`CarbonReport`."""
        return self._report

    # ── Reporting ────────────────────────────────────────────────────────

    def print_report(self) -> None:
        """Render a Rich table with eco-impact stats for the last run."""
        _render_report(self._report, title="Poliora — Carbon Report")

    @staticmethod
    def compare(eco: CarbonReport, baseline: CarbonReport) -> None:
        """Print a side-by-side Rich table comparing an *eco* run to a *baseline*.

        Args:
            eco: The optimised (LoRA + quant) run report.
            baseline: The full-precision baseline run report.
        """
        table = Table(
            title="⚖️  Poliora vs Baseline",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Metric", style="cyan", min_width=28)
        table.add_column("Baseline", style="red", justify="right")
        table.add_column("Poliora", style="green", justify="right")
        table.add_column("Saved", style="bold yellow", justify="right")

        def _row(label: str, base_val: float, eco_val: float, unit: str) -> None:
            saved = base_val - eco_val
            pct = (saved / base_val * 100) if base_val else 0.0
            table.add_row(
                label,
                f"{base_val:.4f} {unit}",
                f"{eco_val:.4f} {unit}",
                f"-{pct:.1f}%",
            )

        _row("⏱  Duration", baseline.duration_s, eco.duration_s, "s")
        _row("⚡ Energy", baseline.energy_wh, eco.energy_wh, "Wh")
        _row("🌍 CO2", baseline.emissions_g, eco.emissions_g, "g")

        console.print()
        console.print(Panel(table, border_style="magenta", expand=False))
        console.print()

    # ── Context-manager interface ────────────────────────────────────────

    def __enter__(self) -> "CarbonTracker":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


# ── Private helpers ──────────────────────────────────────────────────────


def _render_report(report: CarbonReport, *, title: str = "Carbon Report") -> None:
    """Render a single :class:`CarbonReport` as a Rich panel."""
    table = Table(title=f"🌿 {title}", show_header=True, header_style="bold green")
    table.add_column("Metric", style="cyan", min_width=28)
    table.add_column("Value", style="white", justify="right")

    table.add_row("⏱  Training duration", f"{report.duration_s:.1f} s")
    table.add_row("⚡ Energy consumed", f"{report.energy_wh:.4f} Wh")
    table.add_row("🌍 CO2 emitted", f"{report.emissions_g:.4f} g")
    table.add_row("🚗 Equivalent driving", f"{report.km_driven_equiv:.4f} km")
    table.add_row("💡 Equivalent light-bulb hours", f"{report.lightbulb_hours:.2f} h")
    table.add_row("🔌 Grid source", report.grid_source)
    if report.grid_intensity_gco2_kwh is not None:
        table.add_row("⚡ Grid intensity", f"{report.grid_intensity_gco2_kwh:.0f} gCO2/kWh")

    console.print()
    console.print(Panel(table, border_style="green", expand=False))
    console.print()
