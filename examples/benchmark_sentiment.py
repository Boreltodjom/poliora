#!/usr/bin/env python3
"""Benchmark: Poliora (LoRA + 4-bit) vs full-precision baseline on a sentiment dataset.

This script demonstrates the energy and accuracy trade-offs of eco-friendly
fine-tuning.  It runs two training sessions — one with all optimisations and
one baseline — then prints a side-by-side carbon comparison.

Usage:
    python examples/benchmark_sentiment.py

Prerequisites:
    - ``poetry install`` (from the project root)
    - A CUDA-capable GPU is recommended but not required.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from poliora.config import PolioraConfig
from poliora.trainer.core import EcoTrainer
from poliora.utils.carbon import CarbonReport, CarbonTracker

console = Console()

DATASET = Path(__file__).resolve().parent / "sample_data.csv"


# ── Helpers ──────────────────────────────────────────────────────────────


def run_training(tag: str, config: PolioraConfig) -> CarbonReport:
    """Run a single training loop and return its carbon report.

    Args:
        tag: A human-readable label for console output.
        config: The training configuration.

    Returns:
        CarbonReport from the training run.
    """
    console.print(Rule(f"[bold]{tag}[/bold]"))

    trainer = EcoTrainer(config=config)

    # Start carbon tracking
    trainer.track_carbon("start")

    try:
        trainer.load_model()
        trainer.apply_quant_and_lora()
        trainer.load_dataset()
        train_metrics = trainer.train(epochs=config.epochs, batch=config.batch_size)

        eval_metrics = trainer.evaluate()
        console.print(f"[cyan]Train metrics:[/cyan] {train_metrics}")
        if eval_metrics:
            console.print(f"[cyan]Eval  metrics:[/cyan] {eval_metrics}")

        trainer.export()
    finally:
        report = trainer.track_carbon("stop")

    return report or CarbonReport()


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    """Run the eco vs baseline benchmark."""
    console.print(
        "\n[bold green]🌿 Poliora Benchmark — Eco vs Baseline[/bold green]\n"
    )

    # ── 1. Eco-optimised run (LoRA r=8, 4-bit quant) ────────────────
    eco_config = PolioraConfig(
        model_name="microsoft/phi-3-mini-4k-instruct",
        dataset_path=DATASET,
        output_dir=Path("benchmark_eco"),
        epochs=1,
        batch_size=2,
        use_lora=True,
        lora_rank=8,
        use_quantization=True,
        quant_bits=4,
        low_memory=True,
        track_carbon=True,
        eval_split=0.2,
    )
    eco_report = run_training("🌿 Eco Run (LoRA r=8, 4-bit)", eco_config)

    # ── 2. Baseline run (no LoRA, no quant, full precision) ──────────
    baseline_config = PolioraConfig(
        model_name="microsoft/phi-3-mini-4k-instruct",
        dataset_path=DATASET,
        output_dir=Path("benchmark_baseline"),
        epochs=1,
        batch_size=2,
        use_lora=False,
        use_quantization=False,
        low_memory=False,
        track_carbon=True,
        eval_split=0.2,
    )
    baseline_report = run_training("🔥 Baseline (Full Precision)", baseline_config)

    # ── 3. Side-by-side comparison ───────────────────────────────────
    console.print(Rule("[bold magenta]Comparison[/bold magenta]"))
    CarbonTracker.compare(eco_report, baseline_report)

    console.print(
        "\n[bold green]✨ Benchmark complete![/bold green]  "
        "See the table above for energy savings.\n"
    )


if __name__ == "__main__":
    main()
