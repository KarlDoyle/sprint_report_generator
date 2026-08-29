"""Tests for JiraIssue / Sprint field parsing (sprint_report_generator.core)."""

from __future__ import annotations

from datetime import date

from conftest import raw_issue, raw_sprint

from sprint_report_generator.core import JiraIssue, Sprint


def test_jira_issue_parses_basic_fields() -> None:
    issue = JiraIssue(
        raw_issue(
            key="PROJ-7",
            summary="Fix the thing",
            status="In Progress",
            issue_type="Bug",
            assignee="Karl Doyle",
            priority="Highest",
            components=["backend", "api"],
            labels=["urgent"],
        )
    )
    assert issue.key == "PROJ-7"
    assert issue.summary == "Fix the thing"
    assert issue.status == "In Progress"
    assert issue.issue_type == "Bug"
    assert issue.assignee == "Karl Doyle"
    assert issue.priority == "Highest"
    assert issue.components == ["backend", "api"]
    assert issue.labels == ["urgent"]


def test_jira_issue_defaults_for_missing_optional_fields() -> None:
    issue = JiraIssue(raw_issue(assignee=None, priority=None))
    assert issue.assignee == "Unassigned"
    assert issue.priority == "Medium"
    assert issue.story_points == 0


def test_jira_issue_missing_issuetype_and_status_default_to_unknown() -> None:
    data = raw_issue()
    data["fields"]["issuetype"] = None
    data["fields"]["status"] = None
    issue = JiraIssue(data)
    assert issue.issue_type == "Unknown"
    assert issue.status == "Unknown"


def test_jira_issue_reads_first_populated_story_points_field() -> None:
    # customfield_10002 is checked before customfield_10016 — a value there wins.
    data = raw_issue(story_points=5, story_points_field="customfield_10002")
    data["fields"]["customfield_10016"] = 8  # should be ignored
    issue = JiraIssue(data)
    assert issue.story_points == 5.0


def test_jira_issue_falls_back_to_second_story_points_field() -> None:
    issue = JiraIssue(raw_issue(story_points=3, story_points_field="customfield_10016"))
    assert issue.story_points == 3.0


def test_jira_issue_non_numeric_story_points_field_is_ignored() -> None:
    data = raw_issue()
    data["fields"]["customfield_10002"] = "not-a-number"
    data["fields"]["customfield_10004"] = 2
    issue = JiraIssue(data)
    # customfield_10002 fails float() and is skipped; customfield_10004 is used.
    assert issue.story_points == 2.0


def test_sprint_parses_dates_with_z_suffix() -> None:
    sprint = Sprint(raw_sprint(start_date="2026-01-01T00:00:00.000Z"))
    assert sprint.start_date == date(2026, 1, 1)


def test_sprint_handles_missing_dates() -> None:
    sprint = Sprint(raw_sprint(start_date=None, end_date=None))
    assert sprint.start_date is None
    assert sprint.end_date is None


def test_set_sprint_timing_flags_issue_created_after_sprint_start() -> None:
    issue = JiraIssue(raw_issue(created="2026-01-05T09:00:00.000+0000"))
    issue.set_sprint_timing(date(2026, 1, 1))
    assert issue.was_added_mid_sprint is True


def test_set_sprint_timing_leaves_planned_issue_unflagged() -> None:
    issue = JiraIssue(raw_issue(created="2025-12-30T09:00:00.000+0000"))
    issue.set_sprint_timing(date(2026, 1, 1))
    assert issue.was_added_mid_sprint is False


def test_set_sprint_timing_noop_without_sprint_start_date() -> None:
    issue = JiraIssue(raw_issue(created="2026-01-05T09:00:00.000+0000"))
    issue.set_sprint_timing(None)
    assert issue.was_added_mid_sprint is False
