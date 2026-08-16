"""Capture documented ``codex exec --json`` token usage without storing content."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from poliora.cost.pricing import PricingRegistry
from poliora.cost.usage import JsonlUsageStore, UsageEvent
from poliora.cost.workspace import load_workspace


@dataclass(frozen=True)
class CodexCli:
    """A verified OpenAI Codex CLI executable."""

    executable: Path
    version: str
    launcher: tuple[str, ...] = ()

    def command(self, *arguments: str) -> list[str]:
        """Build a subprocess command, including Windows command-shim support."""
        return [*(self.launcher or (str(self.executable),)), *arguments]


def find_codex_cli(candidates: Iterable[str | Path] | None = None) -> CodexCli | None:
    """Find an executable that positively identifies itself as OpenAI Codex."""
    for candidate in _codex_candidates() if candidates is None else candidates:
        path = Path(candidate).expanduser()
        if not path.is_file():
            continue
        launcher = _candidate_launcher(path)
        if launcher is None:
            continue
        cli = CodexCli(path.resolve(), version="", launcher=launcher)
        try:
            result = subprocess.run(  # noqa: S603
                cli.command("--version"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        version = " ".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        if result.returncode == 0 and _is_openai_codex_version(version):
            return CodexCli(path.resolve(), version=version, launcher=launcher)
    return None


def _codex_candidates() -> list[Path]:
    candidates: list[Path] = []
    override = os.getenv("POLIORA_CODEX_PATH")
    if override:
        candidates.append(Path(override))

    names = ("codex.exe", "codex.cmd", "codex.bat", "codex") if os.name == "nt" else ("codex",)
    for raw_directory in os.getenv("PATH", "").split(os.pathsep):
        directory = Path(raw_directory.strip('"')) if raw_directory else None
        if directory is None:
            continue
        candidates.extend(directory / name for name in names)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.resolve(strict=False)))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _is_openai_codex_version(output: str) -> bool:
    return bool(re.search(r"\bcodex-cli\s+v?\d+(?:\.\d+)+", output, flags=re.IGNORECASE))


def _candidate_launcher(path: Path) -> tuple[str, ...] | None:
    resolved = path.resolve()
    if os.name != "nt" or resolved.suffix.lower() not in {".bat", ".cmd"}:
        return (str(resolved),)

    script = resolved.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    bundled_node = resolved.parent / "node.exe"
    node = str(bundled_node) if bundled_node.is_file() else shutil.which("node.exe") or shutil.which("node")
    if not script.is_file() or node is None:
        return None
    return (str(Path(node).resolve()), str(script.resolve()))


def record_codex_exec_event(
    event: dict[str, Any],
    *,
    model: str,
    provider: str = "openai",
    operation: str = "codex-agent",
    project: str | None = None,
    thread_id: str | None = None,
    api_billed: bool = False,
    root: str | Path = ".",
) -> UsageEvent | None:
    """Record one documented Codex ``turn.completed`` event.

    ChatGPT subscription usage has no token-denominated API charge, so it is
    stored with zero dollar cost. API-billed runs use the local price registry.
    Prompt text, agent messages, commands, and file changes are never persisted.
    """
    if event.get("type") != "turn.completed":
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Codex turn.completed event is missing its usage object.")

    input_tokens = _token_count(usage, "input_tokens")
    cached_input_tokens = _token_count(usage, "cached_input_tokens")
    output_tokens = _token_count(usage, "output_tokens")
    reasoning_tokens = _token_count(usage, "reasoning_output_tokens")
    if cached_input_tokens > input_tokens:
        raise ValueError("Codex cached input tokens cannot exceed input tokens.")

    workspace = load_workspace(root)
    registry = PricingRegistry.load(workspace.pricing_path)
    pricing = registry.get(provider, model)
    if api_billed and pricing is not None:
        cost_usd = pricing.estimate(
            input_tokens,
            output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
        billing_basis = "api-estimate"
    elif api_billed:
        cost_usd = 0.0
        billing_basis = "api-unpriced"
    else:
        cost_usd = 0.0
        billing_basis = "chatgpt-subscription"

    usage_event = UsageEvent(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        operation=operation,
        project=project or workspace.project,
        trace_id=thread_id,
        metadata={
            "source": "codex-exec-json",
            "billing_basis": billing_basis,
            "content_collected": False,
        },
    )
    JsonlUsageStore(workspace.usage_path).append(usage_event)
    return usage_event


def _token_count(usage: dict[str, Any], field: str) -> int:
    value = usage.get(field, 0)
    if isinstance(value, bool):
        raise ValueError(f"Codex {field} must be a non-negative integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Codex {field} must be a non-negative integer.") from error
    if parsed < 0:
        raise ValueError(f"Codex {field} must be a non-negative integer.")
    return parsed
