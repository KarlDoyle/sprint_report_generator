"""Tests for MultiTeamRollup and SprintReportGenerator.generate_multi_team_report
(sprint_report_generator.core) — the TPM/director cross-team view.
"""

from __future__ import annotations

import json

from conftest import raw_issue, raw_sprint

from sprint_report_generator.core import (
    JiraIssue,
    MultiTeamRollup,
    Sprint,
    SprintMetricsCalculator,
    SprintReportGenerator,
)


def _team_report(team: str, sprint_id: int, issues: list[JiraIssue], **sprint_kwargs) -> dict:
    sprint = Sprint(raw_sprint(sprint_id=sprint_id, **sprint_kwargs))
    metrics = SprintMetricsCalculator(sprint, issues).calculate_metrics()
    return {"team": team, "metrics": metrics}


def test_rollup_aggregates_totals_across_teams() -> None:
    team_a = _team_report(
        "Payments",
        1,
        [
            JiraIssue(raw_issue(key="A-1", status="Done", created="2025-12-30T09:00:00.000Z")),
            JiraIssue(raw_issue(key="A-2", status="To Do", created="2025-12-30T09:00:00.000Z")),
        ],
        start_date="2026-01-01T00:00:00.000Z",
    )
    team_b = _team_report(
        "Platform",
        2,
        [
            JiraIssue(raw_issue(key="B-1", status="Done", created="2025-12-30T09:00:00.000Z")),
            JiraIssue(raw_issue(key="B-2", status="Done", created="2026-01-05T09:00:00.000Z")),
        ],
        start_date="2026-01-01T00:00:00.000Z",
    )

    rollup = MultiTeamRollup([team_a, team_b]).aggregate()

    assert rollup["team_count"] == 2
    assert rollup["totals"]["total_issues"] == 4
    assert rollup["totals"]["total_completed"] == 3
    assert len(rollup["per_team"]) == 2
    assert {t["team"] for t in rollup["per_team"]} == {"Payments", "Platform"}


def test_rollup_handles_zero_teams_without_division_errors() -> None:
    rollup = MultiTeamRollup([]).aggregate()
    assert rollup["team_count"] == 0
    assert rollup["execution_focus_ratio"] == 0
    assert rollup["scope_creep_ratio"] == 0
    assert rollup["teams_above_average_scope_creep"] == []


def test_rollup_flags_teams_notably_above_average_scope_creep() -> None:
    # Team Low: 0% scope creep (nothing added mid-sprint).
    team_low = _team_report(
        "Low",
        1,
        [JiraIssue(raw_issue(key="L-1", status="Done", created="2025-12-30T09:00:00.000Z"))],
        start_date="2026-01-01T00:00:00.000Z",
    )
    # Team High: 100% scope creep (everything added mid-sprint) — should be flagged.
    team_high = _team_report(
        "High",
        2,
        [JiraIssue(raw_issue(key="H-1", status="Done", created="2026-01-05T09:00:00.000Z"))],
        start_date="2026-01-01T00:00:00.000Z",
    )

    rollup = MultiTeamRollup([team_low, team_high]).aggregate()

    assert "High" in rollup["teams_above_average_scope_creep"]
    assert "Low" not in rollup["teams_above_average_scope_creep"]


class _FakeMultiTeamJiraClient:
    """Duck-typed JiraClient stand-in keyed by sprint_id."""

    def __init__(self, sprints: dict[int, Sprint], issues: dict[int, list[JiraIssue]]):
        self._sprints = sprints
        self._issues = issues

    def get_sprint(self, sprint_id: int) -> Sprint:
        return self._sprints[sprint_id]

    def get_sprint_issues(self, sprint_id: int) -> list[JiraIssue]:
        return self._issues[sprint_id]


def test_generate_multi_team_report_writes_per_team_and_rollup_files(tmp_path) -> None:
    sprint_a = Sprint(
        raw_sprint(sprint_id=1, name="Payments Sprint", start_date="2026-01-01T00:00:00.000Z")
    )
    sprint_b = Sprint(
        raw_sprint(sprint_id=2, name="Platform Sprint", start_date="2026-01-01T00:00:00.000Z")
    )
    issues_a = [JiraIssue(raw_issue(key="A-1", status="Done", created="2025-12-30T09:00:00.000Z"))]
    issues_b = [JiraIssue(raw_issue(key="B-1", status="To Do", created="2025-12-30T09:00:00.000Z"))]

    fake_client = _FakeMultiTeamJiraClient({1: sprint_a, 2: sprint_b}, {1: issues_a, 2: issues_b})
    generator = SprintReportGenerator(fake_client)

    result = generator.generate_multi_team_report({"Payments": 1, "Platform": 2}, tmp_path)

    assert result["rollup"]["team_count"] == 2
    assert len(result["teams"]) == 2

    rollup_file = tmp_path / "rollup_summary.md"
    assert rollup_file.exists()
    rollup_text = rollup_file.read_text(encoding="utf-8")
    assert "Multi-Team Sprint Rollup" in rollup_text
    assert "Payments" in rollup_text
    assert "Platform" in rollup_text
    assert "not a performance verdict" in rollup_text

    # Each team still gets its own full per-sprint report directory.
    team_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(team_dirs) == 2
    for d in team_dirs:
        assert (d / "sprint_summary.md").exists()
        assert (d / "issues.json").exists()
        issues_data = json.loads((d / "issues.json").read_text(encoding="utf-8"))
        assert len(issues_data) == 1
