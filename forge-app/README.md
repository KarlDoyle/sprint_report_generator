# Sprint Health Report — Forge app scaffold

Surfaces the sprint-health report (planned vs. added-mid-sprint completion,
execution focus ratio, scope creep ratio) directly inside a Jira Software
project, using the same changelog-based sprint-membership logic as the
standalone CLI (`sprint_report_generator/core.py`).

This is a **scaffold, not a deployed app**. Deploying requires Karl's own
Atlassian developer account — that step can't be done from here. Everything
up to that point (manifest, resolver, panel) is built and ready to review.

## What's built

- `manifest.yml` — a `jira:projectPage` module (a custom page in the project
  sidebar), UI Kit 2 (`render: native`, `@forge/react`), read-only scopes.
- `src/resolvers/index.js` + `sprintHealth.js` — backend: fetches the sprint
  and its issues (with `expand=changelog`) via
  `api.asUser().requestJira()` using the invoking user's Jira permissions,
  plus the same "added then removed" JQL lookup the Python client uses
  (`sprint was <id> AND sprint != <id>`), then computes the metrics.
- `src/frontend/index.jsx` — the panel: sprint ID entry, load button, and the
  scope/execution-focus/scope-creep numbers, with the same observational
  framing as the CLI's report ("not a performance verdict").

## What's NOT built (honest gaps)

- **Auto-detecting the active sprint.** V1 requires typing in a sprint ID.
  `jira:projectPage` doesn't hand you a "current sprint" in context the way
  an issue panel gets an issue — auto-detect would mean an extra resolver
  call to list the project's boards, then that board's active sprint(s). Left
  out to keep the scaffold small; straightforward to add once the deploy is
  live and the UX is validated with real use.
- **Multi-team rollup in the panel.** The CLI's `generate_multi_team_report()`
  isn't ported here — the panel is single-sprint only for now. A rollup view
  would most naturally live at a different scope (e.g. a global page across
  projects, not a per-project page) and is worth designing separately rather
  than force-fitting into this module.
- **`sprintHealth.js` is a hand-written JS port of `core.py`, not shared code.**
  Forge functions run on Node, not Python, so there's no way to literally
  reuse `core.py` here. The changelog-membership logic in particular
  (`applySprintMembership`) is duplicated by hand from
  `JiraIssue.apply_sprint_membership()` and can drift if one side is fixed
  and the other isn't. If this panel gets real usage, worth extracting the
  membership rules into a small shared fixture set both the Python and JS
  test suites run against, so a fix in one is provably reflected in both.
- **No JS test suite yet.** `package.json`'s `test` script is a placeholder.
  Before this ships, `sprintHealth.js` needs the same rigor as
  `tests/test_sprint_membership.py` on the Python side — same four scenarios
  (added at sprint start, added mid-sprint, removed-then-re-added, no
  changelog entries) — ideally against the identical mocked changelog
  fixtures so both implementations are checked against the same ground truth.
- **Manifest/resource syntax not verified against a live `forge deploy`.**
  Built from Atlassian's current documented patterns for `jira:projectPage` +
  UI Kit 2, but Forge's manifest schema does shift between CLI versions.
  Before the first real deploy: run `forge create` for a throwaway
  `jira-project-page` template app and diff its generated `manifest.yml`
  against this one — cheaper than debugging a deploy failure blind.

## Manual steps Karl needs to do (requires your own Atlassian developer account)

1. **Install the Forge CLI** (needs Node.js 18+):
   ```
   npm install -g @forge/cli
   ```

2. **Log in** with your Atlassian account:
   ```
   forge login
   ```

3. **Install app dependencies:**
   ```
   cd forge-app
   npm install
   ```

4. **Register the app** — this assigns the real `app.id` that the placeholder
   in `manifest.yml` needs replacing with:
   ```
   forge register
   ```
   Forge will prompt for an app name and write the real `app.id` into
   `manifest.yml` for you (or tell you to paste it in).

5. **Deploy to a development environment:**
   ```
   forge deploy
   ```

6. **Install it on your Jira site:**
   ```
   forge install
   ```
   Follow the prompts to pick the Jira Cloud site and confirm the requested
   scopes (`read:jira-work`, `read:sprint:jira-software`,
   `read:board-scope:jira-software`).

7. **Verify:** open a Jira Software project → the "Sprint Health Report" entry
   should appear in the project sidebar. Enter a sprint ID and load the report.

8. **Iterate:** `forge tunnel` gives a live-reload dev loop instead of
   redeploying on every change.

## Marketplace listing (later, not now)

Once the app is deployed and validated on a real site, listing it on the
Atlassian Marketplace is a separate, later step (developer account
verification, listing content, pricing, security review) — out of scope for
this scaffold. Flagging so it isn't assumed to be part of "deploy".
