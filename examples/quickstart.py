#!/usr/bin/env python3
"""Poliora quickstart — programmatic usage without the CLI.

Usage:
    python examples/quickstart.py
"""

from pathlib import Path

from poliora.trainer.core import EcoTrainer


def main() -> None:
    # 1. Create trainer with simple positional args
    trainer = EcoTrainer(
        model_name="microsoft/phi-3-mini-4k-instruct",
        dataset_path=Path("examples/sample_data.csv"),
        lora_rank=8,
        quant_bits=4,
    )

    # 2. Start carbon tracking
    trainer.track_carbon("start")

    try:
        # 3. Load → optimise → train → evaluate → export
        trainer.load_model()
        trainer.apply_quant_and_lora()
        trainer.load_dataset()

        metrics = trainer.train(epochs=1, batch=2)
        print("Train metrics:", metrics)

        eval_metrics = trainer.evaluate()
        print("Eval metrics:", eval_metrics)

        trainer.export()
    finally:
        # 4. Stop tracking and print report
        report = trainer.track_carbon("stop")

    print("\n✅ Fine-tuning complete! Model saved to:", trainer.config.output_dir)
    if report:
        print("Carbon report:", report.to_dict())


if __name__ == "__main__":
    main()
