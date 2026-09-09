/**
 * Sprint Health Report panel — UI Kit 2 (@forge/react). Renders inside a Jira
 * Software project's sidebar (jira:projectPage module, see ../../manifest.yml).
 *
 * V1 scope, deliberately: the user enters a sprint ID manually. jira:projectPage
 * doesn't hand you a "current sprint" in its context the way an issue panel
 * gets an issue, so auto-detecting the active sprint means an extra resolver
 * call to list the project's boards (`/rest/agile/1.0/board?projectKeyOrId=`)
 * and that board's active sprint(s) (`/rest/agile/1.0/board/{id}/sprint?state=active`).
 * Worth adding once this is actually deployed and the UX is validated — not
 * done here to keep the scaffold small and honest about what's built vs. not.
 */

import React, { useState } from "react";
import ForgeReconciler, {
  Box,
  Button,
  Heading,
  Inline,
  SectionMessage,
  Spinner,
  Stack,
  Text,
  Textfield,
} from "@forge/react";
import { invoke } from "@forge/bridge";

const App = () => {
  const [sprintId, setSprintId] = useState("");
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadReport = async () => {
    if (!sprintId) return;
    setLoading(true);
    setError(null);
    setHealth(null);
    try {
      const result = await invoke("getSprintHealth", { sprintId: Number(sprintId) });
      setHealth(result);
    } catch (e) {
      setError(e.message ?? String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Stack space="space.200">
      <Heading size="medium">Sprint Health Report</Heading>
      <Text>
        For observation and awareness — not a performance verdict. Sprint-membership
        timing comes from Jira's own changelog, not a guess from issue creation dates.
      </Text>

      <Inline space="space.100" alignBlock="center">
        <Textfield
          placeholder="Sprint ID"
          value={sprintId}
          onChange={(e) => setSprintId(e.target.value)}
        />
        <Button appearance="primary" onClick={loadReport} isDisabled={loading}>
          Load report
        </Button>
      </Inline>

      {loading && <Spinner size="medium" />}

      {error && (
        <SectionMessage appearance="error" title="Couldn't load sprint health">
          <Text>{error}</Text>
        </SectionMessage>
      )}

      {health && (
        <Stack space="space.150">
          <Text>
            <Text weight="bold">{health.sprint.name}</Text> ({health.sprint.state})
          </Text>

          {health.scope.scopeCreepLookupDegraded && (
            <SectionMessage appearance="warning" title="Scope-creep lookup degraded">
              <Text>
                The "removed from sprint" JQL lookup failed on this Jira instance — removed /
                scope-creep counts below may undercount. Treat 0 removed as "couldn't check",
                not a confirmed zero.
              </Text>
            </SectionMessage>
          )}

          <Box>
            <Text>
              Planned: {health.scope.plannedCompleted}/{health.scope.plannedTotal} completed
              {health.scope.plannedRemoved > 0
                ? ` (${health.scope.plannedRemoved} removed mid-sprint)`
                : ""}
            </Text>
            <Text>
              Added mid-sprint: {health.scope.addedCompleted}/{health.scope.addedTotal} completed
              {health.scope.addedRemoved > 0
                ? ` (${health.scope.addedRemoved} removed after being added)`
                : ""}
            </Text>
            <Text>
              Total: {health.scope.totalCompleted}/{health.scope.totalIssues} completed
            </Text>
            <Text>
              Completed outside sprint: {health.scope.completedOutsideSprintTotal} (
              {health.scope.completedOutsideSprintPlanned} planned,{" "}
              {health.scope.completedOutsideSprintAdded} added mid-sprint)
            </Text>
          </Box>

          <Box>
            <Text weight="bold">
              Planned completion rate: {health.executionMetrics.plannedCompletionRate}%
            </Text>
            <Text weight="bold">
              Added completion rate: {health.executionMetrics.addedCompletionRate}%
            </Text>
            <Text weight="bold">
              Execution focus ratio: {health.executionMetrics.executionFocusRatio}%
            </Text>
            <Text weight="bold">
              Scope creep ratio: {health.executionMetrics.scopeCreepRatio}%
            </Text>
          </Box>
        </Stack>
      )}
    </Stack>
  );
};

ForgeReconciler.render(<App />);
