#!/usr/bin/env python3
"""Poliora AI spend tracking example without external API calls."""

from __future__ import annotations

from pathlib import Path

from poliora.cost import init_workspace, log_usage, track_openai_call


def fake_openai_call() -> dict:
    """Pretend this came from client.chat.completions.create(...)."""
    return {
        "model": "gpt-4o-mini",
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 450,
        },
    }


def main() -> None:
    root = Path(".")
    init_workspace(root, project="demo-agency", monthly_budget_usd=1000.0)

    manual_event = log_usage(
        provider="openai",
        model="gpt-4o",
        input_tokens=8000,
        output_tokens=2000,
        operation="agent",
        root=root,
    )
    print("Manual event:", manual_event.to_dict())

    captured = track_openai_call(fake_openai_call, operation="support-chat", root=root)
    print("Captured event:", captured.event.to_dict())


if __name__ == "__main__":
    main()
