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
    changelog_histories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a raw Jira REST issue dict shaped like the Agile API response.

    ``changelog_histories``, when given, is attached as
    ``changelog.histories`` exactly as Jira's Agile API returns it under
    ``expand=changelog`` — see ``sprint_field_history()`` below for building
    realistic ``field: "Sprint"`` entries.
    """
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

    data: dict[str, Any] = {"key": key, "id": issue_id, "fields": fields}
    if changelog_histories is not None:
        data["changelog"] = {"histories": changelog_histories}

    return data


def sprint_field_history(
    created: str,
    from_sprint_ids: list[int] | None = None,
    to_sprint_ids: list[int] | None = None,
    history_id: str = "10000",
    author: str = "Jane Doe",
) -> dict[str, Any]:
    """Build one changelog `histories[]` entry with a single Sprint-field item.

    Mirrors the real shape returned by Jira's Agile REST API under
    ``expand=changelog``: ``field: "Sprint"``, ``fieldtype: "custom"``, a
    per-instance ``fieldId`` (e.g. ``customfield_10020``), and comma-separated
    sprint IDs/names in ``from``/``fromString``/``to``/``toString``.
    """
    from_ids = from_sprint_ids or []
    to_ids = to_sprint_ids or []
    return {
        "id": history_id,
        "author": {"displayName": author},
        "created": created,
        "items": [
            {
                "field": "Sprint",
                "fieldtype": "custom",
                "fieldId": "customfield_10020",
                "from": ",".join(str(i) for i in from_ids),
                "fromString": ", ".join(f"Sprint {i}" for i in from_ids),
                "to": ",".join(str(i) for i in to_ids),
                "toString": ", ".join(f"Sprint {i}" for i in to_ids),
            }
        ],
    }


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
