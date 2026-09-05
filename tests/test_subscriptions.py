"""Tests for local subscription plan decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from poliora.cost import (
    SubscriptionStore,
    UsageEvent,
    build_usage_report,
    init_workspace,
    new_subscription_plan,
    summarize_plan_stack,
)


def subscription_event(days_ago: int, value: float = 12.0) -> UsageEvent:
    return UsageEvent(
        provider="openai",
        model="gpt-5.6-sol",
        input_tokens=1_000,
        output_tokens=500,
        cost_usd=0.0,
        operation="codex",
        timestamp=(datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
        metadata={"billing_basis": "subscription-included", "equivalent_api_cost_usd": value},
    )


def test_subscription_store_keeps_plan_details_local(tmp_path) -> None:
    workspace = init_workspace(tmp_path)
    plan = new_subscription_plan({"tool": "codex", "display_name": "ChatGPT Plus", "monthly_cost_usd": 20})

    SubscriptionStore(workspace.subscriptions_path).save(plan)

    assert SubscriptionStore(workspace.subscriptions_path).read_all() == [plan]
    assert '"monthly_cost_usd": 20.0' in workspace.subscriptions_path.read_text(encoding="utf-8")


def test_plan_stack_labels_low_usage_as_review_not_auto_cancellation() -> None:
    events = [subscription_event(30, 1.0), subscription_event(0, 1.0)]
    plan = new_subscription_plan({"tool": "codex", "display_name": "ChatGPT Plus", "monthly_cost_usd": 20})

    summary = summarize_plan_stack([plan], events, build_usage_report(events))

    assert summary["monthly_cost_usd"] == 20.0
    assert summary["plans"][0]["status"] == "review"
    assert "cancel" not in summary["plans"][0]["explanation"].lower()


def test_plan_stack_requires_usage_before_making_value_claims() -> None:
    plan = new_subscription_plan({"tool": "claude-code", "display_name": "Claude Pro", "monthly_cost_usd": 20})

    summary = summarize_plan_stack([plan], [], build_usage_report([]))

    assert summary["plans"][0]["status"] == "needs-usage"
    assert summary["monthly_equivalent_api_value_usd"] == 0.0


@pytest.mark.parametrize("price", [-1, 100_001])
def test_subscription_price_is_bounded(price: float) -> None:
    with pytest.raises(ValueError, match="Monthly price"):
        new_subscription_plan({"tool": "codex", "display_name": "Plan", "monthly_cost_usd": price})
