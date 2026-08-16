"""Poliora configuration — Pydantic settings for every tunable knob."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class PolioraConfig(BaseSettings):
    """Central configuration for a Poliora training run.

    Values can be set via constructor kwargs, environment variables prefixed
    with ``POLIORA_`` (e.g. ``POLIORA_MODEL_NAME``), or a ``.env`` file.
    """

    # ── Model & Data ─────────────────────────────────────────────────────
    model_name: str = Field(
        default="microsoft/phi-3-mini-4k-instruct",
        description="Hugging Face model identifier or local path.",
    )
    dataset_path: Path = Field(
        default=Path("data.csv"),
        description="Path to a CSV dataset with at least a 'text' column.",
    )
    output_dir: Path = Field(
        default=Path("tuned_model"),
        description="Directory where the trained model will be saved.",
    )
    text_column: str = Field(
        default="text",
        description="Name of the text column in the CSV dataset.",
    )
    label_column: Optional[str] = Field(
        default=None,
        description="Optional name of the label column (for evaluation).",
    )

    # ── Training hyper-parameters ────────────────────────────────────────
    epochs: int = Field(default=3, ge=1, description="Number of training epochs.")
    batch_size: int = Field(default=4, ge=1, description="Per-device batch size.")
    learning_rate: float = Field(default=2e-4, gt=0, description="Learning rate.")
    max_seq_length: int = Field(default=512, ge=32, description="Maximum token sequence length.")
    eval_split: float = Field(
        default=0.1, ge=0.0, le=0.5,
        description="Fraction of data to hold out for evaluation (0 = skip eval).",
    )

    # ── Optimisations ────────────────────────────────────────────────────
    use_lora: bool = Field(default=True, description="Apply LoRA adapters for parameter-efficient training.")
    lora_rank: int = Field(default=8, ge=1, description="LoRA rank (r).")
    lora_alpha: int = Field(default=16, ge=1, description="LoRA alpha scaling factor.")
    lora_dropout: float = Field(default=0.05, ge=0.0, le=1.0, description="LoRA dropout probability.")

    use_quantization: bool = Field(default=True, description="Load model in quantised mode (bitsandbytes).")
    quant_bits: Literal[4, 8] = Field(default=4, description="Quantisation bitwidth — 4 or 8.")
    low_memory: bool = Field(default=False, description="Enable aggressive memory-saving tweaks.")

    # ── Carbon tracking ──────────────────────────────────────────────────
    track_carbon: bool = Field(default=True, description="Track energy & CO2 with CodeCarbon.")
    country_iso_code: str = Field(default="USA", description="ISO 3166-1 alpha-3 country code for carbon intensity.")

    # ── API keys (optional) ──────────────────────────────────────────────
    electricity_maps_key: Optional[str] = Field(
        default=None,
        description="Electricity Maps API key for real-time grid carbon intensity.",
    )
    electricity_zone: str = Field(
        default="US",
        description="Electricity Maps zone code (e.g. 'US-CAL-CISO', 'DE', 'FR').",
    )
    grok_api_key: Optional[str] = Field(
        default=None,
        description="Grok / xAI API key for LLM-powered eco-tips.",
    )

    # ── Benchmark ────────────────────────────────────────────────────────
    benchmark_output_dir: Path = Field(
        default=Path("benchmark_output"),
        description="Directory for benchmark reports.",
    )
    benchmark_export_json: Optional[Path] = Field(
        default=None,
        description="Path to write the benchmark JSON report.",
    )
    benchmark_export_csv: Optional[Path] = Field(
        default=None,
        description="Path to write the benchmark CSV report.",
    )

    model_config = {"env_prefix": "POLIORA_", "env_file": ".env", "extra": "ignore"}

    # ── Validators ───────────────────────────────────────────────────────

    @field_validator("dataset_path")
    @classmethod
    def _dataset_exists(cls, v: Path) -> Path:
        """Warn early if the dataset path doesn't exist."""
        if not v.exists():
            raise FileNotFoundError(f"Dataset not found: {v}")
        return v
