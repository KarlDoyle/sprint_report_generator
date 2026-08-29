"""Tests for SprintMetricsCalculator (sprint_report_generator.core).

Exercises the actual completion-rate / scope-creep / throughput arithmetic,
not just smoke checks — each expected number below is hand-computed from the
fixture issue set.
"""

from __future__ import annotations

from conftest import raw_issue, raw_sprint

from sprint_report_generator.core import JiraIssue, Sprint, SprintMetricsCalculator


def _issue(status: str, created: str) -> JiraIssue:
    return JiraIssue(raw_issue(status=status, created=created))


def test_calculate_metrics_mixed_planned_and_added_issues() -> None:
    sprint = Sprint(raw_sprint(start_date="2026-01-01T00:00:00.000Z"))

    # 4 planned (created on/before sprint start): 2 completed, 2 not.
    planned = [
        _issue("Done", "2025-12-28T09:00:00.000+0000"),
        _issue("Closed", "2025-12-29T09:00:00.000+0000"),
        _issue("In Progress", "2025-12-30T09:00:00.000+0000"),
        _issue("To Do", "2025-12-31T09:00:00.000+0000"),
    ]
    # 3 added mid-sprint (created after sprint start): 1 completed, 2 not.
    added = [
        _issue("Done", "2026-01-03T09:00:00.000+0000"),
        _issue("In Progress", "2026-01-04T09:00:00.000+0000"),
        _issue("In Progress", "2026-01-05T09:00:00.000+0000"),
    ]

    calculator = SprintMetricsCalculator(sprint, planned + added)
    metrics = calculator.calculate_metrics()

    scope = metrics["scope"]
    assert scope["planned_total"] == 4
    assert scope["planned_completed"] == 2
    assert scope["planned_not_completed"] == 2
    assert scope["added_total"] == 3
    assert scope["added_completed"] == 1
    assert scope["added_not_completed"] == 2
    assert scope["total_issues"] == 7
    assert scope["total_completed"] == 3

    exec_metrics = metrics["execution_metrics"]
    assert exec_metrics["planned_completion_rate"] == 50  # 2/4
    assert exec_metrics["added_completion_rate"] == 33  # round(1/3 * 100)
    assert exec_metrics["execution_focus_ratio"] == 67  # round(2/3 * 100)
    assert exec_metrics["scope_creep_ratio"] == 43  # round(3/7 * 100)
    assert exec_metrics["throughput"] == 3
    assert exec_metrics["planned_vs_added_throughput_pct"] == 67


def test_calculate_metrics_handles_zero_issues_without_division_errors() -> None:
    sprint = Sprint(raw_sprint(start_date="2026-01-01T00:00:00.000Z"))
    calculator = SprintMetricsCalculator(sprint, [])
    metrics = calculator.calculate_metrics()

    scope = metrics["scope"]
    assert scope["total_issues"] == 0
    assert scope["total_completed"] == 0

    exec_metrics = metrics["execution_metrics"]
    assert exec_metrics["planned_completion_rate"] == 0
    assert exec_metrics["added_completion_rate"] == 0
    assert exec_metrics["execution_focus_ratio"] == 0
    assert exec_metrics["scope_creep_ratio"] == 0
    assert exec_metrics["throughput"] == 0


def test_calculate_metrics_falls_back_to_default_goal_when_sprint_has_none() -> None:
    sprint = Sprint(raw_sprint(goal="", start_date="2026-01-01T00:00:00.000Z"))
    calculator = SprintMetricsCalculator(sprint, [])
    metrics = calculator.calculate_metrics()
    assert metrics["sprint_metadata"]["goal"] == (
        "Development continuation, feature enhancements, and quality improvements"
    )


def test_calculate_metrics_missing_dates_report_as_tbd() -> None:
    sprint = Sprint(raw_sprint(start_date=None, end_date=None))
    calculator = SprintMetricsCalculator(sprint, [])
    metrics = calculator.calculate_metrics()
    assert metrics["sprint_metadata"]["start_date"] == "TBD"
    assert metrics["sprint_metadata"]["end_date"] == "TBD"
