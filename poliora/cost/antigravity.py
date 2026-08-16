"""Supported Antigravity activity capture and plugin installation."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poliora.cost.usage import JsonlUsageStore, UsageEvent
from poliora.cost.workspace import load_workspace


@dataclass(frozen=True)
class AntigravityPluginInstall:
    """Location and scope of an installed Antigravity plugin."""

    path: Path
    scope: str


def record_antigravity_hook_event(
    payload: dict[str, Any],
    *,
    event_name: str,
    root: str | Path | None = None,
) -> UsageEvent | None:
    """Record metadata exposed by an official Antigravity lifecycle hook.

    Antigravity hooks do not currently expose model IDs or token totals. The
    resulting event is therefore activity-only and is excluded from spend.
    """
    if event_name != "pre-invocation":
        return None

    workspace_root = Path(root).resolve() if root is not None else _workspace_root(payload)
    workspace = load_workspace(workspace_root)
    invocation_num = _non_negative_int(payload.get("invocationNum"), "invocationNum")
    conversation_id = str(payload.get("conversationId") or "")
    trace_id = _anonymous_trace_id(conversation_id) if conversation_id else None

    event = UsageEvent(
        provider="google",
        model="antigravity-managed",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        operation="antigravity-agent",
        project=workspace.project,
        trace_id=trace_id,
        metadata={
            "source": "antigravity-pre-invocation-hook",
            "billing_basis": "antigravity-subscription-activity",
            "invocation_num": invocation_num,
            "content_collected": False,
            "token_usage_available": False,
        },
    )
    JsonlUsageStore(workspace.usage_path).append(event)
    return event


def install_antigravity_plugin(
    root: str | Path = ".",
    *,
    scope: str = "workspace",
    force: bool = False,
) -> AntigravityPluginInstall:
    """Install Poliora into one of Antigravity's documented plugin locations."""
    plugin_path = _plugin_path(Path(root).resolve(), scope)
    manifest_path = plugin_path / "plugin.json"
    if manifest_path.exists() and not force:
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Refusing to replace an unreadable plugin at {plugin_path}.") from error
        if existing.get("name") != "poliora":
            raise ValueError(f"A different plugin already exists at {plugin_path}. Use --force to replace it.")

    files = {
        "plugin.json": json.dumps({"name": "poliora"}, indent=2) + "\n",
        "hooks.json": json.dumps(_hook_config(), indent=2) + "\n",
        "skills/poliora-cost/SKILL.md": _skill_markdown(),
        "rules/poliora-privacy.md": _privacy_rule_markdown(),
    }
    for relative_path, content in files.items():
        destination = plugin_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    return AntigravityPluginInstall(path=plugin_path, scope=scope)


def _workspace_root(payload: dict[str, Any]) -> Path:
    paths = payload.get("workspacePaths")
    if isinstance(paths, list):
        candidates = [Path(item).resolve() for item in paths if isinstance(item, str) and item.strip()]
        for candidate in candidates:
            if (candidate / ".poliora" / "config.json").exists():
                return candidate
        if candidates:
            return candidates[0]
    return Path.cwd().resolve()


def _plugin_path(root: Path, scope: str) -> Path:
    if scope == "workspace":
        return root / ".agents" / "plugins" / "poliora"
    if scope == "editor-global":
        return Path.home() / ".gemini" / "config" / "plugins" / "poliora"
    if scope == "cli-global":
        return Path.home() / ".gemini" / "antigravity-cli" / "plugins" / "poliora"
    raise ValueError("scope must be workspace, editor-global, or cli-global")


def _hook_config() -> dict[str, object]:
    return {
        "poliora-activity": {
            "PreInvocation": [
                {
                    "type": "command",
                    "command": _hook_command(),
                    "timeout": 10,
                }
            ]
        }
    }


def _hook_command() -> str:
    arguments = [sys.executable, "-m", "poliora.main", "antigravity-hook", "--event", "pre-invocation"]
    if os.name == "nt":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def _skill_markdown() -> str:
    return """---
name: poliora-cost
description: Inspect and improve local AI usage with Poliora.
---

# Poliora Cost Operations

Use Poliora when the user asks about AI spend, model usage, budgets, or routing savings.

- Run `poliora report` for the local usage summary.
- Run `poliora recommend` for prioritized savings opportunities.
- Run `poliora dashboard` only when the user asks to open the local dashboard.
- For Gemini API code, wrap `google.genai.Client` with `poliora.cost.track_gemini_client`.
- Never infer token counts, model IDs, or dollar cost from Antigravity activity-only events.
"""


def _privacy_rule_markdown() -> str:
    return """# Poliora Privacy

Poliora may record lifecycle metadata and provider-reported usage totals. Do not send prompt text,
responses, source files, commands, transcript contents, credentials, or file-change content to Poliora.
Antigravity subscription activity must remain separate from measured or estimated API spend.
"""


def _anonymous_trace_id(conversation_id: str) -> str:
    digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
    return f"antigravity-{digest[:16]}"


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Antigravity {field} must be a non-negative integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Antigravity {field} must be a non-negative integer.") from error
    if parsed < 0:
        raise ValueError(f"Antigravity {field} must be a non-negative integer.")
    return parsed
