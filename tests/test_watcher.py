"""Coverage for the background capacity watcher and its notifications.

A watcher earns its place by warning someone before they lose an afternoon, and
loses it the moment it becomes noise. So the properties pinned hardest here are
restraint and survival: never say the same thing twice, never interrupt without
a measurement behind it, and never let a failed notification or an unreadable
log kill the loop that was supposed to be watching.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poliora import autostart, notify
from poliora.cost.processes import DISPLAY_NAMES, SUPPORTED_TOOLS, RunningTool, _match_tool
from poliora.watcher import (
    APPROACHING,
    EXHAUSTED,
    Alert,
    WatchSettings,
    WatchState,
    evaluate,
    load_state,
    run_once,
    save_state,
    watch,
    window_key,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class Recorder:
    """A notification sink that records instead of interrupting anyone."""

    def __init__(self, *, delivered: bool = True) -> None:
        self.delivered = delivered
        self.sent: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str, *, urgent: bool = False) -> notify.NotificationResult:
        self.sent.append((title, body))
        return notify.NotificationResult(self.delivered, "test")


def claude_home(tmp_path: Path, *, tokens: int, minutes_ago: int = 5, refusal: bool = False) -> Path:
    """Build a Claude Code log with the given consumption, optionally refused."""
    directory = tmp_path / ".claude" / "projects" / "C--demo"
    directory.mkdir(parents=True, exist_ok=True)
    half = tokens // 2
    records = [
        json.dumps(
            {
                "type": "assistant",
                "timestamp": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
                "message": {
                    "model": "claude-opus-5",
                    "usage": {"input_tokens": half, "output_tokens": tokens - half},
                },
            }
        )
    ]
    if refusal:
        records.append(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": (NOW - timedelta(minutes=minutes_ago - 1)).isoformat(),
                    "quotaLimits": {
                        "status": "rejected",
                        "rateLimitType": "five_hour",
                        "resetsAt": int((NOW + timedelta(minutes=30)).timestamp()),
                    },
                }
            )
        )
    (directory / "session.jsonl").write_text("\n".join(records), encoding="utf-8")
    return tmp_path


# --- deduplication ---------------------------------------------------------


def test_the_same_window_produces_one_identifier() -> None:
    early = NOW.replace(minute=5)
    late = NOW.replace(minute=55)
    assert window_key(early) == window_key(late)


def test_a_later_hour_produces_a_new_identifier() -> None:
    assert window_key(NOW) != window_key(NOW + timedelta(hours=1))


def test_state_remembers_what_was_sent() -> None:
    state = WatchState()
    assert state.already_sent(APPROACHING, "k") is False
    state.record(APPROACHING, "k")
    assert state.already_sent(APPROACHING, "k") is True


def test_state_distinguishes_alert_kinds() -> None:
    state = WatchState()
    state.record(APPROACHING, "k")
    assert state.already_sent(EXHAUSTED, "k") is False


def test_state_round_trips(tmp_path: Path) -> None:
    state = WatchState()
    state.record(EXHAUSTED, "five_hour:2026-09-05T12")
    path = save_state(tmp_path / "state.json", state)
    assert load_state(path).already_sent(EXHAUSTED, "five_hour:2026-09-05T12")


@pytest.mark.parametrize("content", ["{ bad", "[]", '{"sent": 5}'])
def test_a_damaged_state_file_degrades_to_empty(tmp_path: Path, content: str) -> None:
    target = tmp_path / "state.json"
    target.write_text(content, encoding="utf-8")
    assert load_state(target).sent == {}


def test_state_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    save_state(tmp_path / "state.json", WatchState())
    assert list(tmp_path.glob(".*tmp")) == []


# --- restraint -------------------------------------------------------------


def test_nothing_is_said_without_any_logs(tmp_path: Path) -> None:
    assert evaluate(now=NOW, home=tmp_path) == []


def test_nothing_is_said_below_the_threshold(tmp_path: Path) -> None:
    # No refusal has been recorded, so no ceiling exists and there is nothing
    # to be a percentage of. Silence is the only honest output.
    home = claude_home(tmp_path, tokens=1_000, refusal=False)
    assert evaluate(now=NOW, home=home) == []


def test_a_refusal_in_a_light_window_does_not_peg_the_ceiling_to_nothing(tmp_path: Path) -> None:
    # A refusal can land in a light window -- a weekly limit biting, or usage
    # this machine cannot see. The ceiling must not collapse to that, or every
    # window afterwards reads "exhausted" forever.
    from poliora.cost.capacity import estimate_ceiling, read_throttle_events
    from poliora.cost.local_usage import read_claude_code_usage

    home = claude_home(tmp_path, tokens=1_000, refusal=True)
    scan = read_claude_code_usage(home=home)
    ceiling = estimate_ceiling(list(scan.events), read_throttle_events(home=home), window="five_hour")
    assert ceiling.tokens is not None
    assert ceiling.tokens >= 1_000


def test_tool_announcements_are_off_by_default() -> None:
    assert WatchSettings().announce_tools is False


def test_capacity_warnings_are_on_by_default() -> None:
    settings = WatchSettings()
    assert settings.warn_approaching and settings.warn_exhausted


def test_an_already_sent_warning_is_not_repeated(tmp_path: Path) -> None:
    home = claude_home(tmp_path, tokens=200_000, refusal=True)
    state = WatchState()
    first = evaluate(now=NOW, home=home, state=state)
    for alert in first:
        state.record(alert.kind, alert.key)
    assert evaluate(now=NOW, home=home, state=state) == []


def test_disabling_exhaustion_warnings_silences_them(tmp_path: Path) -> None:
    home = claude_home(tmp_path, tokens=200_000, refusal=True)
    settings = WatchSettings(warn_exhausted=False, warn_approaching=False, suggest_spare_capacity=False)
    assert evaluate(now=NOW, home=home, settings=settings) == []


# --- alerts that are warranted ---------------------------------------------


def test_an_exhausted_window_raises_an_urgent_alert(tmp_path: Path) -> None:
    home = claude_home(tmp_path, tokens=200_000, refusal=True)
    alerts = evaluate(now=NOW, home=home)
    exhausted = [alert for alert in alerts if alert.kind == EXHAUSTED]
    assert exhausted and exhausted[0].urgent


def test_an_exhaustion_alert_names_the_reset_time(tmp_path: Path) -> None:
    home = claude_home(tmp_path, tokens=200_000, refusal=True)
    alerts = evaluate(now=NOW, home=home)
    assert any("Resets at" in alert.body for alert in alerts if alert.kind == EXHAUSTED)


def test_alerts_serialize(tmp_path: Path) -> None:
    alert = Alert(kind=APPROACHING, key="k", title="t", body="b")
    assert set(alert.to_dict()) == {"kind", "key", "title", "body", "urgent"}


# --- delivery --------------------------------------------------------------


def test_run_once_delivers_and_remembers(tmp_path: Path) -> None:
    home = claude_home(tmp_path, tokens=200_000, refusal=True)
    recorder = Recorder()
    state_path = tmp_path / "state.json"
    delivered = run_once(state_path=state_path, home=home, now=NOW, sender=recorder)
    assert delivered and recorder.sent
    assert load_state(state_path).sent


def test_a_failed_notification_is_not_remembered(tmp_path: Path) -> None:
    # Otherwise a muted session silently consumes the one warning that mattered.
    home = claude_home(tmp_path, tokens=200_000, refusal=True)
    state_path = tmp_path / "state.json"
    run_once(state_path=state_path, home=home, now=NOW, sender=Recorder(delivered=False))
    assert load_state(state_path).sent == {}


def test_run_once_survives_an_exploding_sender(tmp_path: Path) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("notifier is on fire")

    home = claude_home(tmp_path, tokens=200_000, refusal=True)
    assert run_once(state_path=tmp_path / "s.json", home=home, now=NOW, sender=boom) == []


def test_run_once_survives_an_unreadable_home(tmp_path: Path) -> None:
    assert run_once(state_path=tmp_path / "s.json", home=tmp_path / "nope", now=NOW) == []


def test_the_loop_runs_a_bounded_number_of_times(tmp_path: Path) -> None:
    home = claude_home(tmp_path, tokens=1_000)
    ticks: list[float] = []
    watch(
        state_path=tmp_path / "s.json",
        home=home,
        iterations=3,
        interval=timedelta(seconds=15),
        sender=Recorder(),
        sleeper=ticks.append,
    )
    assert len(ticks) == 2  # sleeps between iterations, not after the last


def test_the_loop_never_sleeps_less_than_the_floor(tmp_path: Path) -> None:
    ticks: list[float] = []
    watch(
        state_path=tmp_path / "s.json",
        home=tmp_path,
        iterations=2,
        interval=timedelta(seconds=1),
        sender=Recorder(),
        sleeper=ticks.append,
    )
    assert ticks and all(tick >= 15 for tick in ticks)


# --- settings --------------------------------------------------------------


def test_settings_round_trip() -> None:
    settings = WatchSettings(announce_tools=True, threshold_pct=65.0)
    assert WatchSettings.from_dict(settings.to_dict()) == settings


def test_unknown_settings_keys_are_ignored() -> None:
    assert WatchSettings.from_dict({"announce_tools": True, "nonsense": 1}).announce_tools is True


def test_settings_from_junk_falls_back_to_defaults() -> None:
    assert WatchSettings.from_dict([]) == WatchSettings()  # type: ignore[arg-type]


# --- process detection -----------------------------------------------------


@pytest.mark.parametrize(
    ("process", "tool"),
    [
        ("claude", "claude-code"),
        ("claude.exe", "claude-code"),
        ("CLAUDE.EXE", "claude-code"),
        ("codex", "codex"),
        ("cursor.exe", "cursor"),
        ("antigravity", "antigravity"),
    ],
)
def test_supported_process_names_are_recognized(process: str, tool: str) -> None:
    assert _match_tool(process) == tool


@pytest.mark.parametrize(
    "process",
    ["chrome.exe", "python", "notepad", "", "   ", "claudette", "my-cursor-theme"],
)
def test_unrelated_processes_are_not_reported(process: str) -> None:
    # A substring match would report a text editor as somebody's AI tool.
    assert _match_tool(process) is None


def test_every_supported_tool_has_a_display_name() -> None:
    for tool in SUPPORTED_TOOLS:
        assert tool in DISPLAY_NAMES


def test_running_tool_serializes() -> None:
    assert set(RunningTool("codex", "Codex", 2).to_dict()) == {"id", "display_name", "process_count"}


# --- notification plumbing -------------------------------------------------


def test_notification_result_serializes() -> None:
    assert set(notify.NotificationResult(True, "test").to_dict()) == {"delivered", "backend", "detail"}


def test_long_titles_are_clipped_to_stay_glanceable() -> None:
    result = notify._clip("word " * 100, notify.MAX_TITLE)
    assert len(result) <= notify.MAX_TITLE


def test_short_text_is_left_alone() -> None:
    assert notify._clip("Poliora", 64) == "Poliora"


def test_whitespace_is_collapsed() -> None:
    assert notify._clip("a\n\n  b", 64) == "a b"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("<b>", "&lt;b&gt;"), ("a&b", "a&amp;b"), ('q"q', "q&quot;q"), ("it's", "it&apos;s")],
)
def test_toast_text_is_xml_escaped(raw: str, expected: str) -> None:
    # A model or project name containing markup must not break the document.
    assert notify._escape_xml(raw) == expected


def test_applescript_quotes_are_escaped() -> None:
    assert notify._escape_applescript('say "hi"') == 'say \\"hi\\"'


# --- autostart -------------------------------------------------------------


def test_autostart_reports_not_installed_on_a_clean_home(tmp_path: Path) -> None:
    status = autostart.status(home=tmp_path)
    if status.supported:
        assert status.installed is False


def test_autostart_install_then_remove_round_trips(tmp_path: Path) -> None:
    installed = autostart.install(home=tmp_path, command="echo test")
    if not installed.supported:
        pytest.skip("login startup is not supported on this platform")
    assert installed.installed and installed.path is not None and installed.path.exists()
    removed = autostart.remove(home=tmp_path)
    assert removed.installed is False
    assert installed.path is not None and not installed.path.exists()


def test_the_autostart_entry_is_a_plain_readable_file(tmp_path: Path) -> None:
    # Something that starts itself at login should be inspectable and deletable.
    installed = autostart.install(home=tmp_path, command="echo test")
    if not installed.supported or installed.path is None:
        pytest.skip("login startup is not supported on this platform")
    body = installed.path.read_text(encoding="utf-8")
    assert "echo test" in body
    assert "Poliora" in body or "poliora" in body


def test_removing_an_absent_entry_says_so(tmp_path: Path) -> None:
    # tmp_path must genuinely sandbox this: resolving to the real Startup
    # folder would let a test delete somebody's actual login entry.
    entry = autostart.entry_path(home=tmp_path)
    if entry is not None:
        assert str(entry).startswith(str(tmp_path))
    result = autostart.remove(home=tmp_path)
    if result.supported:
        assert "not set to start at login" in result.detail


def test_autostart_status_serializes(tmp_path: Path) -> None:
    assert {"installed", "path", "platform", "supported"} <= set(autostart.status(home=tmp_path).to_dict())


def test_a_quoted_command_splits_into_arguments() -> None:
    parts = autostart._split_command('"C:/Program Files/py.exe" -m poliora.main watch')
    assert parts == ["C:/Program Files/py.exe", "-m", "poliora.main", "watch"]
