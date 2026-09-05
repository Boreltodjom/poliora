"""Coverage for attributing local AI usage to the projects that caused it.

The riskiest part here is not arithmetic, it is naming: a project label is
derived from an encoded directory name, and two tools encode paths differently.
Getting that wrong renames someone's project in their own report, which is worse
than showing them a raw directory string.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poliora.cost.usage import UsageEvent
from poliora.cost.workflows import (
    events_by_project,
    read_workflow_usage,
    readable_project_name,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def turn(*, tokens: int = 1_000, days_ago: int = 0) -> str:
    """One Claude Code assistant record carrying usage."""
    half = tokens // 2
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": (NOW - timedelta(days=days_ago)).isoformat(),
            "message": {
                "model": "claude-opus-5",
                "usage": {"input_tokens": half, "output_tokens": tokens - half},
            },
        }
    )


def write_project(home: Path, directory: str, *records: str) -> None:
    target = home / ".claude" / "projects" / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / "session.jsonl").write_text("\n".join(records), encoding="utf-8")


# --- project name decoding -------------------------------------------------


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        ("C--EcoTune", "EcoTune"),
        ("D--work--client-site", "client-site"),
        ("C--Users--dana--my-app", "my-app"),
        ("c--lowercase--drive", "drive"),
    ],
)
def test_windows_paths_keep_hyphens_inside_names(encoded: str, expected: str) -> None:
    # "auto-doc" must not become "doc": on Windows a single hyphen is part of
    # the folder name, not a separator.
    assert readable_project_name(encoded) == expected


def test_a_hyphenated_windows_project_survives_intact() -> None:
    assert readable_project_name("C--auto-doc") == "auto-doc"


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        ("-home-dana-api", "api"),
        ("-Users-dana-projects-web", "web"),
        ("-mnt-data-service", "service"),
    ],
)
def test_posix_paths_split_on_every_hyphen(encoded: str, expected: str) -> None:
    assert readable_project_name(encoded) == expected


def test_an_unencoded_name_is_left_alone() -> None:
    assert readable_project_name("plain-name") == "plain-name"


def test_an_empty_name_falls_back_to_the_original() -> None:
    assert readable_project_name("") == ""


def test_whitespace_only_name_falls_back() -> None:
    assert readable_project_name("   ") == "   "


# --- attribution -----------------------------------------------------------


def test_no_claude_directory_yields_an_empty_report(tmp_path: Path) -> None:
    report = read_workflow_usage(home=tmp_path)
    assert report.projects == ()
    assert report.total_tokens == 0


def test_an_empty_report_says_so_plainly(tmp_path: Path) -> None:
    assert "No project-level usage" in read_workflow_usage(home=tmp_path).describe()


def test_usage_is_grouped_by_project(tmp_path: Path) -> None:
    write_project(tmp_path, "C--alpha", turn(tokens=3_000))
    write_project(tmp_path, "C--beta", turn(tokens=1_000))
    report = read_workflow_usage(home=tmp_path)
    assert [project.project for project in report.projects] == ["alpha", "beta"]


def test_projects_are_ordered_by_consumption(tmp_path: Path) -> None:
    write_project(tmp_path, "C--small", turn(tokens=100))
    write_project(tmp_path, "C--large", turn(tokens=99_000))
    assert read_workflow_usage(home=tmp_path).projects[0].project == "large"


def test_shares_are_reported_as_percentages(tmp_path: Path) -> None:
    write_project(tmp_path, "C--three-quarters", turn(tokens=3_000))
    write_project(tmp_path, "C--one-quarter", turn(tokens=1_000))
    report = read_workflow_usage(home=tmp_path)
    assert report.projects[0].share_pct == 75.0
    assert report.projects[1].share_pct == 25.0


def test_shares_sum_to_about_one_hundred(tmp_path: Path) -> None:
    for index, tokens in enumerate((1_100, 2_300, 700)):
        write_project(tmp_path, f"C--p{index}", turn(tokens=tokens))
    report = read_workflow_usage(home=tmp_path)
    assert sum(project.share_pct for project in report.projects) == pytest.approx(100.0, abs=0.2)


def test_multiple_turns_accumulate(tmp_path: Path) -> None:
    write_project(tmp_path, "C--busy", turn(tokens=1_000), turn(tokens=2_000), turn(tokens=3_000))
    project = read_workflow_usage(home=tmp_path).projects[0]
    assert project.requests == 3
    assert project.tokens == 6_000


def test_work_older_than_the_period_is_excluded(tmp_path: Path) -> None:
    write_project(tmp_path, "C--stale", turn(tokens=9_000, days_ago=90))
    write_project(tmp_path, "C--recent", turn(tokens=1_000, days_ago=1))
    report = read_workflow_usage(home=tmp_path, period_days=30, since=NOW - timedelta(days=30))
    assert [project.project for project in report.projects] == ["recent"]


def test_the_listing_is_capped_and_the_remainder_reported(tmp_path: Path) -> None:
    for index in range(6):
        write_project(tmp_path, f"C--p{index}", turn(tokens=(index + 1) * 1_000))
    report = read_workflow_usage(home=tmp_path, limit=3)
    assert len(report.projects) == 3
    assert report.unattributed_tokens == 1_000 + 2_000 + 3_000


def test_the_dominant_project_is_exposed(tmp_path: Path) -> None:
    write_project(tmp_path, "C--hog", turn(tokens=90_000))
    write_project(tmp_path, "C--minor", turn(tokens=1_000))
    dominant = read_workflow_usage(home=tmp_path).dominant
    assert dominant is not None and dominant.project == "hog"


def test_a_single_project_is_described_differently(tmp_path: Path) -> None:
    write_project(tmp_path, "C--only", turn(tokens=1_000))
    assert "All observed usage" in read_workflow_usage(home=tmp_path).describe()


def test_the_description_names_the_top_project(tmp_path: Path) -> None:
    write_project(tmp_path, "C--kmerimmo", turn(tokens=9_000))
    write_project(tmp_path, "C--other", turn(tokens=1_000))
    assert "kmerimmo" in read_workflow_usage(home=tmp_path).describe()


def test_cache_tokens_are_included_in_attribution(tmp_path: Path) -> None:
    # Agent sessions are mostly cache reads; omitting them would understate the
    # heaviest projects the most.
    record = json.dumps(
        {
            "type": "assistant",
            "timestamp": NOW.isoformat(),
            "message": {
                "model": "claude-opus-5",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 5_000,
                    "cache_creation_input_tokens": 1_000,
                    "output_tokens": 90,
                },
            },
        }
    )
    write_project(tmp_path, "C--cached", record)
    assert read_workflow_usage(home=tmp_path).projects[0].tokens == 6_100


def test_records_without_usage_are_ignored(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        "C--mixed",
        json.dumps({"type": "user", "timestamp": NOW.isoformat(), "message": {"content": "hi"}}),
        turn(tokens=1_000),
    )
    assert read_workflow_usage(home=tmp_path).projects[0].requests == 1


def test_zero_token_turns_do_not_create_a_project(tmp_path: Path) -> None:
    write_project(tmp_path, "C--empty", turn(tokens=0))
    assert read_workflow_usage(home=tmp_path).projects == ()


def test_malformed_lines_do_not_break_attribution(tmp_path: Path) -> None:
    write_project(tmp_path, "C--rough", turn(tokens=1_000) + '\n{"truncated": ')
    assert read_workflow_usage(home=tmp_path).projects[0].tokens == 1_000


def test_first_and_last_seen_are_recorded(tmp_path: Path) -> None:
    write_project(tmp_path, "C--span", turn(tokens=500, days_ago=3), turn(tokens=500, days_ago=1))
    project = read_workflow_usage(home=tmp_path).projects[0]
    assert project.first_seen is not None and project.last_seen is not None
    assert project.first_seen < project.last_seen


def test_report_serializes(tmp_path: Path) -> None:
    write_project(tmp_path, "C--demo", turn(tokens=1_000))
    data = read_workflow_usage(home=tmp_path).to_dict()
    assert {"projects", "total_tokens", "period_days", "description"} <= set(data)
    assert data["projects"][0]["project"] == "demo"


# --- grouping already-loaded events ----------------------------------------


def test_events_group_by_their_recorded_project() -> None:
    events = [
        UsageEvent("anthropic", "m", 100, 100, 0.0, project="alpha"),
        UsageEvent("anthropic", "m", 200, 200, 0.0, project="alpha"),
        UsageEvent("anthropic", "m", 50, 50, 0.0, project="beta"),
    ]
    assert events_by_project(events) == {"alpha": 600, "beta": 100}


def test_grouping_no_events_is_empty() -> None:
    assert events_by_project([]) == {}
