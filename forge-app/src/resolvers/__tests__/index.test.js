/**
 * Regression tests for index.js's sprint authorization boundary and
 * fetchRemovedSprintIssues() degraded-state and `_removedViaJql` behavior.
 *
 * index.js requires @forge/resolver and @forge/api, which aren't installed
 * in this scaffold (no node_modules — Forge apps run in Forge's managed
 * runtime, not a local install). We stub both via Module._load before
 * requiring index.js, then drive fetchRemovedSprintIssues by calling the
 * resolver function registered for "getSprintHealth" through our fake
 * `api.asUser().requestJira()`.
 *
 * Run with: node src/resolvers/__tests__/index.test.js
 */

const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");

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

async function asyncTest(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`ok - ${name}`);
  } catch (err) {
    console.error(`FAIL - ${name}`);
    console.error(err);
    process.exitCode = 1;
  }
}

// --- Stub @forge/resolver and @forge/api ------------------------------------

let registeredHandlers = {};
let requestJiraImpl = null;
let asAppCalls = 0;
let asUserCalls = 0;

class FakeResolver {
  define(name, fn) {
    registeredHandlers[name] = fn;
  }
  getDefinitions() {
    return () => {};
  }
}

const fakeForgeResolverModule = { default: FakeResolver };
const fakeForgeApiModule = {
  default: {
    asApp: () => {
      asAppCalls++;
      return { requestJira: (...args) => requestJiraImpl(...args) };
    },
    asUser: () => {
      asUserCalls++;
      return { requestJira: (...args) => requestJiraImpl(...args) };
    },
  },
  route: (strings, ...values) => strings.reduce((acc, s, i) => acc + s + (values[i] ?? ""), ""),
};

const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "@forge/resolver") return fakeForgeResolverModule;
  if (request === "@forge/api") return fakeForgeApiModule;
  return originalLoad.apply(this, arguments);
};

// Fresh require of index.js under the stubs above.
const indexPath = path.join(__dirname, "..", "index.js");
delete require.cache[require.resolve(indexPath)];
require(indexPath);
Module._load = originalLoad; // restore for anything else running in this process

function fakeRes(ok, body) {
  return { ok, status: ok ? 200 : 403, json: async () => body };
}

function resetAuthCalls() {
  asAppCalls = 0;
  asUserCalls = 0;
}

async function main() {

// =============================================================================
// Authorization boundary — reject malformed IDs and use the invoking user
// =============================================================================

await asyncTest(
  "getSprintHealth rejects invalid sprint IDs before making Jira requests",
  async () => {
    resetAuthCalls();
    let requests = 0;
    requestJiraImpl = async () => {
      requests++;
      return fakeRes(true, {});
    };

    for (const sprintId of [0, -1, 1.5, Number.NaN, "42 OR 1=1", {}]) {
      await assert.rejects(
        registeredHandlers.getSprintHealth({ payload: { sprintId } }),
        /sprintId must be a positive integer/
      );
    }

    assert.equal(requests, 0);
    assert.equal(asUserCalls, 0);
    assert.equal(asAppCalls, 0);
  }
);

await asyncTest(
  "getSprintHealth uses the invoking user's Jira permissions for a valid sprint",
  async () => {
    resetAuthCalls();
    requestJiraImpl = async (route) => {
      const path = String(route);
      if (path.includes("/rest/agile/1.0/sprint/42") && !path.includes("/issue")) {
        return fakeRes(true, { id: 42, name: "Sprint 42", state: "active", startDate: "2026-01-01T00:00:00.000Z" });
      }
      if (path.includes("/rest/agile/1.0/sprint/42/issue")) {
        return fakeRes(true, { issues: [] });
      }
      if (path.includes("/rest/api/3/search")) {
        return fakeRes(true, { issues: [] });
      }
      throw new Error(`unexpected route: ${path}`);
    };

    const result = await registeredHandlers.getSprintHealth({ payload: { sprintId: 42 } });
    assert.equal(result.sprint.id, 42);
    assert.equal(asUserCalls, 3);
    assert.equal(asAppCalls, 0);
  }
);

await asyncTest(
  "getSprintHealth fails closed when the invoking user cannot view a sprint",
  async () => {
    resetAuthCalls();
    requestJiraImpl = async () => fakeRes(false, {});

    await assert.rejects(
      registeredHandlers.getSprintHealth({ payload: { sprintId: 99 } }),
      /Failed to fetch sprint 99: HTTP 403/
    );
    assert.equal(asUserCalls, 1);
    assert.equal(asAppCalls, 0);
  }
);

// =============================================================================
// Issue 3 — a failed removed-issues JQL lookup must surface as degraded: true
// =============================================================================

await asyncTest(
  "getSprintHealth surfaces scopeCreepLookupDegraded: true when the removed-issues lookup returns non-ok",
  async () => {
    let call = 0;
    requestJiraImpl = async (route) => {
      call++;
      if (String(route).includes("/rest/agile/1.0/sprint/42") && !String(route).includes("/issue")) {
        return fakeRes(true, { id: 42, name: "Sprint 42", state: "active", startDate: "2026-01-01T00:00:00.000Z" });
      }
      if (String(route).includes("/rest/agile/1.0/sprint/42/issue")) {
        return fakeRes(true, { issues: [] });
      }
      if (String(route).includes("/rest/api/3/search")) {
        return fakeRes(false, {}); // simulate unsupported JQL -> !res.ok
      }
      throw new Error(`unexpected route: ${route}`);
    };

    const result = await registeredHandlers.getSprintHealth({ payload: { sprintId: 42 } });
    assert.equal(result.scope.scopeCreepLookupDegraded, true);
  }
);

await asyncTest(
  "getSprintHealth surfaces scopeCreepLookupDegraded: true when the removed-issues lookup throws",
  async () => {
    requestJiraImpl = async (route) => {
      if (String(route).includes("/rest/agile/1.0/sprint/42") && !String(route).includes("/issue")) {
        return fakeRes(true, { id: 42, name: "Sprint 42", state: "active", startDate: "2026-01-01T00:00:00.000Z" });
      }
      if (String(route).includes("/rest/agile/1.0/sprint/42/issue")) {
        return fakeRes(true, { issues: [] });
      }
      if (String(route).includes("/rest/api/3/search")) {
        throw new Error("network error");
      }
      throw new Error(`unexpected route: ${route}`);
    };

    const result = await registeredHandlers.getSprintHealth({ payload: { sprintId: 42 } });
    assert.equal(result.scope.scopeCreepLookupDegraded, true);
  }
);

await asyncTest(
  "getSprintHealth reports scopeCreepLookupDegraded: false on a clean run with genuinely zero removed issues",
  async () => {
    requestJiraImpl = async (route) => {
      if (String(route).includes("/rest/agile/1.0/sprint/42") && !String(route).includes("/issue")) {
        return fakeRes(true, { id: 42, name: "Sprint 42", state: "active", startDate: "2026-01-01T00:00:00.000Z" });
      }
      if (String(route).includes("/rest/agile/1.0/sprint/42/issue")) {
        return fakeRes(true, { issues: [] });
      }
      if (String(route).includes("/rest/api/3/search")) {
        return fakeRes(true, { issues: [] }); // ran fine, genuinely nothing found
      }
      throw new Error(`unexpected route: ${route}`);
    };

    const result = await registeredHandlers.getSprintHealth({ payload: { sprintId: 42 } });
    assert.equal(result.scope.scopeCreepLookupDegraded, false);
  }
);

// =============================================================================
// Issue 2 — issues found via the removed-issues JQL lookup are tagged
// =============================================================================

await asyncTest(
  "getSprintHealth: an issue found only via the removed-issues JQL lookup (no changelog) is not counted as currently in sprint",
  async () => {
    requestJiraImpl = async (route) => {
      if (String(route).includes("/rest/agile/1.0/sprint/42") && !String(route).includes("/issue")) {
        return fakeRes(true, { id: 42, name: "Sprint 42", state: "active", startDate: "2026-01-01T00:00:00.000Z" });
      }
      if (String(route).includes("/rest/agile/1.0/sprint/42/issue")) {
        return fakeRes(true, { issues: [] }); // no current members
      }
      if (String(route).includes("/rest/api/3/search")) {
        return fakeRes(true, {
          issues: [
            {
              key: "P-1",
              id: "1",
              fields: { status: { name: "To Do" }, created: "2025-12-01T09:00:00.000Z" },
              // No changelog at all -> exercises the fallback branch.
            },
          ],
        });
      }
      throw new Error(`unexpected route: ${route}`);
    };

    const result = await registeredHandlers.getSprintHealth({ payload: { sprintId: 42 } });
    assert.equal(result.scope.totalIssues, 1);
    assert.equal(
      result.scope.plannedRemoved + result.scope.addedRemoved,
      1,
      "issue found only via the JQL-removed lookup must be counted as removed, not current"
    );
  }
);

console.log(`\n${passed} test(s) passed.`);
if (process.exitCode) {
  console.error("Some tests FAILED.");
  process.exit(1);
}

}

main();
