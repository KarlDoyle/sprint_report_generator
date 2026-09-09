/**
 * Regression tests for sprintHealth.js — plain Node + `assert`, no test
 * framework dependency (none is installed; see forge-app/package.json). Run
 * directly with `node src/resolvers/__tests__/sprintHealth.test.js` from
 * `forge-app/`.
 *
 * Covers the three stress-test findings that live in this file:
 *   1. Python/JS rounding parity (round-half-to-even vs. Math.round)
 *   2. The JQL-removed fallback no longer hardcodes currentlyInSprint: true
 *   4. The ported plannedCompletionRate / addedCompletionRate /
 *      completedOutsideSprint_* metrics
 */

const assert = require("node:assert/strict");
const {
  applySprintMembership,
  calculateSprintHealth,
  roundHalfEven,
} = require("../sprintHealth");

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`ok - ${name}`);
  } catch (err) {
    console.error(`FAIL - ${name}`);
    console.error(err);
    process.exitCode = 1;
  }
}

function sprintFieldHistory(created, fromIds, toIds) {
  return {
    id: "10000",
    author: { displayName: "Jane Doe" },
    created,
    items: [
      {
        field: "Sprint",
        fieldtype: "custom",
        fieldId: "customfield_10020",
        from: fromIds.join(","),
        fromString: fromIds.map((i) => `Sprint ${i}`).join(", "),
        to: toIds.join(","),
        toString: toIds.map((i) => `Sprint ${i}`).join(", "),
      },
    ],
  };
}

function rawIssue({ key, status, created, changelogHistories, removedViaJql = false }) {
  const issue = {
    key,
    id: key,
    fields: {
      status: { name: status },
      created,
    },
  };
  if (changelogHistories) {
    issue.changelog = { histories: changelogHistories };
  }
  if (removedViaJql) {
    issue._removedViaJql = true;
  }
  return issue;
}

// =============================================================================
// Issue 1 — rounding parity
// =============================================================================

test("roundHalfEven matches Python round() on the 12.5%-style tie (N even)", () => {
  // 1 added / 8 total = 12.5% exactly. Python's round(12.5) -> 12 (nearest
  // even). Math.round(12.5) -> 13 (always rounds .5 up) — the bug.
  assert.equal(roundHalfEven(12.5), 12);
  assert.equal(Math.round(12.5), 13); // documents the mismatch this replaces
});

test("roundHalfEven matches Python round() on a tie where N is odd", () => {
  // Python round(87.5) -> 88 (nearest even), same as Math.round here — this
  // side of the tie was never the bug, confirm we didn't break it.
  assert.equal(roundHalfEven(87.5), 88);
  assert.equal(Math.round(87.5), 88);
});

test("roundHalfEven behaves like normal rounding away from a tie", () => {
  assert.equal(roundHalfEven(12.4), 12);
  assert.equal(roundHalfEven(12.6), 13);
  assert.equal(roundHalfEven(0), 0);
});

test("calculateSprintHealth produces 12% scope creep for the 1-added-of-8 repro, not 13%", () => {
  const sprint = { id: 42, name: "Sprint 42", state: "active", goal: "" };
  const sprintStart = new Date("2026-01-01T00:00:00.000Z");

  const planned = Array.from({ length: 7 }, (_, i) =>
    rawIssue({
      key: `P-${i + 1}`,
      status: "To Do",
      created: "2025-12-01T09:00:00.000Z",
      changelogHistories: [sprintFieldHistory("2026-01-01T00:00:00.000Z", [], [42])],
    })
  );
  const added = rawIssue({
    key: "P-8",
    status: "To Do",
    created: "2025-12-01T09:00:00.000Z",
    changelogHistories: [sprintFieldHistory("2026-01-04T00:00:00.000Z", [], [42])],
  });

  const health = calculateSprintHealth(sprint, [...planned, added], sprintStart);

  assert.equal(health.scope.totalIssues, 8);
  assert.equal(health.scope.addedTotal, 1);
  // The exact repro from the stress-test finding: 1/8 = 12.5% -> 12, not 13.
  assert.equal(health.executionMetrics.scopeCreepRatio, 12);
});

// =============================================================================
// Issue 2 — JQL-removed fallback must not hardcode currentlyInSprint: true
// =============================================================================

test("applySprintMembership: issue with no changelog events but found via JQL-removed lookup is NOT currently in sprint", () => {
  const issue = rawIssue({
    key: "P-9",
    status: "To Do",
    created: "2025-12-01T09:00:00.000Z",
    // No changelog history at all for our sprint — the exact case the fallback covers.
    changelogHistories: [],
    removedViaJql: true,
  });

  const result = applySprintMembership(issue, 42, new Date("2026-01-01T00:00:00.000Z"));

  assert.equal(result.historySource, "created_date_fallback");
  assert.equal(
    result.currentlyInSprint,
    false,
    "issue tagged _removedViaJql must not default to currentlyInSprint: true"
  );
});

test("applySprintMembership: issue with no changelog events and NOT found via JQL lookup is still currently in sprint (unchanged behavior)", () => {
  const issue = rawIssue({
    key: "P-10",
    status: "To Do",
    created: "2025-12-01T09:00:00.000Z",
    changelogHistories: [],
    removedViaJql: false,
  });

  const result = applySprintMembership(issue, 42, new Date("2026-01-01T00:00:00.000Z"));

  assert.equal(result.currentlyInSprint, true);
});

test("calculateSprintHealth: a JQL-removed issue with no changelog counts as removed, not current", () => {
  const sprint = { id: 42, name: "Sprint 42", state: "active", goal: "" };
  const sprintStart = new Date("2026-01-01T00:00:00.000Z");

  const planned = rawIssue({
    key: "P-1",
    status: "Done",
    created: "2025-12-01T09:00:00.000Z",
    changelogHistories: [sprintFieldHistory("2026-01-01T00:00:00.000Z", [], [42])],
  });
  const removedNoChangelog = rawIssue({
    key: "P-2",
    status: "To Do",
    created: "2025-12-01T09:00:00.000Z",
    changelogHistories: [],
    removedViaJql: true,
  });

  const health = calculateSprintHealth(sprint, [planned, removedNoChangelog], sprintStart);

  assert.equal(health.scope.plannedTotal, 2);
  assert.equal(
    health.scope.plannedRemoved,
    1,
    "the JQL-removed issue must be counted as removed"
  );
});

// =============================================================================
// Issue 4 — ported metrics
// =============================================================================

test("calculateSprintHealth exposes plannedCompletionRate, addedCompletionRate, and completedOutsideSprint_*", () => {
  const sprint = { id: 42, name: "Sprint 42", state: "active", goal: "" };
  const sprintStart = new Date("2026-01-01T00:00:00.000Z");

  // 4 planned: 2 completed (1 of those completed-and-removed).
  const planned = [
    rawIssue({
      key: "P-1",
      status: "Done",
      created: "2025-12-01T09:00:00.000Z",
      changelogHistories: [sprintFieldHistory("2026-01-01T00:00:00.000Z", [], [42])],
    }),
    rawIssue({
      key: "P-2",
      status: "Done",
      created: "2025-12-01T09:00:00.000Z",
      changelogHistories: [
        sprintFieldHistory("2026-01-01T00:00:00.000Z", [], [42]),
        sprintFieldHistory("2026-01-10T00:00:00.000Z", [42], []),
      ],
    }),
    rawIssue({
      key: "P-3",
      status: "To Do",
      created: "2025-12-01T09:00:00.000Z",
      changelogHistories: [sprintFieldHistory("2026-01-01T00:00:00.000Z", [], [42])],
    }),
    rawIssue({
      key: "P-4",
      status: "To Do",
      created: "2025-12-01T09:00:00.000Z",
      changelogHistories: [sprintFieldHistory("2026-01-01T00:00:00.000Z", [], [42])],
    }),
  ];
  // 1 added mid-sprint, completed.
  const added = rawIssue({
    key: "A-1",
    status: "Done",
    created: "2025-12-01T09:00:00.000Z",
    changelogHistories: [sprintFieldHistory("2026-01-05T00:00:00.000Z", [], [42])],
  });

  const health = calculateSprintHealth(sprint, [...planned, added], sprintStart);

  assert.equal(health.executionMetrics.plannedCompletionRate, 50); // 2/4
  assert.equal(health.executionMetrics.addedCompletionRate, 100); // 1/1
  assert.equal(health.scope.completedOutsideSprintPlanned, 1); // P-2
  assert.equal(health.scope.completedOutsideSprintAdded, 0);
  assert.equal(health.scope.completedOutsideSprintTotal, 1);
});

// =============================================================================
// Issue 3 — degraded scope-creep lookup is surfaced, not swallowed
// =============================================================================

test("calculateSprintHealth surfaces scopeCreepLookupDegraded when passed true", () => {
  const sprint = { id: 42, name: "Sprint 42", state: "active", goal: "" };
  const health = calculateSprintHealth(sprint, [], null, true);
  assert.equal(health.scope.scopeCreepLookupDegraded, true);
});

test("calculateSprintHealth defaults scopeCreepLookupDegraded to false", () => {
  const sprint = { id: 42, name: "Sprint 42", state: "active", goal: "" };
  const health = calculateSprintHealth(sprint, [], null);
  assert.equal(health.scope.scopeCreepLookupDegraded, false);
});

console.log(`\n${passed} test(s) passed.`);
if (process.exitCode) {
  console.error("Some tests FAILED.");
  process.exit(1);
}
