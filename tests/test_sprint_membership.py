"""Tests for changelog-based sprint-membership detection (sprint_report_generator.core).

Covers JiraIssue.apply_sprint_membership() — the real fix for the "added
mid-sprint" defect: sprint-membership timing is derived from Jira's own
changelog `field: "Sprint"` history entries, not inferred from the issue's
`created` date.
"""

from __future__ import annotations

from datetime import date

from conftest import raw_issue, raw_sprint, sprint_field_history

from sprint_report_generator.core import JiraClient, JiraIssue, Sprint, SprintMetricsCalculator

SPRINT_ID = 42


def _issue(**kwargs) -> JiraIssue:
    return JiraIssue(raw_issue(**kwargs))


def test_issue_added_at_sprint_start_is_not_mid_sprint() -> None:
    """Added on the sprint's start date itself — planning-time add, not scope creep."""
    issue = _issue(
        created="2025-12-01T09:00:00.000+0000",  # created long before the sprint
        changelog_histories=[
            sprint_field_history(
                created="2026-01-01T00:00:00.000+0000",  # added exactly at sprint start
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            )
        ],
    )
    issue.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))

    assert issue.sprint_history_source == "changelog"
    assert issue.was_added_mid_sprint is False
    assert issue.currently_in_sprint is True
    assert issue.sprint_added_at is not None
    assert issue.sprint_added_at.date() == date(2026, 1, 1)


def test_issue_added_three_days_into_sprint_is_mid_sprint() -> None:
    """The headline case the audit flagged: created weeks ago, pulled in mid-sprint."""
    issue = _issue(
        created="2025-11-01T09:00:00.000+0000",  # created weeks before the sprint even opened
        changelog_histories=[
            sprint_field_history(
                created="2026-01-04T10:30:00.000+0000",  # added 3 days into the sprint
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            )
        ],
    )
    issue.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))

    assert issue.was_added_mid_sprint is True
    assert issue.currently_in_sprint is True
    assert issue.sprint_added_at.date() == date(2026, 1, 4)


def test_created_date_heuristic_would_have_been_wrong_for_this_issue() -> None:
    """Directly demonstrates the defect: created-date heuristic says "planned",
    changelog says "added mid-sprint" — because it was created earlier and only
    pulled into *this* sprint later.
    """
    created = "2025-11-01T09:00:00.000+0000"
    issue_heuristic = _issue(created=created)
    issue_heuristic.set_sprint_timing(date(2026, 1, 1))
    assert issue_heuristic.was_added_mid_sprint is False  # old, wrong answer

    issue_changelog = _issue(
        created=created,
        changelog_histories=[
            sprint_field_history(
                created="2026-01-04T10:30:00.000+0000",
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            )
        ],
    )
    issue_changelog.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))
    assert issue_changelog.was_added_mid_sprint is True  # correct answer


def test_issue_removed_then_re_added_ends_up_currently_in_sprint() -> None:
    issue = _issue(
        created="2025-12-15T09:00:00.000+0000",
        changelog_histories=[
            sprint_field_history(
                created="2026-01-01T00:00:00.000+0000",
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            ),
            sprint_field_history(
                created="2026-01-05T00:00:00.000+0000",
                from_sprint_ids=[SPRINT_ID],
                to_sprint_ids=[],
            ),
            sprint_field_history(
                created="2026-01-07T00:00:00.000+0000",
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            ),
        ],
    )
    issue.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))

    assert issue.currently_in_sprint is True
    assert issue.was_removed_from_sprint is False  # final state is "in", not "removed"
    assert issue.sprint_added_at.date() == date(2026, 1, 1)  # first add, not the re-add
    assert issue.sprint_removed_at.date() == date(2026, 1, 5)  # the removal is still on record
    assert issue.was_added_mid_sprint is False  # first add was at sprint start


def test_issue_added_then_removed_and_never_re_added() -> None:
    issue = _issue(
        created="2025-12-15T09:00:00.000+0000",
        changelog_histories=[
            sprint_field_history(
                created="2026-01-03T00:00:00.000+0000",
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            ),
            sprint_field_history(
                created="2026-01-06T00:00:00.000+0000",
                from_sprint_ids=[SPRINT_ID],
                to_sprint_ids=[],
            ),
        ],
    )
    issue.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))

    assert issue.currently_in_sprint is False
    assert issue.was_removed_from_sprint is True
    assert issue.was_added_mid_sprint is True  # added 2 days in, before being removed


def test_issue_with_no_sprint_changelog_entries_falls_back_without_crashing() -> None:
    """Edge case: no Sprint-field changelog entries at all (aged-out history,
    or an instance that doesn't retain it). Must fall back sensibly, not crash.
    """
    issue = _issue(
        created="2026-01-05T09:00:00.000+0000",
        changelog_histories=[],  # explicitly empty changelog, not missing
    )
    issue.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))

    assert issue.sprint_history_source == "created_date_fallback"
    assert issue.was_added_mid_sprint is True  # falls back to created-date heuristic
    assert issue.currently_in_sprint is True
    assert issue.sprint_added_at is None


def test_issue_with_no_changelog_key_at_all_falls_back_without_crashing() -> None:
    """Even more minimal edge case: `changelog` key entirely absent from raw_data
    (e.g. expand=changelog wasn't honoured by an older Jira instance).
    """
    issue = JiraIssue(raw_issue(created="2026-01-05T09:00:00.000+0000"))
    assert "changelog" not in issue.raw_data

    issue.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))

    assert issue.sprint_history_source == "created_date_fallback"
    assert issue.was_added_mid_sprint is True


def test_created_inside_sprint_with_only_a_removal_event_is_treated_as_planned() -> None:
    """Real Jira quirk: an issue created directly inside a sprint gets no "added"
    changelog entry (nothing to diff against) — only a later removal, if any,
    shows up. With no recorded add, treat it as planned rather than guess.
    """
    issue = _issue(
        created="2026-01-02T09:00:00.000+0000",
        changelog_histories=[
            sprint_field_history(
                created="2026-01-10T00:00:00.000+0000",
                from_sprint_ids=[SPRINT_ID],
                to_sprint_ids=[],
            ),
        ],
    )
    issue.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))

    assert issue.sprint_added_at is None
    assert issue.was_added_mid_sprint is False
    assert issue.currently_in_sprint is False


def test_sprint_field_from_to_ignores_other_sprints_on_the_same_issue() -> None:
    """An issue moved between two other sprints, never touching ours, has no
    membership event for our sprint at all — same as having no Sprint
    changelog entries for it, so it correctly falls back to the created-date
    heuristic rather than claiming changelog-derived certainty it doesn't have.
    """
    issue = _issue(
        created="2025-12-15T09:00:00.000+0000",
        changelog_histories=[
            sprint_field_history(
                created="2026-01-02T00:00:00.000+0000",
                from_sprint_ids=[7],
                to_sprint_ids=[8],
            ),
        ],
    )
    issue.apply_sprint_membership(SPRINT_ID, date(2026, 1, 1))

    assert issue.sprint_history_source == "created_date_fallback"
    assert issue.sprint_added_at is None
    assert issue.was_added_mid_sprint is False


def test_metrics_calculator_uses_changelog_membership_not_created_date() -> None:
    """End-to-end: SprintMetricsCalculator now calls apply_sprint_membership(),
    so an issue created weeks ago but added 3 days into the sprint counts as
    "added" (scope creep), not "planned" — fixing the two headline metrics.
    """
    sprint = Sprint(raw_sprint(sprint_id=SPRINT_ID, start_date="2026-01-01T00:00:00.000Z"))

    planned = _issue(
        key="P-1",
        status="Done",
        created="2025-12-01T09:00:00.000+0000",
        changelog_histories=[
            sprint_field_history(
                created="2026-01-01T00:00:00.000+0000",
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            )
        ],
    )
    # Created long before the sprint, but the changelog shows it was actually
    # pulled in 3 days into the sprint — must be counted as "added", not "planned".
    added_but_old_created_date = _issue(
        key="P-2",
        status="In Progress",
        created="2025-10-01T09:00:00.000+0000",
        changelog_histories=[
            sprint_field_history(
                created="2026-01-04T00:00:00.000+0000",
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            )
        ],
    )

    calculator = SprintMetricsCalculator(sprint, [planned, added_but_old_created_date])
    metrics = calculator.calculate_metrics()

    assert metrics["scope"]["planned_total"] == 1
    assert metrics["scope"]["added_total"] == 1
    assert metrics["execution_metrics"]["scope_creep_ratio"] == 50


def test_scope_creep_ratio_counts_removed_then_gone_issues() -> None:
    """Per the audit: an issue added mid-sprint and later removed must still
    count toward scope creep — it entered the sprint's scope even though it
    didn't stay. This models the merged issue list JiraClient.get_sprint_issues()
    produces (current members + removed-but-was-in-scope issues).
    """
    sprint = Sprint(raw_sprint(sprint_id=SPRINT_ID, start_date="2026-01-01T00:00:00.000Z"))

    planned = _issue(
        key="P-1",
        status="Done",
        created="2025-12-01T09:00:00.000+0000",
        changelog_histories=[
            sprint_field_history(
                created="2026-01-01T00:00:00.000+0000",
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            )
        ],
    )
    added_and_removed = _issue(
        key="P-2",
        status="To Do",
        created="2026-01-03T09:00:00.000+0000",
        changelog_histories=[
            sprint_field_history(
                created="2026-01-03T00:00:00.000+0000",
                from_sprint_ids=[],
                to_sprint_ids=[SPRINT_ID],
            ),
            sprint_field_history(
                created="2026-01-06T00:00:00.000+0000",
                from_sprint_ids=[SPRINT_ID],
                to_sprint_ids=[],
            ),
        ],
    )

    calculator = SprintMetricsCalculator(sprint, [planned, added_and_removed])
    metrics = calculator.calculate_metrics()

    scope = metrics["scope"]
    assert scope["total_issues"] == 2
    assert scope["added_total"] == 1
    assert scope["added_removed"] == 1
    assert metrics["execution_metrics"]["scope_creep_ratio"] == 50


# =============================================================================
# JiraClient — removed-from-sprint issue lookup
# =============================================================================


class _FakeHttp:
    """Stands in for SimpleHttpClient — returns canned responses per endpoint."""

    def __init__(self, responses: dict[str, list[dict]]):
        # endpoint -> list of page responses, popped in order
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        self.calls.append((endpoint, params or {}))
        pages = self._responses.get(endpoint)
        if not pages:
            return {"issues": []}
        return pages.pop(0)


def test_get_sprint_issues_merges_current_and_removed_issues() -> None:
    current_response = {
        "issues": [raw_issue(key="P-1"), raw_issue(key="P-2")],
    }
    removed_response = {
        "issues": [raw_issue(key="P-3")],
    }
    fake_http = _FakeHttp(
        {
            f"/rest/agile/1.0/sprint/{SPRINT_ID}/issue": [current_response],
            "/rest/api/3/search": [removed_response],
        }
    )

    client = JiraClient.__new__(JiraClient)
    client.http = fake_http

    issues = client.get_sprint_issues(SPRINT_ID)

    keys = {i.key for i in issues}
    assert keys == {"P-1", "P-2", "P-3"}

    removed_issue = next(i for i in issues if i.key == "P-3")
    assert removed_issue._removed_via_jql is True

    # Verify the JQL used is the documented "was in, not currently in" idiom.
    search_call = next(c for c in fake_http.calls if c[0] == "/rest/api/3/search")
    assert search_call[1]["jql"] == f"sprint was {SPRINT_ID} AND sprint != {SPRINT_ID}"


def test_get_sprint_issues_excludes_current_members_from_removed_lookup() -> None:
    """If the removed-issue JQL somehow re-returns a current member, it must
    not be double-counted.
    """
    current_response = {"issues": [raw_issue(key="P-1")]}
    removed_response = {"issues": [raw_issue(key="P-1"), raw_issue(key="P-2")]}
    fake_http = _FakeHttp(
        {
            f"/rest/agile/1.0/sprint/{SPRINT_ID}/issue": [current_response],
            "/rest/api/3/search": [removed_response],
        }
    )

    client = JiraClient.__new__(JiraClient)
    client.http = fake_http

    issues = client.get_sprint_issues(SPRINT_ID)
    keys = [i.key for i in issues]
    assert keys.count("P-1") == 1
    assert "P-2" in keys


def test_get_sprint_issues_degrades_gracefully_when_removed_lookup_unsupported() -> None:
    """If the JQL sprint-history operators aren't supported (or any other error
    occurs), the report must still generate with current members only.
    """

    class _RaisingHttp:
        def get(self, endpoint, params=None):
            if endpoint == "/rest/api/3/search":
                raise Exception("HTTP 400: Bad Request - operator 'was' is not supported")
            return {"issues": [raw_issue(key="P-1")]}

    client = JiraClient.__new__(JiraClient)
    client.http = _RaisingHttp()

    issues = client.get_sprint_issues(SPRINT_ID)
    assert [i.key for i in issues] == ["P-1"]


def test_scope_creep_lookup_failure_is_surfaced_not_indistinguishable_from_zero() -> None:
    """Regression test for the stress-test finding: a failed 'removed from
    sprint' JQL lookup used to degrade to an empty list with no trace anywhere
    in the report — a sprint with a genuinely clean 0-removed result looked
    byte-for-byte identical to one where the lookup silently failed. Both
    JiraClient (the "did the lookup fail" signal) and SprintMetricsCalculator
    (the report-facing field) must now make that distinguishable.
    """

    class _RaisingHttp:
        def get(self, endpoint, params=None):
            if endpoint == "/rest/api/3/search":
                raise Exception("HTTP 400: Bad Request - operator 'was' is not supported")
            return {"issues": [raw_issue(key="P-1")]}

    client = JiraClient.__new__(JiraClient)
    client.http = _RaisingHttp()

    issues = client.get_sprint_issues(SPRINT_ID)

    # The client itself now knows the lookup degraded...
    assert client.scope_creep_lookup_degraded is True

    # ...and that must reach the report output, not just live on the client.
    sprint = Sprint(raw_sprint(sprint_id=SPRINT_ID, start_date="2026-01-01T00:00:00.000Z"))
    metrics = SprintMetricsCalculator(
        sprint, issues, scope_creep_lookup_degraded=client.scope_creep_lookup_degraded
    ).calculate_metrics()

    assert metrics["scope"]["planned_removed"] == 0  # looks like a clean zero...
    assert metrics["scope"]["scope_creep_lookup_degraded"] is True  # ...but is flagged as unknown


def test_scope_creep_lookup_success_reports_a_genuine_zero_as_not_degraded() -> None:
    """The flip side: when the lookup actually runs and finds nothing removed,
    that must still read as a confident zero, not a false alarm.
    """
    fake_http = _FakeHttp(
        {
            f"/rest/agile/1.0/sprint/{SPRINT_ID}/issue": [{"issues": [raw_issue(key="P-1")]}],
            "/rest/api/3/search": [{"issues": []}],
        }
    )
    client = JiraClient.__new__(JiraClient)
    client.http = fake_http

    issues = client.get_sprint_issues(SPRINT_ID)
    assert client.scope_creep_lookup_degraded is False

    sprint = Sprint(raw_sprint(sprint_id=SPRINT_ID, start_date="2026-01-01T00:00:00.000Z"))
    metrics = SprintMetricsCalculator(
        sprint, issues, scope_creep_lookup_degraded=client.scope_creep_lookup_degraded
    ).calculate_metrics()
    assert metrics["scope"]["scope_creep_lookup_degraded"] is False


def test_get_sprint_issues_include_removed_false_skips_the_lookup() -> None:
    fake_http = _FakeHttp(
        {
            f"/rest/agile/1.0/sprint/{SPRINT_ID}/issue": [{"issues": [raw_issue(key="P-1")]}],
        }
    )
    client = JiraClient.__new__(JiraClient)
    client.http = fake_http

    issues = client.get_sprint_issues(SPRINT_ID, include_removed=False)

    assert [i.key for i in issues] == ["P-1"]
    assert all(c[0] != "/rest/api/3/search" for c in fake_http.calls)
