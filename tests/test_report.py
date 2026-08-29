"""Tests for SprintReportGenerator (sprint_report_generator.core).

Uses a fake JiraClient (duck-typed: get_sprint / get_sprint_issues) so no
network access is required.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import raw_issue, raw_sprint

from sprint_report_generator.core import JiraIssue, Sprint, SprintReportGenerator


class FakeJiraClient:
    """Stands in for JiraClient: returns fixed sprint/issue data."""

    def __init__(self, sprint: Sprint, issues: list[JiraIssue]):
        self._sprint = sprint
        self._issues = issues
        self.get_sprint_issues_calls = 0

    def get_sprint(self, sprint_id: int) -> Sprint:
        return self._sprint

    def get_sprint_issues(self, sprint_id: int) -> list[JiraIssue]:
        self.get_sprint_issues_calls += 1
        return self._issues


def test_create_output_directory_sanitizes_special_characters(tmp_path) -> None:
    sprint = Sprint(raw_sprint(sprint_id=99, name='Sprint 99: "Q1 / Launch"?'))
    generator = SprintReportGenerator(FakeJiraClient(sprint, []))

    out = generator._create_output_directory(sprint, tmp_path)

    assert out.exists()
    assert out.parent == tmp_path
    # No raw filesystem-unsafe characters should survive.
    for bad_char in '<>:"/\\|?*':
        assert bad_char not in out.name
    assert out.name.startswith("sprint-99-2026-01-01-")


def test_create_output_directory_falls_back_to_today_when_no_start_date(tmp_path) -> None:
    sprint = Sprint(raw_sprint(sprint_id=5, start_date=None, name="No Dates"))
    generator = SprintReportGenerator(FakeJiraClient(sprint, []))

    out = generator._create_output_directory(sprint, tmp_path)

    # Directory name still has 3 dash-separated segments before the sprint name:
    # sprint-<id>-<date>-<name>
    assert out.name.startswith("sprint-5-")
    assert out.name.endswith("No-Dates")


def test_generate_report_writes_expected_files_with_correct_content(tmp_path) -> None:
    sprint = Sprint(
        raw_sprint(sprint_id=42, name="Sprint 42", start_date="2026-01-01T00:00:00.000Z")
    )
    issues = [
        JiraIssue(raw_issue(key="P-1", status="Done", created="2025-12-30T09:00:00.000+0000")),
        JiraIssue(raw_issue(key="P-2", status="To Do", created="2025-12-30T09:00:00.000+0000")),
    ]
    fake_client = FakeJiraClient(sprint, issues)
    generator = SprintReportGenerator(fake_client)

    result = generator.generate_report(42, tmp_path)

    output_dir = Path(result["output_directory"])
    assert output_dir.exists()
    assert output_dir.parent == tmp_path

    # sprint_summary.md
    summary_text = (output_dir / "sprint_summary.md").read_text(encoding="utf-8")
    assert "Sprint 42" in summary_text
    assert "Total Issues: 2" in summary_text
    assert "Total Completed: 1" in summary_text

    # issues.json
    issues_data = json.loads((output_dir / "issues.json").read_text(encoding="utf-8"))
    assert len(issues_data) == 2
    assert {i["key"] for i in issues_data} == {"P-1", "P-2"}
    assert issues_data[0]["raw_data"]["key"] == "P-1"

    # readme.md
    readme_text = (output_dir / "readme.md").read_text(encoding="utf-8")
    assert "Sprint 42 Report Files" in readme_text

    # Return value
    assert result["sprint_id"] == 42
    assert result["sprint_name"] == "Sprint 42"
    assert result["total_issues"] == 2
    assert result["completed_issues"] == 1

    # get_sprint_issues is called twice: once in generate_report, once again
    # in _create_readme to count issues for the docstring blurb.
    assert fake_client.get_sprint_issues_calls == 2
