"""EcoTrainer — core training pipeline with LoRA, quantisation, Accelerate, and eco-friendly defaults.

This module is the heart of Poliora. It handles the full lifecycle:

1. ``load_model()``  — download / load a Hugging Face causal-LM.
2. ``apply_quant_and_lora()`` — quantise the model and attach LoRA adapters.
3. ``load_dataset()``  — read a CSV, tokenise, and split train/eval.
4. ``train()``  — run training with HF Trainer + Accelerate mixed precision.
5. ``evaluate()``  — compute perplexity (and optionally accuracy) on the eval set.
6. ``track_carbon()``  — start/stop CodeCarbon and print a carbon report.
7. ``export()``  — merge LoRA adapters and save the final model in HF format.
"""

from __future__ import annotations

import gc
import logging
import math
from pathlib import Path
from typing import Any, Dict, Optional

import torch
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from poliora.config import PolioraConfig
from poliora.utils.carbon import CarbonReport, CarbonTracker

logger = logging.getLogger(__name__)
console = Console()

# ── Sentinels for low-RAM detection ──────────────────────────────────────

_LOW_RAM_THRESHOLD_GB: float = 8.0  # auto-enable fallbacks below this


def _available_ram_gb() -> float:
    """Return available system RAM in GiB, or inf if unknown."""
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        return float("inf")


def _available_vram_gb() -> float:
    """Return free GPU VRAM in GiB, or 0 if no CUDA device."""
    if torch.cuda.is_available():
        free, _ = torch.cuda.mem_get_info()
        return free / (1024 ** 3)
    return 0.0


# ═════════════════════════════════════════════════════════════════════════
#  EcoTrainer
# ═════════════════════════════════════════════════════════════════════════


class EcoTrainer:
    """End-to-end eco-friendly fine-tuning pipeline.

    Args:
        model_name: Hugging Face model ID or local path.
        dataset_path: Path to a CSV file with at least a ``text`` column.
        lora_rank: LoRA rank (``r``) — lower is lighter.  Default **8**.
        quant_bits: Quantisation width, ``4`` or ``8``.  Default **4**.
        config: Optional :class:`PolioraConfig` override for full control.

    Example::

        trainer = EcoTrainer("microsoft/phi-3-mini-4k-instruct", "data.csv")
        trainer.load_model()
        trainer.apply_quant_and_lora()
        trainer.load_dataset()
        report = trainer.track_carbon(action="start")
        metrics = trainer.train(epochs=3, batch=4)
        eval_metrics = trainer.evaluate()
        report = trainer.track_carbon(action="stop")
        trainer.export()
    """

    def __init__(
        self,
        model_name: str = "microsoft/phi-3-mini-4k-instruct",
        dataset_path: str | Path = "data.csv",
        lora_rank: int = 8,
        quant_bits: int = 4,
        *,
        config: Optional[PolioraConfig] = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            self.config = PolioraConfig(
                model_name=model_name,
                dataset_path=Path(dataset_path),
                lora_rank=lora_rank,
                quant_bits=quant_bits,  # type: ignore[arg-type]
            )

        # ── Internal state ───────────────────────────────────────────
        self.model: Any = None
        self.tokenizer: Any = None
        self.train_dataset: Any = None
        self.eval_dataset: Any = None
        self._peft_applied: bool = False
        self._quant_applied: bool = False
        self._carbon_tracker: Optional[CarbonTracker] = None
        self._low_ram: bool = self.config.low_memory

        # Auto-detect low-RAM if not explicitly set
        if not self._low_ram:
            ram = _available_ram_gb()
            if ram < _LOW_RAM_THRESHOLD_GB:
                console.print(
                    f"[yellow]⚠ Low RAM detected ({ram:.1f} GiB). "
                    f"Enabling low-memory fallbacks automatically.[/yellow]"
                )
                self._low_ram = True

    # ─────────────────────────────────────────────────────────────────────
    #  1. load_model
    # ─────────────────────────────────────────────────────────────────────

    def load_model(self) -> "EcoTrainer":
        """Download / load the base Hugging Face causal-LM and its tokenizer.

        This method loads the model in **full precision** (or in CPU-offload
        mode when low-RAM is active).  Call :meth:`apply_quant_and_lora`
        afterwards to compress and attach adapters.

        Returns:
            self — for method chaining.

        Raises:
            RuntimeError: If the model cannot be loaded.
        """
        from transformers import AutoModelForCausalLM, AutoTokenizer

        with _spinner("Loading tokenizer"):
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.config.model_name,
                trust_remote_code=True,
            )
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
                self.tokenizer.pad_token_id = self.tokenizer.eos_token_id

        # ── Device map strategy ──────────────────────────────────────
        device_map: str | dict = "auto"
        model_kwargs: Dict[str, Any] = {
            "trust_remote_code": True,
            "torch_dtype": torch.float16,
        }

        if self._low_ram:
            model_kwargs["low_cpu_mem_usage"] = True
            vram = _available_vram_gb()
            if vram < 4.0:
                device_map = "cpu"
                console.print("[yellow]⚠ Very low VRAM — loading model on CPU.[/yellow]")

        with _spinner("Loading model"):
            try:
                self.model = AutoModelForCausalLM.from_pretrained(
                    self.config.model_name,
                    device_map=device_map,
                    **model_kwargs,
                )
            except Exception as exc:
                # Fallback: try fp32 on CPU if fp16 fails
                if "float16" in str(exc).lower() or "half" in str(exc).lower():
                    console.print("[yellow]⚠ fp16 failed — retrying with fp32 on CPU.[/yellow]")
                    model_kwargs.pop("torch_dtype", None)
                    self.model = AutoModelForCausalLM.from_pretrained(
                        self.config.model_name,
                        device_map="cpu",
                        **model_kwargs,
                    )
                else:
                    raise RuntimeError(f"Failed to load model '{self.config.model_name}': {exc}") from exc

        _log_param_count(self.model, label="Base model loaded")
        return self

    # ─────────────────────────────────────────────────────────────────────
    #  2. apply_quant_and_lora
    # ─────────────────────────────────────────────────────────────────────

    def apply_quant_and_lora(self) -> "EcoTrainer":
        """Quantise the model and wrap it with LoRA adapters.

        Quantisation uses ``bitsandbytes`` NF4 (4-bit) or INT8 (8-bit).
        LoRA is applied via ``peft`` to the attention projection layers.

        Returns:
            self — for method chaining.

        Raises:
            RuntimeError: If the model has not been loaded yet.
        """
        if self.model is None:
            raise RuntimeError("Call load_model() before apply_quant_and_lora().")

        # ── Quantisation ─────────────────────────────────────────────
        if self.config.use_quantization and not self._quant_applied:
            self._apply_quantization()

        # ── LoRA ─────────────────────────────────────────────────────
        if self.config.use_lora and not self._peft_applied:
            self._apply_lora()

        _log_param_count(self.model, label="After quant + LoRA")
        return self

    def _apply_quantization(self) -> None:
        """Reload the model with a bitsandbytes quantisation config."""
        from transformers import AutoModelForCausalLM, BitsAndBytesConfig

        bits = self.config.quant_bits

        if bits == 4:
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )
        else:  # 8-bit
            bnb_config = BitsAndBytesConfig(load_in_8bit=True)

        with _spinner(f"Applying {bits}-bit quantisation"):
            self.model = AutoModelForCausalLM.from_pretrained(
                self.config.model_name,
                quantization_config=bnb_config,
                device_map="auto",
                trust_remote_code=True,
            )

        self._quant_applied = True
        console.print(f"[green]✓[/green] {bits}-bit quantisation applied")

    def _apply_lora(self) -> None:
        """Attach LoRA adapters to attention projection layers via PEFT."""
        from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training

        if self._quant_applied:
            self.model = prepare_model_for_kbit_training(
                self.model,
                use_gradient_checkpointing=self._low_ram,
            )

        lora_cfg = LoraConfig(
            r=self.config.lora_rank,
            lora_alpha=self.config.lora_alpha,
            lora_dropout=self.config.lora_dropout,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            task_type=TaskType.CAUSAL_LM,
            bias="none",
        )

        self.model = get_peft_model(self.model, lora_cfg)
        self._peft_applied = True
        console.print(
            f"[green]✓[/green] LoRA applied — r={self.config.lora_rank}, "
            f"alpha={self.config.lora_alpha}, dropout={self.config.lora_dropout}"
        )

    # ─────────────────────────────────────────────────────────────────────
    #  3. load_dataset
    # ─────────────────────────────────────────────────────────────────────

    def load_dataset(self) -> "EcoTrainer":
        """Load a CSV dataset, tokenise, and optionally split into train / eval.

        Returns:
            self — for method chaining.

        Raises:
            FileNotFoundError: If the dataset CSV does not exist.
            KeyError: If the expected text column is missing.
        """
        from datasets import load_dataset

        ds_path = str(self.config.dataset_path)
        if not Path(ds_path).exists():
            raise FileNotFoundError(f"Dataset not found: {ds_path}")

        with _spinner("Loading dataset"):
            raw = load_dataset("csv", data_files=ds_path, split="train")

        text_col = self.config.text_column
        if text_col not in raw.column_names:
            available = ", ".join(raw.column_names)
            raise KeyError(
                f"Text column '{text_col}' not found. Available: {available}"
            )

        console.print(
            f"[green]✓[/green] Dataset loaded — {len(raw)} rows, "
            f"text column: [cyan]{text_col}[/cyan]"
        )

        # ── Tokenise ─────────────────────────────────────────────────
        max_len = self.config.max_seq_length

        def _tokenize(examples: dict) -> dict:
            tokens = self.tokenizer(
                examples[text_col],
                truncation=True,
                padding="max_length",
                max_length=max_len,
            )
            tokens["labels"] = tokens["input_ids"].copy()
            return tokens

        with _spinner("Tokenising"):
            tokenised = raw.map(
                _tokenize,
                batched=True,
                remove_columns=raw.column_names,
                desc="Tokenising",
            )

        # ── Train / eval split ───────────────────────────────────────
        if self.config.eval_split > 0 and len(tokenised) > 1:
            split = tokenised.train_test_split(test_size=self.config.eval_split, seed=42)
            self.train_dataset = split["train"]
            self.eval_dataset = split["test"]
            console.print(
                f"[green]✓[/green] Split — train={len(self.train_dataset)}, "
                f"eval={len(self.eval_dataset)}"
            )
        else:
            self.train_dataset = tokenised
            self.eval_dataset = None
            console.print("[green]✓[/green] No eval split requested")

        return self

    # ─────────────────────────────────────────────────────────────────────
    #  4. train
    # ─────────────────────────────────────────────────────────────────────

    def train(self, epochs: int = 3, batch: int = 4) -> Dict[str, Any]:
        """Run training using the HF Trainer with Accelerate mixed precision.

        Args:
            epochs: Number of training epochs (overrides config if given).
            batch:  Per-device train batch size (overrides config if given).

        Returns:
            Dict with training metrics (``train_loss``, etc.).

        Raises:
            RuntimeError: If model or dataset has not been loaded.
        """
        from transformers import Trainer, TrainingArguments

        if self.model is None:
            raise RuntimeError("Call load_model() before train().")
        if self.train_dataset is None:
            raise RuntimeError("Call load_dataset() before train().")

        ckpt_dir = Path(self.config.output_dir) / "checkpoints"
        ckpt_dir.mkdir(parents=True, exist_ok=True)

        # ── Low-memory adjustments ───────────────────────────────────
        grad_accum = 8 if self._low_ram else 1
        effective_batch = max(1, batch // 2) if self._low_ram else batch
        grad_ckpt = self._low_ram

        # Fallback batch size if very low VRAM
        if self._low_ram and _available_vram_gb() < 4.0:
            effective_batch = 1
            grad_accum = 16
            console.print("[yellow]⚠ Ultra-low VRAM — batch=1, grad_accum=16.[/yellow]")

        training_args = TrainingArguments(
            output_dir=str(ckpt_dir),
            num_train_epochs=epochs,
            per_device_train_batch_size=effective_batch,
            learning_rate=self.config.learning_rate,
            # ── Accelerate mixed precision ───────────────────────────
            fp16=torch.cuda.is_available(),
            bf16=False,
            # ── Memory optimisations ─────────────────────────────────
            gradient_checkpointing=grad_ckpt,
            gradient_accumulation_steps=grad_accum,
            optim="adamw_torch",
            # ── Logging & saving ─────────────────────────────────────
            logging_steps=10,
            save_strategy="epoch",
            evaluation_strategy="epoch" if self.eval_dataset is not None else "no",
            report_to="none",
            seed=42,
            # ── Safety ───────────────────────────────────────────────
            dataloader_pin_memory=not self._low_ram,
            dataloader_num_workers=0 if self._low_ram else 2,
        )

        hf_trainer = Trainer(
            model=self.model,
            args=training_args,
            train_dataset=self.train_dataset,
            eval_dataset=self.eval_dataset,
            tokenizer=self.tokenizer,
        )

        console.print(
            f"\n[bold green]🚀 Training started[/bold green] — "
            f"epochs={epochs}, batch={effective_batch}, grad_accum={grad_accum}\n"
        )

        try:
            result = hf_trainer.train()
        except torch.cuda.OutOfMemoryError:
            console.print("[red]✗ CUDA OOM — retrying with batch=1 + grad_accum=16[/red]")
            _free_memory()
            training_args.per_device_train_batch_size = 1
            training_args.gradient_accumulation_steps = 16
            training_args.gradient_checkpointing = True
            hf_trainer = Trainer(
                model=self.model,
                args=training_args,
                train_dataset=self.train_dataset,
                eval_dataset=self.eval_dataset,
                tokenizer=self.tokenizer,
            )
            result = hf_trainer.train()

        metrics = result.metrics
        loss = metrics.get("train_loss", float("nan"))
        console.print(f"\n[green]✓[/green] Training complete — loss={loss:.4f}")
        return metrics

    # ─────────────────────────────────────────────────────────────────────
    #  5. evaluate
    # ─────────────────────────────────────────────────────────────────────

    def evaluate(self) -> Dict[str, float]:
        """Compute perplexity (and optionally accuracy) on the eval set.

        Returns:
            Dict with ``perplexity`` and optionally ``accuracy``.

        Raises:
            RuntimeError: If no eval dataset is available.
        """
        if self.eval_dataset is None:
            console.print("[yellow]⚠ No eval set — skipping evaluation.[/yellow]")
            return {}

        if self.model is None:
            raise RuntimeError("Model not loaded.")

        from transformers import Trainer, TrainingArguments

        eval_args = TrainingArguments(
            output_dir=str(Path(self.config.output_dir) / "eval_tmp"),
            per_device_eval_batch_size=max(1, self.config.batch_size),
            fp16=torch.cuda.is_available(),
            report_to="none",
            dataloader_pin_memory=not self._low_ram,
            dataloader_num_workers=0 if self._low_ram else 2,
        )

        hf_trainer = Trainer(
            model=self.model,
            args=eval_args,
            eval_dataset=self.eval_dataset,
            tokenizer=self.tokenizer,
        )

        with _spinner("Evaluating"):
            raw_metrics = hf_trainer.evaluate()

        # ── Perplexity ───────────────────────────────────────────────
        eval_loss = raw_metrics.get("eval_loss", float("nan"))
        perplexity = math.exp(eval_loss) if math.isfinite(eval_loss) else float("inf")

        results: Dict[str, float] = {
            "eval_loss": round(eval_loss, 4),
            "perplexity": round(perplexity, 4),
        }

        console.print(
            f"[green]✓[/green] Evaluation — "
            f"loss={eval_loss:.4f}, perplexity={perplexity:.2f}"
        )

        # ── Simple accuracy (if labels present) ──────────────────────
        if self.config.label_column and self.config.label_column in (self.eval_dataset.column_names or []):
            try:
                accuracy = self._compute_accuracy()
                results["accuracy"] = round(accuracy, 4)
                console.print(f"[green]✓[/green] Accuracy: {accuracy:.2%}")
            except Exception as exc:
                logger.warning("Accuracy computation failed: %s", exc)

        return results

    def _compute_accuracy(self) -> float:
        """Token-level accuracy between predictions and labels on eval set."""
        self.model.eval()
        correct = 0
        total = 0

        with torch.no_grad():
            for i in range(len(self.eval_dataset)):
                sample = self.eval_dataset[i]
                input_ids = torch.tensor([sample["input_ids"]], device=self.model.device)
                labels = torch.tensor([sample["labels"]], device=self.model.device)

                outputs = self.model(input_ids)
                preds = outputs.logits.argmax(dim=-1)

                mask = labels != -100
                correct += (preds[:, :-1][mask[:, 1:]] == labels[:, 1:][mask[:, 1:]]).sum().item()
                total += mask[:, 1:].sum().item()

                if i >= 50:  # cap for speed
                    break

        return correct / total if total > 0 else 0.0

    # ─────────────────────────────────────────────────────────────────────
    #  6. track_carbon
    # ─────────────────────────────────────────────────────────────────────

    def track_carbon(self, action: str = "start") -> Optional[CarbonReport]:
        """Start or stop the built-in CodeCarbon tracker.

        Args:
            action: ``"start"`` to begin tracking, ``"stop"`` to end and print
                    the carbon report.

        Returns:
            A :class:`CarbonReport` when ``action="stop"``, else ``None``.

        Raises:
            ValueError: If *action* is not ``"start"`` or ``"stop"``.
        """
        action = action.strip().lower()

        if action == "start":
            if not self.config.track_carbon:
                console.print("[dim]Carbon tracking disabled in config.[/dim]")
                return None
            self._carbon_tracker = CarbonTracker(
                country_iso_code=self.config.country_iso_code,
                project_name="poliora",
            )
            self._carbon_tracker.start()
            return None

        elif action == "stop":
            if self._carbon_tracker is None:
                console.print("[yellow]⚠ No active carbon tracker to stop.[/yellow]")
                return CarbonReport()
            report = self._carbon_tracker.stop()
            self._carbon_tracker.print_report()
            return report

        else:
            raise ValueError(f"action must be 'start' or 'stop', got '{action}'")

    # ─────────────────────────────────────────────────────────────────────
    #  7. export
    # ─────────────────────────────────────────────────────────────────────

    def export(self, output_dir: Optional[str | Path] = None) -> Path:
        """Merge LoRA adapters (if any) and save the model in Hugging Face format.

        Args:
            output_dir: Override the config output directory.  Defaults to
                        ``self.config.output_dir``.

        Returns:
            Path to the saved model directory.

        Raises:
            RuntimeError: If the model has not been loaded.
        """
        if self.model is None:
            raise RuntimeError("No model to export — call load_model() first.")

        out = Path(output_dir) if output_dir else Path(self.config.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        with _spinner("Exporting model"):
            if self._peft_applied:
                try:
                    merged = self.model.merge_and_unload()
                    merged.save_pretrained(out, safe_serialization=True)
                    console.print("[green]✓[/green] LoRA adapters merged")
                except Exception as exc:
                    # Fallback: save adapter-only if merge fails (e.g. quantised)
                    console.print(
                        f"[yellow]⚠ Merge failed ({exc}); saving adapter weights only.[/yellow]"
                    )
                    self.model.save_pretrained(out)
            else:
                self.model.save_pretrained(out, safe_serialization=True)

            self.tokenizer.save_pretrained(out)

        console.print(f"[green]✓[/green] Model exported to [cyan]{out}[/cyan]")
        return out


# ── Private helpers ──────────────────────────────────────────────────────


def _spinner(description: str):
    """Create a Rich spinner context manager."""
    return Progress(SpinnerColumn(), TextColumn(f"[bold green]{description}"), console=console)


def _log_param_count(model: Any, *, label: str = "Model") -> None:
    """Log total vs trainable parameter counts."""
    total = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    pct = trainable / total * 100 if total else 0.0
    console.print(
        f"[green]✓[/green] {label} — "
        f"[cyan]{trainable:,}[/cyan] / {total:,} trainable "
        f"([cyan]{pct:.2f}%[/cyan])"
    )


def _free_memory() -> None:
    """Aggressively release CUDA and Python memory."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
