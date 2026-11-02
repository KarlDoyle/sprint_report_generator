# Sprint Report Generator

Generates a sprint-health report from a Jira sprint: planned vs. added-mid-sprint
completion, execution focus ratio, and scope creep ratio — built to answer the
question a TPM or director actually asks about a sprint ("how much of what we
finished was the plan, vs. what got pulled in along the way"), not just
velocity or burndown.

Standard-library-only Python (no runtime dependencies). Validated in person
against a real sprint by a VP of TPM.

## Install

```
pip install -e .
# or, with uv:
uv sync
```

## Usage

Single sprint:

```
export JIRA_BASE_URL=https://yourcompany.atlassian.net
export JIRA_USERNAME=you@yourcompany.com
export JIRA_API_TOKEN=your-api-token

srg 12345
```

Writes `sprint_summary.md`, `issues.json`, and `readme.md` to a
`sprint-<id>-<date>-<name>/` directory.

Multi-team rollup, for a TPM/director watching several teams at once:

```
srg --team Payments:101 --team Platform:102
```

Writes one full per-team report directory plus `rollup_summary.md` — a
cross-team, observational view (not a ranking or a verdict on any one team).
`JIRA_TEAM_SPRINTS="Payments:101,Platform:102"` works as an env-var equivalent.

## How sprint-membership timing works

"Added mid-sprint" is determined from Jira's own changelog — specifically the
`field: "Sprint"` history entries the Agile REST API returns under
`expand=changelog`, which record exactly when an issue was added to or
removed from a sprint. This replaced an earlier heuristic that inferred
"added mid-sprint" from an issue's *creation* date vs. the sprint's start
date, which was wrong whenever an issue was created earlier and only pulled
into the sprint later (or vice versa) — see
`sprint_report_generator/core.py::JiraIssue.apply_sprint_membership` for the
full explanation and the Jira-specific edge cases it handles (issues created
directly inside a sprint leave no "added" changelog entry; sprint names can
contain commas so only the ID lists are parsed, never the name strings).

Scope-creep ratio also now counts issues that were added mid-sprint and later
removed — they entered the sprint's scope even though they didn't stay, which
the old implementation silently dropped since it only ever looked at the
sprint's *current* members.

## Forge app (Jira-embedded panel)

`forge-app/` is a scaffold that surfaces this same report as a panel inside a
Jira Software project — Atlassian's current framework for Jira integrations
(Connect no longer accepts new apps). It is **not deployed**; deploying
requires an Atlassian developer account. See `forge-app/README.md` for what's
built, what's intentionally left out, and the exact manual steps to deploy it.

## Data handling

The generated `issues.json` contains Jira issue data, including descriptions,
assignee display names, labels, and timestamps. Treat generated reports as
confidential and do not commit or share them unless you have permission to do
so. The [synthetic example](examples/synthetic-report.md) uses fictional data
only and is safe to publish.

## Development

```
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

`.githooks/pre-push` runs the same checks before every push
(`git config core.hooksPath .githooks`, or `./scripts/install-hooks.sh`).
