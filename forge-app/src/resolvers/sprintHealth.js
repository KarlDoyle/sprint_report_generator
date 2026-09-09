/**
 * Sprint health metrics — JS port of the core logic in
 * sprint_report_generator/core.py (JiraIssue.apply_sprint_membership,
 * SprintMetricsCalculator). Forge functions run on Node, not Python, so this
 * is a deliberate, minimal re-implementation of just the parts the panel
 * needs — NOT the full CLI tool (no report file generation, no multi-team
 * rollup yet).
 *
 * IMPORTANT: keep this in sync with core.py by hand for now. If this panel
 * gets real usage, the changelog-membership logic in particular is worth
 * extracting into a shared, spec-tested definition (e.g. a small fixture set
 * both the Python and JS tests run against) rather than maintaining two
 * hand-written copies that can silently drift.
 */

const COMPLETION_STATUSES = [
  "Done",
  "QA Passed",
  "Resolved",
  "Closed",
  "Completed",
  "Verified",
  "Accepted",
];

/**
 * Round-half-to-even ("banker's rounding"), matching Python's built-in
 * `round()` — which is what core.py uses for every percentage metric.
 * `Math.round()` always rounds .5 up, so on an exact N.5 tie (N even) it
 * disagrees with Python: e.g. 1/8 = 12.5% -> Python round() = 12,
 * Math.round() = 13. Any total that's a multiple of 8/16/40 with the right
 * split lands exactly on a tie, so this isn't a contrived edge case — it's
 * a real, recurring cross-language mismatch. Only Python's default `round()`
 * (ndigits=0) semantics are replicated here; that's all core.py ever uses
 * for these metrics.
 */
function roundHalfEven(x) {
  if (!Number.isFinite(x)) return x;
  const floor = Math.floor(x);
  const diff = x - floor;
  const EPSILON = 1e-9; // guard against float division not landing on an exact .5
  if (Math.abs(diff - 0.5) < EPSILON) {
    return floor % 2 === 0 ? floor : floor + 1;
  }
  return Math.round(x);
}

/** Parse a changelog Sprint item's comma-separated `from`/`to` ID string. */
function parseSprintIdSet(value) {
  if (!value) return new Set();
  return new Set(
    String(value)
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
      .map((s) => parseInt(s, 10))
      .filter((n) => !Number.isNaN(n))
  );
}

/**
 * Determine sprint add/remove timing for one issue from its changelog.
 *
 * Mirrors JiraIssue.apply_sprint_membership() in core.py: Jira's Agile REST
 * API (`expand=changelog`) records sprint membership changes as changelog
 * history items with `field: "Sprint"`. `from`/`to` are comma-separated
 * sprint IDs (never parse `fromString`/`toString` — sprint names can contain
 * commas). `fieldId` varies per instance, so match on `field === "Sprint"` only.
 *
 * Falls back to the created-date heuristic when the issue has no Sprint-field
 * changelog entries at all. Mirrors core.py's `_removed_via_jql` handling: an
 * issue found only via the "removed from sprint" JQL lookup (tagged
 * `issue._removedViaJql` by the caller — see index.js's
 * fetchRemovedSprintIssues) is correctly marked as not currently in the
 * sprint even when it has no relevant changelog events, instead of defaulting
 * to `true`.
 */
function applySprintMembership(issue, sprintId, sprintStartDate) {
  const events = [];
  const histories = issue.changelog?.histories ?? [];

  for (const history of histories) {
    if (!history.created) continue;
    const ts = new Date(history.created);
    if (Number.isNaN(ts.getTime())) continue;

    for (const item of history.items ?? []) {
      if (item.field !== "Sprint") continue;
      const fromIds = parseSprintIdSet(item.from);
      const toIds = parseSprintIdSet(item.to);
      const wasIn = fromIds.has(sprintId);
      const nowIn = toIds.has(sprintId);
      if (wasIn === nowIn) continue;
      events.push({ ts, nowIn });
    }
  }

  if (events.length === 0) {
    const createdDate = issue.fields?.created ? new Date(issue.fields.created) : null;
    const wasAddedMidSprint =
      !!sprintStartDate && !!createdDate && createdDate > sprintStartDate;
    const removedViaJql = !!issue._removedViaJql;
    return {
      historySource: "created_date_fallback",
      addedAt: null,
      removedAt: null,
      currentlyInSprint: !removedViaJql,
      wasAddedMidSprint,
    };
  }

  events.sort((a, b) => a.ts - b.ts);

  let addedAt = null;
  let removedAt = null;
  let currentlyIn = false;
  for (const { ts, nowIn } of events) {
    currentlyIn = nowIn;
    if (nowIn && addedAt === null) addedAt = ts;
    if (!nowIn) removedAt = ts;
  }

  const wasAddedMidSprint =
    addedAt !== null && !!sprintStartDate ? addedAt > sprintStartDate : false;

  return {
    historySource: "changelog",
    addedAt,
    removedAt,
    currentlyInSprint: currentlyIn,
    wasAddedMidSprint,
  };
}

function issueStatus(issue) {
  return issue.fields?.status?.name ?? "Unknown";
}

/**
 * Compute the same headline metrics as SprintMetricsCalculator.calculate_metrics()
 * in core.py: planned/added completion rates, execution focus ratio, scope
 * creep ratio, and completed-outside-sprint counts. `issues` should already
 * include both current sprint members and any "added then removed" issues
 * the caller fetched separately, so scope creep counts issues that entered
 * scope even if they didn't stay.
 *
 * `scopeCreepLookupDegraded` mirrors core.py's `scope_creep_lookup_degraded`:
 * pass `true` when the caller's "removed from sprint" JQL lookup failed, so
 * the panel can show "couldn't check" instead of looking identical to a
 * genuine zero removed issues.
 */
function calculateSprintHealth(sprint, issues, sprintStartDate, scopeCreepLookupDegraded = false) {
  const membership = issues.map((issue) => ({
    issue,
    ...applySprintMembership(issue, sprint.id, sprintStartDate),
  }));

  const planned = membership.filter((m) => !m.wasAddedMidSprint);
  const added = membership.filter((m) => m.wasAddedMidSprint);

  const isCompleted = (m) => COMPLETION_STATUSES.includes(issueStatus(m.issue));

  const plannedTotal = planned.length;
  const plannedCompleted = planned.filter(isCompleted).length;
  const plannedRemoved = planned.filter((m) => !m.currentlyInSprint).length;

  const addedTotal = added.length;
  const addedCompleted = added.filter(isCompleted).length;
  const addedRemoved = added.filter((m) => !m.currentlyInSprint).length;

  // Completed Outside Sprint: closed but no longer a sprint member — mirrors
  // core.py's completed_outside_sprint_* fields.
  const completedOutsideSprintPlanned = planned.filter(
    (m) => !m.currentlyInSprint && isCompleted(m)
  ).length;
  const completedOutsideSprintAdded = added.filter(
    (m) => !m.currentlyInSprint && isCompleted(m)
  ).length;
  const completedOutsideSprintTotal = completedOutsideSprintPlanned + completedOutsideSprintAdded;

  const totalIssues = plannedTotal + addedTotal;
  const totalCompleted = plannedCompleted + addedCompleted;

  const plannedCompletionRate = plannedTotal > 0 ? (plannedCompleted / plannedTotal) * 100 : 0;
  const addedCompletionRate = addedTotal > 0 ? (addedCompleted / addedTotal) * 100 : 0;
  const executionFocusRatio = totalCompleted > 0 ? (plannedCompleted / totalCompleted) * 100 : 0;
  const scopeCreepRatio = totalIssues > 0 ? (addedTotal / totalIssues) * 100 : 0;

  return {
    sprint: { id: sprint.id, name: sprint.name, state: sprint.state, goal: sprint.goal },
    scope: {
      plannedTotal,
      plannedCompleted,
      plannedRemoved,
      addedTotal,
      addedCompleted,
      addedRemoved,
      completedOutsideSprintPlanned,
      completedOutsideSprintAdded,
      completedOutsideSprintTotal,
      totalIssues,
      totalCompleted,
      scopeCreepLookupDegraded: !!scopeCreepLookupDegraded,
    },
    executionMetrics: {
      plannedCompletionRate: roundHalfEven(plannedCompletionRate),
      addedCompletionRate: roundHalfEven(addedCompletionRate),
      executionFocusRatio: roundHalfEven(executionFocusRatio),
      scopeCreepRatio: roundHalfEven(scopeCreepRatio),
    },
  };
}

module.exports = {
  applySprintMembership,
  calculateSprintHealth,
  parseSprintIdSet,
  roundHalfEven,
};
