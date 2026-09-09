/**
 * Regression tests for index.js's fetchRemovedSprintIssues() — specifically
 * Issue 3 (a failed "removed from sprint" JQL lookup must be surfaced as
 * `degraded: true`, not silently swallowed) and the Issue 2 `_removedViaJql`
 * tagging it's responsible for.
 *
 * index.js requires @forge/resolver and @forge/api, which aren't installed
 * in this scaffold (no node_modules — Forge apps run in Forge's managed
 * runtime, not a local install). We stub both via Module._load before
 * requiring index.js, then drive fetchRemovedSprintIssues by calling the
 * resolver function registered for "getSprintHealth" through our fake
 * `api.asApp().requestJira()`.
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
    asApp: () => ({
      requestJira: (...args) => requestJiraImpl(...args),
    }),
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
  return { ok, json: async () => body };
}

async function main() {

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
