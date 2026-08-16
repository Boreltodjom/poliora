#!/usr/bin/env python3
"""Run an Poliora benchmark — eco-optimised vs baseline.

This script programmatically invokes ``benchmark_training()`` to compare
LoRA + 4-bit quantised training against full-precision, then exports the
results to JSON and CSV.

Usage:
    python examples/run_benchmark.py

Set environment variables for enhanced tracking:
    POLIORA_ELECTRICITY_MAPS_KEY=...   # real-time grid carbon
    POLIORA_GROK_API_KEY=...           # LLM eco-tips
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from poliora.utils.benchmark import benchmark_training

console = Console()

DATASET = Path(__file__).resolve().parent / "sample_data.csv"


def main() -> None:
    """Run the benchmark and export results."""
    console.print("\n[bold green]🌿 Poliora Benchmark Script[/bold green]\n")

    result = benchmark_training(
        model_name="microsoft/phi-3-mini-4k-instruct",
        dataset_path=DATASET,
        epochs=1,
        batch_size=2,
        lora_rank=8,
        quant_bits=4,
        eval_split=0.2,
        output_dir="benchmark_output",
        export_json="benchmark_output/report.json",
        export_csv="benchmark_output/report.csv",
        # Pass API keys here or set POLIORA_ELECTRICITY_MAPS_KEY / POLIORA_GROK_API_KEY
        electricity_api_key=None,
        grok_api_key=None,
    )

    console.print("\n✅ Benchmark complete!")
    console.print(f"   Energy saved:    {result.energy_saved_pct:.1f}%")
    console.print(f"   Emissions saved: {result.emissions_saved_pct:.1f}%")
    console.print(f"   Time saved:      {result.time_saved_pct:.1f}%")
    console.print("\n   Reports: benchmark_output/report.json, benchmark_output/report.csv")


if __name__ == "__main__":
    main()
