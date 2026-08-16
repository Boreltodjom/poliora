"""Coverage for reading usage out of AI coding tools' own local session logs.

Two properties matter more than any parsing detail and are pinned hardest here:
content never leaves the log, and a subscription turn is never reported as
dollars spent. Everything else is tolerance for logs written by another process
that may be truncated, malformed, or in a shape we did not anticipate.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poliora.cost.local_usage import (
    SUBSCRIPTION,
    read_claude_code_usage,
    read_codex_usage,
    scan_local_usage,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def claude_turn(
    *,
    model: str = "claude-opus-5",
    input_tokens: int = 100,
    cache_read: int = 0,
    cache_creation: int = 0,
    output_tokens: int = 50,
    days_ago: int = 0,
    text: str = "SECRET PROMPT TEXT",
) -> str:
    """One Claude Code assistant record, including content we must not read."""
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": (NOW - timedelta(days=days_ago)).isoformat(),
            "message": {
                "model": model,
                "content": [{"type": "text", "text": text}],
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_creation,
                    "output_tokens": output_tokens,
                    "output_tokens_details": {"thinking_tokens": 7},
                },
            },
        }
    )


def codex_turn(
    *,
    input_tokens: int = 100,
    cached: int = 0,
    output_tokens: int = 50,
    days_ago: int = 0,
    plan: str | None = "plus",
    used_percent: float = 12.5,
) -> str:
    """One Codex ``token_count`` event, optionally carrying plan and quota."""
    payload: dict = {
        "type": "token_count",
        "info": {
            "last_token_usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached,
                "output_tokens": output_tokens,
                "reasoning_output_tokens": 5,
            },
            "total_token_usage": {"input_tokens": 999_999, "output_tokens": 999_999},
        },
    }
    if plan is not None:
        payload["rate_limits"] = {
            "plan_type": plan,
            "primary": {"used_percent": used_percent, "window_minutes": 300, "resets_at": 1786000000},
        }
    return json.dumps(
        {"type": "event_msg", "timestamp": (NOW - timedelta(days=days_ago)).isoformat(), "payload": payload}
    )


@pytest.fixture()
def claude_home(tmp_path: Path) -> Path:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "session-a.jsonl").write_text(
        "\n".join([claude_turn(), claude_turn(model="claude-haiku-4-5")]), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture()
def codex_home(tmp_path: Path) -> Path:
    directory = tmp_path / ".codex" / "sessions"
    directory.mkdir(parents=True)
    (directory / "rollout-2026-08-16T10-00-00-abc.jsonl").write_text(
        "\n".join([json.dumps({"type": "turn_context", "model": "gpt-5.6-sol"}), codex_turn()]),
        encoding="utf-8",
    )
    return tmp_path


# --- absence ---------------------------------------------------------------


def test_missing_claude_logs_report_unavailable(tmp_path: Path) -> None:
    scan = read_claude_code_usage(home=tmp_path)
    assert scan.available is False
    assert scan.events == ()


def test_missing_codex_logs_report_unavailable(tmp_path: Path) -> None:
    assert read_codex_usage(home=tmp_path).available is False


def test_unavailable_tool_explains_itself(tmp_path: Path) -> None:
    assert "No Claude Code session logs" in read_claude_code_usage(home=tmp_path).note


def test_scan_covers_every_supported_tool(tmp_path: Path) -> None:
    assert {scan.tool for scan in scan_local_usage(home=tmp_path)} == {"claude-code", "codex"}


# --- privacy ---------------------------------------------------------------


def test_prompt_text_is_never_captured(claude_home: Path) -> None:
    # The content sits in the same record as the token counts; reading one must
    # not drag in the other.
    serialized = json.dumps([event.to_dict() for event in read_claude_code_usage(home=claude_home).events])
    assert "SECRET PROMPT TEXT" not in serialized


def test_events_declare_that_no_content_was_collected(claude_home: Path) -> None:
    for event in read_claude_code_usage(home=claude_home).events:
        assert event.metadata["content_collected"] is False


def test_session_identity_is_hashed_not_stored_verbatim(claude_home: Path) -> None:
    trace = read_claude_code_usage(home=claude_home).events[0].trace_id
    assert trace is not None
    assert "session-a" not in trace
    assert trace.startswith("claude-code-")


def test_the_same_session_yields_a_stable_trace(claude_home: Path) -> None:
    events = read_claude_code_usage(home=claude_home).events
    assert len({event.trace_id for event in events}) == 1


# --- subscription accounting ----------------------------------------------


def test_subscription_turns_are_recorded_at_zero_cost(claude_home: Path) -> None:
    # Inventing dollar spend for an already-paid flat-fee turn would be the
    # single most damaging thing this reader could do.
    for event in read_claude_code_usage(home=claude_home).events:
        assert event.cost_usd == 0.0


def test_subscription_turns_are_labelled_as_such(claude_home: Path) -> None:
    for event in read_claude_code_usage(home=claude_home).events:
        assert event.metadata["billing_basis"] == SUBSCRIPTION


def test_equivalent_api_value_is_recorded_per_event(claude_home: Path) -> None:
    for event in read_claude_code_usage(home=claude_home).events:
        assert event.metadata["equivalent_api_cost_usd"] >= 0


def test_equivalent_api_value_totals_across_the_scan(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    # 1M input + 1M output on Opus 5 ($5/$25) is $30 of equivalent value.
    (directory / "s.jsonl").write_text(
        claude_turn(input_tokens=1_000_000, output_tokens=1_000_000), encoding="utf-8"
    )
    assert read_claude_code_usage(home=tmp_path).equivalent_api_cost_usd == pytest.approx(30.0)


def test_an_unpriced_model_is_reported_rather_than_valued_at_zero(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text(claude_turn(model="claude-from-the-future"), encoding="utf-8")
    scan = read_claude_code_usage(home=tmp_path)
    assert scan.unpriced_models == ("anthropic/claude-from-the-future",)


# --- token accounting ------------------------------------------------------


def test_cache_tokens_count_toward_input(tmp_path: Path) -> None:
    # Omitting cache reads would understate real consumption enormously; a
    # long agent session is mostly cache reads.
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text(
        claude_turn(input_tokens=10, cache_read=1_000, cache_creation=500, output_tokens=0),
        encoding="utf-8",
    )
    event = read_claude_code_usage(home=tmp_path).events[0]
    assert event.input_tokens == 1_510
    assert event.cached_input_tokens == 1_000


def test_thinking_tokens_are_captured(claude_home: Path) -> None:
    assert read_claude_code_usage(home=claude_home).events[0].reasoning_tokens == 7


def test_codex_uses_per_turn_usage_not_the_cumulative_total(codex_home: Path) -> None:
    # total_token_usage is cumulative; summing it across a session would
    # multiply-count every earlier turn.
    event = read_codex_usage(home=codex_home).events[0]
    assert event.input_tokens == 100
    assert event.output_tokens == 50


def test_codex_cached_tokens_cannot_exceed_input(tmp_path: Path) -> None:
    directory = tmp_path / ".codex" / "sessions"
    directory.mkdir(parents=True)
    (directory / "rollout-x.jsonl").write_text(codex_turn(input_tokens=10, cached=9_999), encoding="utf-8")
    event = read_codex_usage(home=tmp_path).events[0]
    assert event.cached_input_tokens <= event.input_tokens


def test_turns_with_no_tokens_are_skipped(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text(claude_turn(input_tokens=0, output_tokens=0), encoding="utf-8")
    assert read_claude_code_usage(home=tmp_path).events == ()


# --- model mix -------------------------------------------------------------


def test_model_mix_counts_requests_per_model(claude_home: Path) -> None:
    assert read_claude_code_usage(home=claude_home).model_mix() == {
        "claude-opus-5": 1,
        "claude-haiku-4-5": 1,
    }


def test_codex_attributes_turns_to_the_session_model(codex_home: Path) -> None:
    assert read_codex_usage(home=codex_home).events[0].model == "gpt-5.6-sol"


def test_model_mix_appears_in_the_serialized_scan(claude_home: Path) -> None:
    models = {row["model"] for row in read_claude_code_usage(home=claude_home).to_dict()["models"]}
    assert models == {"claude-opus-5", "claude-haiku-4-5"}


# --- plan and quota --------------------------------------------------------


def test_codex_plan_type_is_detected(codex_home: Path) -> None:
    plan = read_codex_usage(home=codex_home).plan
    assert plan is not None and plan.plan_type == "plus"


def test_codex_quota_utilization_is_detected(codex_home: Path) -> None:
    plan = read_codex_usage(home=codex_home).plan
    assert plan is not None
    assert plan.quota_used_pct == 12.5
    assert plan.quota_window_minutes == 300


def test_a_log_without_plan_data_reports_none_rather_than_guessing(tmp_path: Path) -> None:
    directory = tmp_path / ".codex" / "sessions"
    directory.mkdir(parents=True)
    (directory / "rollout-x.jsonl").write_text(codex_turn(plan=None), encoding="utf-8")
    plan = read_codex_usage(home=tmp_path).plan
    assert plan is not None and plan.plan_type is None


def test_plan_serializes(codex_home: Path) -> None:
    plan = read_codex_usage(home=codex_home).to_dict()["plan"]
    assert plan is not None and plan["plan_type"] == "plus"


# --- resilience ------------------------------------------------------------


def test_a_truncated_final_line_does_not_lose_earlier_turns(tmp_path: Path) -> None:
    # The tool writes these logs live, so a half-written last line is normal.
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text(claude_turn() + "\n{\"message\": {\"usa", encoding="utf-8")
    assert len(read_claude_code_usage(home=tmp_path).events) == 1


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text("\n\n" + claude_turn() + "\n\n", encoding="utf-8")
    assert len(read_claude_code_usage(home=tmp_path).events) == 1


def test_records_in_an_unexpected_shape_are_ignored(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text(
        "\n".join(['{"message": "not an object"}', '{"unrelated": true}', "[1,2,3]", claude_turn()]),
        encoding="utf-8",
    )
    assert len(read_claude_code_usage(home=tmp_path).events) == 1


def test_an_empty_log_file_is_handled(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text("", encoding="utf-8")
    scan = read_claude_code_usage(home=tmp_path)
    assert scan.available is True and scan.events == ()


def test_sessions_are_counted_across_projects(tmp_path: Path) -> None:
    for project in ("C--one", "C--two"):
        directory = tmp_path / ".claude" / "projects" / project
        directory.mkdir(parents=True)
        (directory / "s.jsonl").write_text(claude_turn(), encoding="utf-8")
    assert read_claude_code_usage(home=tmp_path).sessions == 2


def test_codex_reads_both_live_and_archived_sessions(tmp_path: Path) -> None:
    for directory_name in ("sessions", "archived_sessions"):
        directory = tmp_path / ".codex" / directory_name
        directory.mkdir(parents=True)
        (directory / "rollout-x.jsonl").write_text(codex_turn(), encoding="utf-8")
    assert read_codex_usage(home=tmp_path).sessions == 2


# --- time filtering --------------------------------------------------------


def test_since_filters_out_older_turns(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text(
        "\n".join([claude_turn(days_ago=60), claude_turn(days_ago=1)]), encoding="utf-8"
    )
    scan = read_claude_code_usage(home=tmp_path, since=NOW - timedelta(days=30))
    assert len(scan.events) == 1


def test_no_since_returns_the_full_history(tmp_path: Path) -> None:
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True)
    (directory / "s.jsonl").write_text(
        "\n".join([claude_turn(days_ago=400), claude_turn(days_ago=1)]), encoding="utf-8"
    )
    assert len(read_claude_code_usage(home=tmp_path).events) == 2


def test_observed_window_is_reported(claude_home: Path) -> None:
    scan = read_claude_code_usage(home=claude_home)
    assert scan.first_seen is not None and scan.last_seen is not None


def test_scan_serializes_completely(claude_home: Path) -> None:
    data = read_claude_code_usage(home=claude_home).to_dict()
    assert {"tool", "requests", "sessions", "total_tokens", "equivalent_api_cost_usd",
            "plan", "models", "note"} <= set(data)
