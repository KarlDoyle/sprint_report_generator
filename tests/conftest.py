"""Shared test fixtures/helpers: build raw Jira-shaped dicts without a live Jira."""

from __future__ import annotations

from typing import Any


def raw_issue(
    key: str = "PROJ-1",
    issue_id: str = "10001",
    summary: str = "Example issue",
    status: str = "To Do",
    issue_type: str = "Story",
    assignee: str | None = "Jane Doe",
    priority: str | None = "High",
    story_points: float | None = None,
    story_points_field: str = "customfield_10016",
    components: list[str] | None = None,
    labels: list[str] | None = None,
    created: str = "2026-01-01T09:00:00.000+0000",
    updated: str = "2026-01-02T09:00:00.000+0000",
    resolved: str = "",
) -> dict[str, Any]:
    """Build a raw Jira REST issue dict shaped like the Agile API response."""
    fields: dict[str, Any] = {
        "summary": summary,
        "description": None,
        "issuetype": {"name": issue_type},
        "status": {"name": status},
        "assignee": {"displayName": assignee} if assignee else None,
        "priority": {"name": priority} if priority else None,
        "components": [{"name": c} for c in (components or [])],
        "labels": labels or [],
        "created": created,
        "updated": updated,
        "resolutiondate": resolved,
    }
    if story_points is not None:
        fields[story_points_field] = story_points

    return {"key": key, "id": issue_id, "fields": fields}


def raw_sprint(
    sprint_id: int = 42,
    name: str = "Sprint 42",
    goal: str = "Ship the thing",
    state: str = "active",
    board_id: int = 7,
    start_date: str | None = "2026-01-01T00:00:00.000Z",
    end_date: str | None = "2026-01-15T00:00:00.000Z",
) -> dict[str, Any]:
    return {
        "id": sprint_id,
        "name": name,
        "goal": goal,
        "state": state,
        "originBoardId": board_id,
        "startDate": start_date,
        "endDate": end_date,
    }
