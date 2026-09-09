/**
 * Forge resolver — backend function invoked by the frontend panel
 * (src/frontend/index.jsx) via @forge/bridge's `invoke()`.
 *
 * Fetches sprint + issue data through Jira's Agile REST API using the app's
 * own Forge-managed auth (api.asApp()), then hands off to sprintHealth.js
 * for the metrics. No Jira credentials are handled here — that's the whole
 * point of running this as a Forge app instead of the standalone CLI, which
 * needs JIRA_API_TOKEN.
 */

const Resolver = require("@forge/resolver").default;
const api = require("@forge/api").default;
const { route } = require("@forge/api");
const { calculateSprintHealth } = require("./sprintHealth");

const resolver = new Resolver();

const MAX_RESULTS = 100;

async function fetchSprint(sprintId) {
  const res = await api.asApp().requestJira(route`/rest/agile/1.0/sprint/${sprintId}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch sprint ${sprintId}: HTTP ${res.status}`);
  }
  return res.json();
}

async function fetchSprintIssues(sprintId) {
  const issues = [];
  let startAt = 0;

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const res = await api
      .asApp()
      .requestJira(
        route`/rest/agile/1.0/sprint/${sprintId}/issue?expand=changelog&startAt=${startAt}&maxResults=${MAX_RESULTS}`
      );
    if (!res.ok) {
      throw new Error(`Failed to fetch issues for sprint ${sprintId}: HTTP ${res.status}`);
    }
    const data = await res.json();
    const batch = data.issues ?? [];
    issues.push(...batch);
    if (batch.length < MAX_RESULTS) break;
    startAt += MAX_RESULTS;
  }

  return issues;
}

/**
 * Issues that were added to this sprint and later removed — same rationale
 * as JiraClient._get_removed_sprint_issues() in core.py: the sprint's
 * "current issues" endpoint can't see them, but they still count toward
 * scope creep. Best-effort: JQL sprint-history operators degrade to an
 * empty result rather than failing the whole panel if unsupported — but,
 * mirroring core.py's `scope_creep_lookup_degraded`, that degradation is
 * reported back to the caller rather than swallowed, so a failed lookup
 * isn't indistinguishable from a genuine zero removed issues.
 *
 * Each returned raw issue is tagged `_removedViaJql: true` — the same hint
 * core.py's JiraIssue._removed_via_jql carries — so applySprintMembership's
 * changelog-fallback path (sprintHealth.js) knows the issue isn't a current
 * sprint member even when it has no relevant Sprint-field changelog events.
 */
async function fetchRemovedSprintIssues(sprintId, excludeKeys) {
  const jql = `sprint was ${sprintId} AND sprint != ${sprintId}`;
  const issues = [];
  let startAt = 0;

  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const res = await api.asApp().requestJira(route`/rest/api/3/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jql,
          startAt,
          maxResults: MAX_RESULTS,
          expand: ["changelog"],
          fields: ["*all"],
        }),
      });
      if (!res.ok) return { issues: [], degraded: true };
      const data = await res.json();
      const page = data.issues ?? [];
      for (const raw of page) {
        if (!excludeKeys.has(raw.key)) {
          raw._removedViaJql = true;
          issues.push(raw);
        }
      }
      if (page.length < MAX_RESULTS) break;
      startAt += MAX_RESULTS;
    }
  } catch {
    return { issues: [], degraded: true };
  }

  return { issues, degraded: false };
}

resolver.define("getSprintHealth", async (req) => {
  const { sprintId } = req.payload;
  if (!sprintId) {
    throw new Error("sprintId is required");
  }

  const sprint = await fetchSprint(sprintId);
  const currentIssues = await fetchSprintIssues(sprintId);
  const currentKeys = new Set(currentIssues.map((i) => i.key));
  const { issues: removedIssues, degraded: scopeCreepLookupDegraded } =
    await fetchRemovedSprintIssues(sprintId, currentKeys);

  const sprintStartDate = sprint.startDate ? new Date(sprint.startDate) : null;
  return calculateSprintHealth(
    sprint,
    [...currentIssues, ...removedIssues],
    sprintStartDate,
    scopeCreepLookupDegraded
  );
});

exports.handler = resolver.getDefinitions();
