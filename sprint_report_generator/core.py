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
            with urllib.request.urlopen(request) as response:
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

        # Sprint information from changelog
        self.added_to_sprint_date = None
        self.was_added_mid_sprint = False

    def set_sprint_timing(self, sprint_start_date: date | None):
        """Determine if issue was added mid-sprint"""
        if not sprint_start_date or not self.created:
            return

        try:
            created_date = datetime.fromisoformat(self.created.replace("Z", "+00:00")).date()
            self.was_added_mid_sprint = created_date > sprint_start_date
        except (ValueError, AttributeError):
            pass


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

    def get_sprint(self, sprint_id: int) -> Sprint:
        """Get sprint information"""
        data = self.http.get(f"/rest/agile/1.0/sprint/{sprint_id}")
        return Sprint(data)

    def get_sprint_issues(self, sprint_id: int) -> list[JiraIssue]:
        """Get all issues in a sprint"""
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


# =============================================================================
# METRICS CALCULATOR
# =============================================================================


class SprintMetricsCalculator:
    def __init__(self, sprint: Sprint, issues: list[JiraIssue]):
        self.sprint = sprint
        self.issues = issues

        # Set sprint timing for all issues
        for issue in self.issues:
            issue.set_sprint_timing(sprint.start_date)

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

        # Added mid-sprint metrics
        added_total = len(self.added_issues)
        added_completed = len(
            [i for i in self.added_issues if i.status in self.completion_statuses]
        )
        added_not_completed = added_total - added_completed

        # Totals
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
                "planned_removed": 0,  # Not tracked in this simple version
                "added_total": added_total,
                "added_completed": added_completed,
                "added_not_completed": added_not_completed,
                "added_removed": 0,  # Not tracked in this simple version
                # Completed Outside Sprint: Issues closed but removed from sprint (requires
                # changelog analysis)
                "completed_outside_sprint_total": 0,  # Not tracked - needs sprint removal history
                "completed_outside_sprint_planned": 0,
                "completed_outside_sprint_added": 0,
                "total_issues": total_issues,
                "total_completed": total_completed,
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
        calculator = SprintMetricsCalculator(sprint, issues)
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

        content = f"""Sprint {meta["sprint_id"]} Summary - {meta["name"]}

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
