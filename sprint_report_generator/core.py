"""Core logic: Jira HTTP client, data models, metrics, and report generation.

No external dependencies — uses only ``urllib`` from the standard library to
talk to the Jira Cloud REST/Agile APIs.

Environment Variables (read by the CLI, not this module):
    JIRA_BASE_URL - Jira instance URL (e.g., https://company.atlassian.net)
    JIRA_USERNAME - Jira email/username
    JIRA_API_TOKEN - Jira API token
    JIRA_PROJECT_KEY - Project key (optional, will auto-detect)
    JIRA_BOARD_ID - Board ID (optional, will auto-detect)
    JIRA_SPRINT_ID - Default sprint ID (optional)
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

# =============================================================================
# HTTP CLIENT (No external dependencies)
# =============================================================================


class SimpleHttpClient:
    def __init__(self, base_url: str, username: str, api_token: str):
        parsed_url = urllib.parse.urlsplit(base_url)
        if (
            parsed_url.scheme.lower() != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "JIRA_BASE_URL must be a trusted HTTPS Jira URL without embedded "
                "credentials, query, or fragment"
            )
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.api_token = api_token

        # Create auth header
        credentials = f"{username}:{api_token}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        self.auth_header = f"Basic {encoded_credentials}"

    def get(self, endpoint: str, params: dict | None = None) -> dict[str, Any]:
        """Make GET request to Jira API"""
        url = f"{self.base_url}{endpoint}"

        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"

        request = urllib.request.Request(url)
        request.add_header("Authorization", self.auth_header)
        request.add_header("Accept", "application/json")
        request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read().decode("utf-8")
                return json.loads(data)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else "No error details"
            raise Exception(f"HTTP {e.code}: {e.reason} - {error_body}") from e
        except Exception as e:
            raise Exception(f"Request failed: {str(e)}") from e


# =============================================================================
# DATA MODELS
# =============================================================================


class JiraIssue:
    def __init__(self, data: dict[str, Any]):
        self.raw_data = data
        self.key = data.get("key", "")
        self.id = data.get("id", "")

        fields = data.get("fields", {})
        self.summary = fields.get("summary", "")
        self.description = fields.get("description", "")

        # Issue type
        issue_type = fields.get("issuetype", {})
        self.issue_type = issue_type.get("name", "Unknown") if issue_type else "Unknown"

        # Status
        status = fields.get("status", {})
        self.status = status.get("name", "Unknown") if status else "Unknown"

        # Assignee
        assignee = fields.get("assignee")
        self.assignee = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"

        # Story points (try common custom fields)
        self.story_points = None
        for field in ["customfield_10002", "customfield_10016", "customfield_10004"]:
            if fields.get(field):
                try:
                    self.story_points = float(fields[field])
                    break
                except (ValueError, TypeError):
                    continue
        if self.story_points is None:
            self.story_points = 0

        # Priority
        priority = fields.get("priority", {})
        self.priority = priority.get("name", "Medium") if priority else "Medium"

        # Components
        components = fields.get("components", [])
        self.components = [comp.get("name", "") for comp in components] if components else []

        # Labels
        self.labels = fields.get("labels", [])

        # Dates
        self.created = fields.get("created", "")
        self.updated = fields.get("updated", "")
        self.resolved = fields.get("resolutiondate", "")

        # Sprint information — filled in by apply_sprint_membership() (preferred,
        # changelog-based) or set_sprint_timing() (fallback, created-date heuristic).
        self.added_to_sprint_date = None
        self.was_added_mid_sprint = False
        self.sprint_added_at: datetime | None = None
        self.sprint_removed_at: datetime | None = None
        self.currently_in_sprint = True
        self.was_removed_from_sprint = False
        self.sprint_history_source: str | None = None
        # Set by JiraClient when this issue was found via the "removed from sprint"
        # JQL lookup rather than the sprint's current-members endpoint — a hint for
        # the changelog-fallback path, which otherwise has no way to know the issue
        # isn't a current member.
        self._removed_via_jql = False

    def set_sprint_timing(self, sprint_start_date: date | None):
        """Determine if issue was added mid-sprint (created-date heuristic).

        This is the pre-changelog fallback: it infers "added mid-sprint" from
        the issue's *creation* date vs. the sprint's start date, which is wrong
        whenever an issue was created earlier and only pulled into the sprint
        later (or vice versa). Kept as the fallback used by
        apply_sprint_membership() when an issue has no Sprint-field changelog
        entries at all, and as a standalone method for direct use/testing.
        """
        if not sprint_start_date or not self.created:
            return

        try:
            created_date = datetime.fromisoformat(self.created.replace("Z", "+00:00")).date()
            self.was_added_mid_sprint = created_date > sprint_start_date
        except (ValueError, AttributeError):
            pass

    def apply_sprint_membership(
        self, sprint_id: int, sprint_start: date | None, sprint_end: date | None = None
    ) -> None:
        """Determine sprint add/remove timing from the issue's changelog.

        Jira's Agile REST API (``expand=changelog``) records sprint membership
        changes as changelog history items with ``field: "Sprint"``. The
        ``from``/``to`` values are comma-separated *sprint IDs* (an issue can be
        linked to more than one sprint over its life); ``fromString``/
        ``toString`` are the comma-separated sprint *names* and are NOT safe to
        parse — sprint names may themselves contain commas, so we only ever
        parse the ID lists. ``fieldId`` for the Sprint field varies per Jira
        instance (e.g. ``customfield_10020``), so we match on ``field == "Sprint"``
        only, never a hardcoded fieldId.

        Diffing ``to`` against ``from`` for our sprint_id at each history entry
        gives a chronological add/remove timeline for that specific sprint,
        which is what actually determines "added mid-sprint" — not the issue's
        creation date.

        Known Jira quirk: if an issue was *created directly inside* the sprint,
        there is no changelog entry for the initial add (there was nothing to
        diff against) — only a later removal, if any, will appear. That case is
        handled below by treating "removal with no prior recorded add" as
        planned (not mid-sprint-added), since there is no evidence it was
        pulled in later.

        Falls back to the created-date heuristic (set_sprint_timing) when the
        issue has no Sprint-field changelog entries at all — e.g. changelog
        history has aged out, or the instance doesn't retain it. This is a
        deliberate best-effort fallback, not a silent crash.
        """
        events: list[tuple[datetime, bool]] = []

        histories = (self.raw_data.get("changelog") or {}).get("histories") or []
        for history in histories:
            created_raw = history.get("created")
            if not created_raw:
                continue
            try:
                ts = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            for item in history.get("items") or []:
                if item.get("field") != "Sprint":
                    continue
                from_ids = _parse_sprint_id_set(item.get("from"))
                to_ids = _parse_sprint_id_set(item.get("to"))
                was_in = sprint_id in from_ids
                now_in = sprint_id in to_ids
                if was_in == now_in:
                    continue  # this Sprint-field change didn't touch our sprint
                events.append((ts, now_in))

        if not events:
            self.sprint_history_source = "created_date_fallback"
            self.set_sprint_timing(sprint_start)
            self.currently_in_sprint = not self._removed_via_jql
            self.sprint_added_at = None
            self.sprint_removed_at = None
            self.was_removed_from_sprint = self._removed_via_jql
            return

        self.sprint_history_source = "changelog"
        events.sort(key=lambda e: e[0])

        added_at: datetime | None = None
        removed_at: datetime | None = None
        currently_in = False
        for ts, now_in in events:
            currently_in = now_in
            if now_in and added_at is None:
                added_at = ts
            if not now_in:
                removed_at = ts

        self.sprint_added_at = added_at
        self.sprint_removed_at = removed_at
        self.currently_in_sprint = currently_in
        self.was_removed_from_sprint = added_at is not None and not currently_in

        if added_at is not None and sprint_start is not None:
            self.was_added_mid_sprint = added_at.date() > sprint_start
        else:
            # Either never added mid-sprint start info to compare against, or the
            # only recorded events are removals with no prior add on record (the
            # "created inside the sprint" quirk above) — treat as planned rather
            # than guess.
            self.was_added_mid_sprint = False


def _parse_sprint_id_set(value: Any) -> set[int]:
    """Parse a changelog Sprint item's comma-separated ``from``/``to`` ID string."""
    if not value:
        return set()
    ids: set[int] = set()
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


class Sprint:
    def __init__(self, data: dict[str, Any]):
        self.id = data.get("id", 0)
        self.name = data.get("name", "")
        self.goal = data.get("goal", "")
        self.state = data.get("state", "UNKNOWN")
        self.board_id = data.get("originBoardId", 0)

        # Parse dates
        self.start_date = self._parse_date(data.get("startDate"))
        self.end_date = self._parse_date(data.get("endDate"))

    def _parse_date(self, date_str: str | None) -> date | None:
        if not date_str:
            return None
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.date()
        except (ValueError, AttributeError):
            return None


# =============================================================================
# JIRA CLIENT
# =============================================================================


class JiraClient:
    def __init__(self, base_url: str, username: str, api_token: str):
        self.http = SimpleHttpClient(base_url, username, api_token)
        # Set by get_sprint_issues()/_get_removed_sprint_issues(): True when the
        # scope-creep "removed from sprint" JQL lookup failed (e.g. unsupported
        # JQL operator on this Jira instance) and was silently degraded to an
        # empty result. Read by SprintReportGenerator so the report can say "we
        # couldn't check" instead of looking identical to a genuine zero.
        self.scope_creep_lookup_degraded = False

    def get_sprint(self, sprint_id: int) -> Sprint:
        """Get sprint information"""
        data = self.http.get(f"/rest/agile/1.0/sprint/{sprint_id}")
        return Sprint(data)

    def get_sprint_issues(self, sprint_id: int, *, include_removed: bool = True) -> list[JiraIssue]:
        """Get all issues relevant to a sprint, with full changelog for membership analysis.

        By default this includes both current sprint members AND issues that
        were added to the sprint at some point and later removed — the latter
        would otherwise vanish from scope-creep accounting entirely, since the
        Agile "current members" endpoint (by definition) no longer lists them.
        Pass ``include_removed=False`` to fetch only current members (faster,
        one endpoint) when the removed-issue picture doesn't matter.
        """
        # Reset per call — a single client instance is reused across sprints in
        # generate_multi_team_report(), and tests construct JiraClient via
        # __new__() without running __init__, so this can't rely solely on the
        # constructor having set it.
        self.scope_creep_lookup_degraded = False

        issues = self._get_current_sprint_issues(sprint_id)
        if include_removed:
            current_keys = {issue.key for issue in issues}
            issues.extend(self._get_removed_sprint_issues(sprint_id, current_keys))
        return issues

    def _get_current_sprint_issues(self, sprint_id: int) -> list[JiraIssue]:
        """Get issues currently in the sprint (the pre-existing behavior)."""
        issues = []
        start_at = 0
        max_results = 100

        while True:
            params = {
                "startAt": start_at,
                "maxResults": max_results,
                "expand": "changelog",
            }

            data = self.http.get(f"/rest/agile/1.0/sprint/{sprint_id}/issue", params)
            batch_issues = [JiraIssue(issue) for issue in data.get("issues", [])]
            issues.extend(batch_issues)

            if len(batch_issues) < max_results:
                break

            start_at += max_results

        return issues

    def _get_removed_sprint_issues(self, sprint_id: int, exclude_keys: set[str]) -> list[JiraIssue]:
        """Issues that were once in this sprint but have since been removed.

        ``/rest/agile/1.0/sprint/{id}/issue`` only ever returns CURRENT
        members, so an issue added mid-sprint and later pulled back out never
        appears there — silently undercounting scope creep. JQL's sprint
        history operator (``sprint was <id>``) matches issues that have EVER
        been in a sprint regardless of current membership; combined with
        ``sprint != <id>`` (not a *current* member) that narrows to exactly
        the "added, then removed" set. This is a documented JQL idiom, not the
        Agile API, so it goes through the classic search endpoint.

        Best-effort: if the JQL sprint-history operators aren't supported on
        this instance/version, the request raises and we degrade to an empty
        list (report still generates, just without removed-issue detail)
        rather than fail the whole report.
        """
        issues: list[JiraIssue] = []
        start_at = 0
        max_results = 100
        jql = f"sprint was {sprint_id} AND sprint != {sprint_id}"

        try:
            while True:
                params = {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": max_results,
                    "expand": "changelog",
                    "fields": "*all",
                }
                data = self.http.get("/rest/api/3/search", params)
                page = data.get("issues", [])
                for raw in page:
                    if raw.get("key") in exclude_keys:
                        continue
                    issue = JiraIssue(raw)
                    issue._removed_via_jql = True
                    issues.append(issue)

                if len(page) < max_results:
                    break
                start_at += max_results
        except Exception:
            # Best-effort degrade to "no removed issues found" rather than
            # failing the whole report — but record that this was a failure,
            # not a genuine zero, so the caller can surface it (see
            # scope_creep_lookup_degraded on the report's "scope" dict).
            self.scope_creep_lookup_degraded = True
            return []

        return issues


# =============================================================================
# METRICS CALCULATOR
# =============================================================================


class SprintMetricsCalculator:
    def __init__(
        self,
        sprint: Sprint,
        issues: list[JiraIssue],
        *,
        scope_creep_lookup_degraded: bool = False,
    ):
        self.sprint = sprint
        self.issues = issues
        # True when the caller's "removed from sprint" JQL lookup failed and
        # was silently degraded to an empty list — surfaced in the "scope"
        # output below so a reader can tell "0 removed" from "couldn't check".
        self.scope_creep_lookup_degraded = scope_creep_lookup_degraded

        # Determine real sprint-membership timing from each issue's changelog
        # (falls back to the created-date heuristic per-issue when an issue has
        # no Sprint-field changelog entries — see JiraIssue.apply_sprint_membership).
        for issue in self.issues:
            issue.apply_sprint_membership(sprint.id, sprint.start_date, sprint.end_date)

        # Categorize issues
        self.planned_issues = [i for i in issues if not i.was_added_mid_sprint]
        self.added_issues = [i for i in issues if i.was_added_mid_sprint]

        # Completion statuses
        self.completion_statuses = [
            "Done",
            "QA Passed",
            "Resolved",
            "Closed",
            "Completed",
            "Verified",
            "Accepted",
        ]

    def calculate_metrics(self) -> dict[str, Any]:
        """Calculate all sprint metrics"""

        # Planned issues metrics
        planned_total = len(self.planned_issues)
        planned_completed = len(
            [i for i in self.planned_issues if i.status in self.completion_statuses]
        )
        planned_not_completed = planned_total - planned_completed
        planned_removed = len([i for i in self.planned_issues if not i.currently_in_sprint])

        # Added mid-sprint metrics
        added_total = len(self.added_issues)
        added_completed = len(
            [i for i in self.added_issues if i.status in self.completion_statuses]
        )
        added_not_completed = added_total - added_completed
        added_removed = len([i for i in self.added_issues if not i.currently_in_sprint])

        # Completed Outside Sprint: closed but no longer a sprint member (real
        # changelog-derived removal history, not the placeholder it used to be).
        completed_outside_sprint_planned = len(
            [
                i
                for i in self.planned_issues
                if not i.currently_in_sprint and i.status in self.completion_statuses
            ]
        )
        completed_outside_sprint_added = len(
            [
                i
                for i in self.added_issues
                if not i.currently_in_sprint and i.status in self.completion_statuses
            ]
        )
        completed_outside_sprint_total = (
            completed_outside_sprint_planned + completed_outside_sprint_added
        )

        # Totals — includes issues later removed from the sprint (self.issues may
        # contain "added then removed" issues fetched via the removed-issue JQL
        # lookup), so scope creep is counted against everything that ever entered
        # the sprint's scope, not just what's still there at report time.
        total_issues = planned_total + added_total
        total_completed = planned_completed + added_completed

        # Calculate percentages according to user definitions
        # Planned Completion Rate: % of completed planned issues from total planned issues
        planned_completion_rate = (
            (planned_completed / planned_total * 100) if planned_total > 0 else 0
        )

        # Added Completion Rate: % of completed added mid-sprint issues from total added
        # mid-sprint issues
        added_completion_rate = (added_completed / added_total * 100) if added_total > 0 else 0

        # Execution Focus Ratio: % (Planned completed / Total completed)
        execution_focus_ratio = (
            (planned_completed / total_completed * 100) if total_completed > 0 else 0
        )

        # Scope Creep Ratio: % (total Added Mid-Sprint Issues / Total Issues)
        scope_creep_ratio = (added_total / total_issues * 100) if total_issues > 0 else 0

        # Planned vs Added Throughput %: % (Planned completed / Total completed) - same as
        # Execution Focus Ratio
        planned_vs_added_throughput = execution_focus_ratio

        return {
            "sprint_metadata": {
                "sprint_id": self.sprint.id,
                "name": self.sprint.name,
                "goal": self.sprint.goal
                if self.sprint.goal
                else "Development continuation, feature enhancements, and quality improvements",
                "start_date": self.sprint.start_date.strftime("%Y-%m-%d")
                if self.sprint.start_date
                else "TBD",
                "end_date": self.sprint.end_date.strftime("%Y-%m-%d")
                if self.sprint.end_date
                else "TBD",
                "state": self.sprint.state,
                "board_id": self.sprint.board_id,
            },
            "scope": {
                "planned_total": planned_total,
                "planned_completed": planned_completed,
                "planned_not_completed": planned_not_completed,
                "planned_removed": planned_removed,
                "added_total": added_total,
                "added_completed": added_completed,
                "added_not_completed": added_not_completed,
                "added_removed": added_removed,
                "completed_outside_sprint_total": completed_outside_sprint_total,
                "completed_outside_sprint_planned": completed_outside_sprint_planned,
                "completed_outside_sprint_added": completed_outside_sprint_added,
                "total_issues": total_issues,
                "total_completed": total_completed,
                "scope_creep_lookup_degraded": self.scope_creep_lookup_degraded,
            },
            "execution_metrics": {
                "planned_completion_rate": round(planned_completion_rate),
                "added_completion_rate": round(added_completion_rate),
                "execution_focus_ratio": round(execution_focus_ratio),
                "scope_creep_ratio": round(scope_creep_ratio),
                "throughput": total_completed,
                "planned_vs_added_throughput_pct": round(planned_vs_added_throughput),
            },
        }


# =============================================================================
# MULTI-TEAM ROLLUP
# =============================================================================


class MultiTeamRollup:
    """Aggregates per-team sprint metrics into one cross-team observation view.

    Built for a TPM/director watching several teams at once rather than a
    single team's sprint — informational and comparative, not a performance
    verdict on any one team. Takes metrics already computed by
    SprintMetricsCalculator.calculate_metrics() for each team; it makes no
    Jira calls of its own.
    """

    #: How far above the cross-team average scope-creep ratio (percentage
    #: points) a team has to be before it's flagged for a look — not a pass/fail
    #: threshold, just where the dashboard draws attention.
    SCOPE_CREEP_FLAG_MARGIN = 15

    def __init__(self, team_reports: list[dict[str, Any]]):
        """``team_reports``: list of {"team": str, "metrics": <calculate_metrics() dict>}."""
        self.team_reports = team_reports

    def aggregate(self) -> dict[str, Any]:
        totals = {
            "planned_total": 0,
            "planned_completed": 0,
            "planned_removed": 0,
            "added_total": 0,
            "added_completed": 0,
            "added_removed": 0,
            "completed_outside_sprint_total": 0,
            "total_issues": 0,
            "total_completed": 0,
        }
        per_team: list[dict[str, Any]] = []

        for team in self.team_reports:
            metrics = team["metrics"]
            scope = metrics["scope"]
            for key in totals:
                totals[key] += scope.get(key, 0)

            per_team.append(
                {
                    "team": team["team"],
                    "sprint_name": metrics["sprint_metadata"]["name"],
                    "sprint_state": metrics["sprint_metadata"]["state"],
                    "total_issues": scope["total_issues"],
                    "total_completed": scope["total_completed"],
                    "scope_creep_ratio": metrics["execution_metrics"]["scope_creep_ratio"],
                    "execution_focus_ratio": metrics["execution_metrics"]["execution_focus_ratio"],
                    "scope_creep_lookup_degraded": scope.get("scope_creep_lookup_degraded", False),
                }
            )

        execution_focus_ratio = (
            (totals["planned_completed"] / totals["total_completed"] * 100)
            if totals["total_completed"] > 0
            else 0
        )
        scope_creep_ratio = (
            (totals["added_total"] / totals["total_issues"] * 100)
            if totals["total_issues"] > 0
            else 0
        )
        scope_creep_ratio_rounded = round(scope_creep_ratio)

        # Observational flag only: teams notably above the cross-team average —
        # worth a look, not a ranking or a verdict on any one team.
        teams_above_average_scope_creep = [
            t["team"]
            for t in per_team
            if t["scope_creep_ratio"] > scope_creep_ratio_rounded + self.SCOPE_CREEP_FLAG_MARGIN
        ]

        return {
            "team_count": len(per_team),
            "totals": totals,
            "execution_focus_ratio": round(execution_focus_ratio),
            "scope_creep_ratio": scope_creep_ratio_rounded,
            "per_team": per_team,
            "teams_above_average_scope_creep": teams_above_average_scope_creep,
            "any_team_scope_creep_lookup_degraded": any(
                t["scope_creep_lookup_degraded"] for t in per_team
            ),
        }


# =============================================================================
# REPORT GENERATOR
# =============================================================================


class SprintReportGenerator:
    def __init__(self, jira_client: JiraClient):
        self.jira = jira_client

    def generate_report(self, sprint_id: int, output_dir: Path) -> dict[str, Any]:
        """Generate complete sprint report"""
        print(f"🚀 Generating sprint report for Sprint {sprint_id}...")

        # Fetch data
        print("📊 Fetching sprint data...")
        sprint = self.jira.get_sprint(sprint_id)
        print(f"   Sprint: {sprint.name}")

        print("📝 Fetching sprint issues...")
        issues = self.jira.get_sprint_issues(sprint_id)
        print(f"   Found {len(issues)} issues")

        # Calculate metrics
        print("📈 Calculating metrics...")
        calculator = SprintMetricsCalculator(
            sprint,
            issues,
            scope_creep_lookup_degraded=getattr(self.jira, "scope_creep_lookup_degraded", False),
        )
        metrics = calculator.calculate_metrics()

        # Create output directory
        sprint_dir = self._create_output_directory(sprint, output_dir)

        # Generate files
        print("📄 Generating sprint summary...")
        self._create_sprint_summary(sprint_dir, metrics)

        print("📋 Generating issues JSON...")
        self._create_issues_json(sprint_dir, issues)

        print("📖 Generating README...")
        self._create_readme(sprint_dir, sprint)

        print(f"✅ Sprint report completed! Files saved to: {sprint_dir}")

        return {
            "sprint_id": sprint_id,
            "sprint_name": sprint.name,
            "output_directory": str(sprint_dir),
            "total_issues": len(issues),
            "completed_issues": metrics["scope"]["total_completed"],
        }

    def generate_multi_team_report(
        self, team_sprint_ids: dict[str, int], output_dir: Path
    ) -> dict[str, Any]:
        """Generate one report per team plus a cross-team rollup.

        ``team_sprint_ids`` maps a team/board label (e.g. "Payments",
        "Platform") to the sprint ID currently active for that team. Built
        for a TPM or director observing several teams' sprints at once — each
        team's own report is still written in full, plus one
        ``rollup_summary.md`` giving the cross-team, informational view.
        """
        team_results: list[dict[str, Any]] = []

        for team_label, sprint_id in team_sprint_ids.items():
            print(f"--- Team: {team_label} (sprint {sprint_id}) ---")
            sprint = self.jira.get_sprint(sprint_id)
            issues = self.jira.get_sprint_issues(sprint_id)
            calculator = SprintMetricsCalculator(
                sprint,
                issues,
                scope_creep_lookup_degraded=getattr(
                    self.jira, "scope_creep_lookup_degraded", False
                ),
            )
            metrics = calculator.calculate_metrics()

            sprint_dir = self._create_output_directory(sprint, output_dir)
            self._create_sprint_summary(sprint_dir, metrics)
            self._create_issues_json(sprint_dir, issues)
            self._create_readme(sprint_dir, sprint)

            team_results.append(
                {
                    "team": team_label,
                    "sprint_id": sprint_id,
                    "metrics": metrics,
                    "output_directory": str(sprint_dir),
                }
            )

        rollup = MultiTeamRollup(team_results).aggregate()
        self._create_rollup_summary(output_dir, rollup)

        return {
            "teams": team_results,
            "rollup": rollup,
            "output_directory": str(output_dir),
        }

    def _create_rollup_summary(self, output_dir: Path, rollup: dict[str, Any]) -> Path:
        """Create rollup_summary.md — the cross-team, observational view."""
        lines = [
            "Multi-Team Sprint Rollup",
            "",
            "For observation and awareness across teams — not a performance verdict on "
            "any single team. Use it to see where attention might be worth spending, "
            "then look at that team's own sprint_summary.md for the detail.",
            "",
            f"Teams included: {rollup['team_count']}",
            "",
            "Cross-Team Totals",
            f"Total Issues: {rollup['totals']['total_issues']}",
            f"Total Completed: {rollup['totals']['total_completed']}",
            f"Planned Removed: {rollup['totals']['planned_removed']}",
            f"Added Mid-Sprint Removed: {rollup['totals']['added_removed']}",
            f"Completed Outside Sprint: {rollup['totals']['completed_outside_sprint_total']}",
            f"Cross-Team Execution Focus Ratio: {rollup['execution_focus_ratio']}%",
            f"Cross-Team Scope Creep Ratio: {rollup['scope_creep_ratio']}%",
            "",
            "Per-Team Snapshot",
        ]

        for team in rollup["per_team"]:
            lines.append(
                f"- {team['team']} ({team['sprint_name']}, {team['sprint_state']}): "
                f"{team['total_completed']}/{team['total_issues']} completed, "
                f"execution focus {team['execution_focus_ratio']}%, "
                f"scope creep {team['scope_creep_ratio']}%"
            )

        lines.append("")
        if rollup["teams_above_average_scope_creep"]:
            flagged = ", ".join(rollup["teams_above_average_scope_creep"])
            lines.append(
                f"Worth a look: {flagged} — scope creep notably above the cross-team "
                "average this sprint. Informational flag, not a conclusion; sprint "
                "context (interrupt-driven work, a genuine priority shift) may fully "
                "explain it."
            )
        else:
            lines.append("No team is notably above the cross-team scope-creep average.")

        content = "\n".join(lines) + "\n"

        with open(output_dir / "rollup_summary.md", "w", encoding="utf-8") as f:
            f.write(content)

        return output_dir / "rollup_summary.md"

    def _create_output_directory(self, sprint: Sprint, base_dir: Path) -> Path:
        """Create output directory"""
        # Simple directory name
        sanitized_name = re.sub(r'[<>:"/\\|?*]', "-", sprint.name)
        sanitized_name = re.sub(r"[-\s]+", "-", sanitized_name).strip("-")

        date_str = (
            sprint.start_date.strftime("%Y-%m-%d")
            if sprint.start_date
            else datetime.now().strftime("%Y-%m-%d")
        )

        dir_name = f"sprint-{sprint.id}-{date_str}-{sanitized_name}"
        sprint_dir = base_dir / dir_name
        sprint_dir.mkdir(parents=True, exist_ok=True)

        return sprint_dir

    def _create_sprint_summary(self, output_dir: Path, metrics: dict[str, Any]):
        """Create sprint_summary.md file"""
        meta = metrics["sprint_metadata"]
        scope = metrics["scope"]
        exec_metrics = metrics["execution_metrics"]

        scope_creep_lookup_note = (
            "YES — the 'removed from sprint' JQL lookup failed; Removed/Planned "
            "Removed above may undercount, not a confirmed zero"
            if scope["scope_creep_lookup_degraded"]
            else "No"
        )

        content = f"""Sprint {meta["sprint_id"]} Summary - {meta["name"]}

For observation and awareness, not a performance verdict — sprint-membership
timing below comes from Jira's own changelog (when an issue was actually
added to or removed from this sprint), not a guess from its creation date.

Sprint Metadata
Sprint ID: {meta["sprint_id"]}
Name: {meta["name"]}
Goal: {meta["goal"]}
Start Date: {meta["start_date"]}
End Date: {meta["end_date"]}
State: {meta["state"]}
Board ID: {meta["board_id"]}

Sprint Scope

## Planned Issues (Total): {scope["planned_total"]}
Completed: {scope["planned_completed"]}
Not Completed: {scope["planned_not_completed"]}
Removed: {scope["planned_removed"]}

## Added Mid-Sprint Issues (Total): {scope["added_total"]}
Completed: {scope["added_completed"]}
Not Completed: {scope["added_not_completed"]}
Removed: {scope["added_removed"]}

## Completed Outside Sprint (Total): {scope["completed_outside_sprint_total"]}
Planned: {scope["completed_outside_sprint_planned"]}
Added Mid-Sprint: {scope["completed_outside_sprint_added"]}

## Totals
Total Issues: {scope["total_issues"]}
Total Completed: {scope["total_completed"]}
Scope Creep Lookup Degraded: {scope_creep_lookup_note}

# Sprint Execution Metrics
Planned Completion Rate: {exec_metrics["planned_completion_rate"]}%
Added Completion Rate: {exec_metrics["added_completion_rate"]}%
Execution Focus Ratio: {exec_metrics["execution_focus_ratio"]}%
Scope Creep Ratio: {exec_metrics["scope_creep_ratio"]}%
Throughput: {exec_metrics["throughput"]}
Planned vs Added Throughput %: {exec_metrics["planned_vs_added_throughput_pct"]}%
"""

        with open(output_dir / "sprint_summary.md", "w", encoding="utf-8") as f:
            f.write(content)

    def _create_issues_json(self, output_dir: Path, issues: list[JiraIssue]):
        """Create issues.json file with all issue data"""
        issues_data = []

        for issue in issues:
            issue_dict = {
                "key": issue.key,
                "id": issue.id,
                "summary": issue.summary,
                "description": issue.description,
                "issue_type": issue.issue_type,
                "status": issue.status,
                "assignee": issue.assignee,
                "priority": issue.priority,
                "story_points": issue.story_points,
                "components": issue.components,
                "labels": issue.labels,
                "created": issue.created,
                "updated": issue.updated,
                "resolved": issue.resolved,
                "was_added_mid_sprint": issue.was_added_mid_sprint,
                "raw_data": issue.raw_data,  # Include full Jira data for manual queries
            }
            issues_data.append(issue_dict)

        with open(output_dir / "issues.json", "w", encoding="utf-8") as f:
            json.dump(issues_data, f, indent=2, default=str)

    def _create_readme(self, output_dir: Path, sprint: Sprint):
        """Create README.md explaining the files"""
        content = f"""# Sprint {sprint.id} Report Files

This directory contains the sprint report for **{sprint.name}**.

## Files

### 1. sprint_summary.md
Contains the sprint summary with key metrics including:
- Sprint metadata (ID, name, dates, etc.)
- Planned vs added issue breakdown
- Completion rates and execution metrics
- Scope creep and throughput analysis

### 2. issues.json
Complete JSON export of all \
{len(self.jira.get_sprint_issues(sprint.id)) if hasattr(self, "jira") else "sprint"} \
issues in the sprint including:
- Issue metadata (key, summary, type, status)
- Assignment and priority information
- Story points and components
- Creation and resolution dates
- Full raw Jira API data for manual querying

Use this file to manually query and analyze issues:
```python
import json
with open('issues.json', 'r') as f:
    issues = json.load(f)

# Find all bugs
bugs = [i for i in issues if i['issue_type'].lower() == 'bug']

# Find issues by assignee
user_issues = [i for i in issues if i['assignee'] == 'John Doe']

# Find completed issues
completed = [i for i in issues if i['status'] in ['Done', 'Closed', 'Resolved']]
```

### 3. readme.md (this file)
Explains the contents and usage of the other files.

## Generated
Report generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} using Sprint Report Generator.
"""

        with open(output_dir / "readme.md", "w", encoding="utf-8") as f:
            f.write(content)
