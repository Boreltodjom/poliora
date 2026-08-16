"""Benchmark — compare eco-optimised vs baseline training runs.

The :func:`benchmark_training` function runs two training sessions on the
same model and dataset — one with full Poliora optimisations and one vanilla
baseline — then produces a structured :class:`BenchmarkResult` that can be
exported to JSON, CSV, or pretty-printed with Rich.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from poliora.config import PolioraConfig
from poliora.utils.carbon import CarbonReport, CarbonTracker

logger = logging.getLogger(__name__)
console = Console()


# ── Data models ──────────────────────────────────────────────────────────


@dataclass
class RunResult:
    """Metrics from a single training run."""

    tag: str
    train_loss: float = 0.0
    eval_loss: float = 0.0
    perplexity: float = 0.0
    accuracy: Optional[float] = None
    carbon: CarbonReport = field(default_factory=CarbonReport)
    trainable_params: int = 0
    total_params: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Flat dict for serialisation."""
        d: dict[str, Any] = {
            "tag": self.tag,
            "train_loss": round(self.train_loss, 4),
            "eval_loss": round(self.eval_loss, 4),
            "perplexity": round(self.perplexity, 4),
            "trainable_params": self.trainable_params,
            "total_params": self.total_params,
        }
        if self.accuracy is not None:
            d["accuracy"] = round(self.accuracy, 4)
        d.update({f"carbon_{k}": v for k, v in self.carbon.to_dict().items()})
        return d


@dataclass
class BenchmarkResult:
    """Aggregated benchmark comparing eco vs baseline runs."""

    model_name: str
    dataset_path: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    eco: RunResult = field(default_factory=lambda: RunResult(tag="eco"))
    baseline: RunResult = field(default_factory=lambda: RunResult(tag="baseline"))
    eco_tips: list[str] = field(default_factory=list)

    # ── Derived savings ──────────────────────────────────────────────

    @property
    def energy_saved_pct(self) -> float:
        """Percentage of energy saved by the eco run."""
        if self.baseline.carbon.energy_kwh:
            return (1 - self.eco.carbon.energy_kwh / self.baseline.carbon.energy_kwh) * 100
        return 0.0

    @property
    def emissions_saved_pct(self) -> float:
        """Percentage of emissions saved by the eco run."""
        if self.baseline.carbon.emissions_kg:
            return (1 - self.eco.carbon.emissions_kg / self.baseline.carbon.emissions_kg) * 100
        return 0.0

    @property
    def time_saved_pct(self) -> float:
        """Percentage of time saved by the eco run."""
        if self.baseline.carbon.duration_s:
            return (1 - self.eco.carbon.duration_s / self.baseline.carbon.duration_s) * 100
        return 0.0

    # ── Serialisation ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Full dict representation."""
        return {
            "model_name": self.model_name,
            "dataset_path": self.dataset_path,
            "timestamp": self.timestamp,
            "eco": self.eco.to_dict(),
            "baseline": self.baseline.to_dict(),
            "savings": {
                "energy_pct": round(self.energy_saved_pct, 2),
                "emissions_pct": round(self.emissions_saved_pct, 2),
                "time_pct": round(self.time_saved_pct, 2),
            },
            "eco_tips": self.eco_tips,
        }

    def to_json(self, path: str | Path) -> Path:
        """Write the benchmark to a JSON file.

        Args:
            path: Output file path.

        Returns:
            Resolved path to the written file.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]✓[/green] Benchmark saved to [cyan]{p}[/cyan] (JSON)")
        return p

    def to_csv(self, path: str | Path) -> Path:
        """Write the benchmark to a CSV file (one row per run).

        Args:
            path: Output file path.

        Returns:
            Resolved path to the written file.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        rows = [self.eco.to_dict(), self.baseline.to_dict()]
        fieldnames = list(rows[0].keys())

        with p.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        console.print(f"[green]✓[/green] Benchmark saved to [cyan]{p}[/cyan] (CSV)")
        return p


# ── Pretty-print ─────────────────────────────────────────────────────────


def print_benchmark(result: BenchmarkResult) -> None:
    """Render a Rich comparison table for a :class:`BenchmarkResult`."""
    table = Table(
        title=f"⚖️  Benchmark — {result.model_name}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Metric", style="cyan", min_width=28)
    table.add_column("Baseline", style="red", justify="right")
    table.add_column("Poliora", style="green", justify="right")
    table.add_column("Saved", style="bold yellow", justify="right")

    def _row(label: str, base: float, eco: float, unit: str = "", fmt: str = ".4f") -> None:
        saved_pct = ((base - eco) / base * 100) if base else 0.0
        table.add_row(
            label,
            f"{base:{fmt}} {unit}".strip(),
            f"{eco:{fmt}} {unit}".strip(),
            f"{saved_pct:+.1f}%",
        )

    # Training metrics
    _row("📉 Train Loss", result.baseline.train_loss, result.eco.train_loss)
    _row("📉 Eval Loss", result.baseline.eval_loss, result.eco.eval_loss)
    _row("📊 Perplexity", result.baseline.perplexity, result.eco.perplexity, fmt=".2f")

    if result.eco.accuracy is not None and result.baseline.accuracy is not None:
        _row("🎯 Accuracy", result.baseline.accuracy, result.eco.accuracy, fmt=".2%")

    # Carbon metrics
    _row("⏱  Duration", result.baseline.carbon.duration_s, result.eco.carbon.duration_s, "s", ".1f")
    _row("⚡ Energy", result.baseline.carbon.energy_wh, result.eco.carbon.energy_wh, "Wh")
    _row("🌍 CO2", result.baseline.carbon.emissions_g, result.eco.carbon.emissions_g, "g")

    # Param counts
    table.add_row(
        "🔧 Trainable Params",
        f"{result.baseline.trainable_params:,}",
        f"{result.eco.trainable_params:,}",
        f"{(1 - result.eco.trainable_params / max(result.baseline.trainable_params, 1)) * 100:+.1f}%",
    )

    console.print()
    console.print(Panel(table, border_style="magenta", expand=False))
    console.print()


# ── Core benchmark function ──────────────────────────────────────────────


def benchmark_training(
    model_name: str,
    dataset_path: str | Path,
    *,
    epochs: int = 1,
    batch_size: int = 2,
    lora_rank: int = 8,
    quant_bits: int = 4,
    eval_split: float = 0.2,
    output_dir: str | Path = "benchmark_output",
    export_json: Optional[str | Path] = None,
    export_csv: Optional[str | Path] = None,
    grok_api_key: Optional[str] = None,
    electricity_api_key: Optional[str] = None,
) -> BenchmarkResult:
    """Run eco-optimised and baseline training, compare results.

    This function trains the given model twice:
    1. **Eco run** — LoRA + quantisation + low-memory mode.
    2. **Baseline run** — full-precision, no LoRA, no quantisation.

    It tracks carbon for both, computes eval metrics, and optionally
    generates LLM eco-tips.

    Args:
        model_name: Hugging Face model identifier.
        dataset_path: Path to a CSV dataset.
        epochs: Training epochs per run.
        batch_size: Per-device batch size.
        lora_rank: LoRA rank for the eco run.
        quant_bits: Quantisation bits for the eco run (4 or 8).
        eval_split: Fraction held out for evaluation.
        output_dir: Base directory for model outputs.
        export_json: Optional path to write JSON report.
        export_csv: Optional path to write CSV report.
        grok_api_key: Optional Grok API key for LLM eco-tips.
        electricity_api_key: Optional Electricity Maps API key.

    Returns:
        A :class:`BenchmarkResult` with both runs' metrics.
    """
    from poliora.utils.eco_tips import get_eco_tips, print_eco_tips
    from poliora.utils.electricity import adjust_emissions

    out = Path(output_dir)
    ds = Path(dataset_path)

    # ── 1. Eco run ───────────────────────────────────────────────────
    console.print("\n[bold green]━━━ 🌿 Eco Run (LoRA + Quantised) ━━━[/bold green]\n")

    eco_config = PolioraConfig(
        model_name=model_name,
        dataset_path=ds,
        output_dir=out / "eco",
        epochs=epochs,
        batch_size=batch_size,
        use_lora=True,
        lora_rank=lora_rank,
        use_quantization=True,
        quant_bits=quant_bits,  # type: ignore[arg-type]
        low_memory=True,
        track_carbon=True,
        eval_split=eval_split,
    )

    eco_run = _run_single(eco_config, tag="eco")

    # ── 2. Baseline run ──────────────────────────────────────────────
    console.print("\n[bold red]━━━ 🔥 Baseline Run (Full Precision) ━━━[/bold red]\n")

    baseline_config = PolioraConfig(
        model_name=model_name,
        dataset_path=ds,
        output_dir=out / "baseline",
        epochs=epochs,
        batch_size=batch_size,
        use_lora=False,
        use_quantization=False,
        low_memory=False,
        track_carbon=True,
        eval_split=eval_split,
    )

    baseline_run = _run_single(baseline_config, tag="baseline")

    # ── 3. Adjust emissions with grid data ───────────────────────────
    if electricity_api_key:
        for run in (eco_run, baseline_run):
            adj_kg, grid = adjust_emissions(
                run.carbon.energy_kwh,
                api_key=electricity_api_key,
            )
            run.carbon.emissions_kg = adj_kg
            console.print(
                f"[cyan]Grid-adjusted CO2 ({run.tag}):[/cyan] {adj_kg*1000:.4f} g "
                f"(source={grid.source}, intensity={grid.carbon_intensity_gco2_kwh} gCO2/kWh)"
            )

    # ── 4. Assemble result ───────────────────────────────────────────
    result = BenchmarkResult(
        model_name=model_name,
        dataset_path=str(ds),
        eco=eco_run,
        baseline=baseline_run,
    )

    # ── 5. Eco tips ──────────────────────────────────────────────────
    tips = get_eco_tips(result.to_dict(), api_key=grok_api_key)
    result.eco_tips = tips

    # ── 6. Output ────────────────────────────────────────────────────
    print_benchmark(result)
    print_eco_tips(tips)

    if export_json:
        result.to_json(export_json)
    if export_csv:
        result.to_csv(export_csv)

    return result


# ── Private helpers ──────────────────────────────────────────────────────


def _run_single(config: PolioraConfig, tag: str) -> RunResult:
    """Execute a single training run and capture metrics."""
    from poliora.trainer.core import EcoTrainer

    trainer = EcoTrainer(config=config)

    tracker = CarbonTracker(country_iso_code=config.country_iso_code)
    tracker.start()

    try:
        trainer.load_model()
        trainer.apply_quant_and_lora()
        trainer.load_dataset()

        train_metrics = trainer.train(epochs=config.epochs, batch=config.batch_size)
        eval_metrics = trainer.evaluate()
        trainer.export()
    finally:
        report = tracker.stop()
        tracker.print_report()

    # Param counts
    total_params = sum(p.numel() for p in trainer.model.parameters())
    trainable_params = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)

    return RunResult(
        tag=tag,
        train_loss=train_metrics.get("train_loss", 0.0),
        eval_loss=eval_metrics.get("eval_loss", 0.0),
        perplexity=eval_metrics.get("perplexity", 0.0),
        accuracy=eval_metrics.get("accuracy"),
        carbon=report,
        trainable_params=trainable_params,
        total_params=total_params,
    )
