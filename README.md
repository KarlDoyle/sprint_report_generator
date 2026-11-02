# Sprint Report Generator

I built this type of Jira sprint reporting to solve a recurring problem I saw across teams, departments, and companies: when work finishes, how much was in the plan and how much was added during the sprint? This repository packages that approach as an abstracted, white-label CLI that can be configured for a team's Jira instance; it is not a copy of any company's internal reporting system.

The reporting approach was validated in person against a real sprint with a VP of TPM. This repository contains no company Jira data. The example below is fictional.

## Example output

| Measure | Fictional example |
| --- | ---: |
| Planned issues completed | 4 of 6 |
| Added mid-sprint issues completed | 2 of 2 |
| Execution focus ratio | 67% |
| Scope creep ratio | 25% |

[View the full synthetic report](examples/synthetic-report.md). These measures describe sprint scope and completion; they are not a team ranking or performance verdict.

## Requirements

- Python 3.10 or later
- Jira Software access to the board, sprint, and issues you want to report
- A Jira API token for an account with permission to read that data

## Quick start

Clone the repository and install the CLI:

```sh
git clone https://github.com/KarlDoyle/sprint_report_generator.git
cd sprint_report_generator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Set your Jira connection details and run a report:

```sh
export JIRA_BASE_URL=https://yourcompany.atlassian.net
export JIRA_USERNAME=you@yourcompany.com
export JIRA_API_TOKEN=your-api-token

srg 12345 --output-dir ./reports
```

Replace `12345` with the numeric sprint ID. To find it, list the sprints for a board with Jira's [Get all sprints endpoint](https://developer.atlassian.com/cloud/jira/software/rest/api-group-board/#api-rest-agile-1-0-board-boardid-sprint-get) and use the sprint's `id` value. Keep your API token out of source files and generated reports.

The command creates a `sprint-<id>-<date>-<name>/` directory under `./reports` with:

- `sprint_summary.md` — sprint metrics and analysis
- `issues.json` — issue data used to calculate the report
- `readme.md` — notes about the generated files

If `--output-dir` is omitted, the current working directory is used.

## Multi-team rollup

For a cross-team observational view, repeat `--team NAME:SPRINT_ID`:

```sh
srg --team Payments:101 --team Platform:102 --output-dir ./reports
```

This writes a report directory for each team plus `rollup_summary.md`. You can also set `JIRA_TEAM_SPRINTS="Payments:101,Platform:102"`. The rollup is not a ranking or verdict on any one team.

## Metrics and sprint membership

- **Planned completion rate** is planned issues completed divided by planned issues.
- **Added completion rate** is mid-sprint additions completed divided by mid-sprint additions.
- **Execution focus ratio** is planned issues completed divided by all completed issues.
- **Scope creep ratio** is mid-sprint additions divided by all issues counted in the sprint scope, including additions later removed.

“Added mid-sprint” is determined from Jira's changelog: the `field: "Sprint"` history entries returned with `expand=changelog` record when an issue was added to or removed from a sprint. This avoids inferring membership from an issue's creation date. Issues created directly inside a sprint may have no separate “added” changelog entry. Sprint names can contain commas, so the implementation parses sprint ID lists rather than names; see `sprint_report_generator/core.py::JiraIssue.apply_sprint_membership` for details.

## Forge app (Jira panel scaffold)

`forge-app/` is an undeployed scaffold for showing this report in a Jira Software project. It requires an Atlassian developer account to deploy. Review [the Forge app README](forge-app/README.md) for what is implemented and the remaining manual deployment steps. The resolver's Jira access must be checked against the invoking user's permissions before deployment.

## Data handling

The CLI sends Jira credentials and requests to the Jira base URL you configure over HTTPS. It writes reports locally; it does not upload generated reports. `issues.json` includes the full Jira issue response in `raw_data`, in addition to the named fields, so it may contain private issue details beyond descriptions, assignee names, labels, and timestamps. Treat the entire output directory as confidential and share it only with people authorized to see that Jira data.

The [synthetic example](examples/synthetic-report.md) uses fictional names, issue keys, and figures and is safe to publish.

## Development

```sh
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

`.githooks/pre-push` runs these checks before every push. Enable it with `git config core.hooksPath .githooks` or run `./scripts/install-hooks.sh`.
