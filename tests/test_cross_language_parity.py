"""Regression tests for the Python/JS cross-language parity issues found by
the stress-test pass: core.py (Python CLI) and forge-app/src/resolvers/
sprintHealth.js (the Forge panel) must compute identical numbers from
identical input.

Issue 1 (rounding): Python's built-in round() is round-half-to-even
("banker's rounding" — round(12.5) == 12); JS's Math.round() always rounds
.5 up (Math.round(12.5) === 13). Same formula, same inputs, different output
whenever a ratio lands exactly on N.5 with N even. The concrete repro: 1
added issue out of 8 total = 12.5% scope creep exactly.

The chosen fix direction: leave Python's round() untouched (core.py has 7
call sites all doing the same percentage-metric rounding — changing that
behavior globally would be a much bigger, less localized change than fixing
the one thing that actually needs to match). Instead, sprintHealth.js grew a
small `roundHalfEven()` helper that replicates Python's round() semantics for
exactly the metrics shared between the two languages.

This file proves Python's own behavior (unchanged) and, where Node is
available, actually runs the fixed JS code out-of-process to prove the two
languages now agree on the exact repro — not just a code-reading assertion.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import raw_sprint

from sprint_report_generator.core import JiraIssue, Sprint, SprintMetricsCalculator

FORGE_APP_DIR = Path(__file__).resolve().parents[1] / "forge-app"
SPRINT_HEALTH_JS = FORGE_APP_DIR / "src" / "resolvers" / "sprintHealth.js"

NODE = shutil.which("node")


def _planned_issue(key: str) -> JiraIssue:
    from conftest import raw_issue, sprint_field_history

    return JiraIssue(
        raw_issue(
            key=key,
            status="To Do",
            created="2025-12-01T09:00:00.000+0000",
            changelog_histories=[
                sprint_field_history(
                    created="2026-01-01T00:00:00.000+0000", from_sprint_ids=[], to_sprint_ids=[42]
                )
            ],
        )
    )


def _added_issue(key: str) -> JiraIssue:
    from conftest import raw_issue, sprint_field_history

    return JiraIssue(
        raw_issue(
            key=key,
            status="To Do",
            created="2025-12-01T09:00:00.000+0000",
            changelog_histories=[
                sprint_field_history(
                    created="2026-01-04T00:00:00.000+0000", from_sprint_ids=[], to_sprint_ids=[42]
                )
            ],
        )
    )


def test_python_round_is_banker_rounding_on_the_12_5_percent_repro() -> None:
    """The exact concrete repro from the stress-test finding: 1 added issue
    out of 8 total planned+added = 12.5% scope creep exactly. Python's
    round() must give 12 (nearest even), not 13.
    """
    assert round(12.5) == 12  # documents the semantics core.py relies on

    sprint = Sprint(raw_sprint(sprint_id=42, start_date="2026-01-01T00:00:00.000Z"))
    planned = [_planned_issue(f"P-{i}") for i in range(1, 8)]  # 7 planned
    added = [_added_issue("P-8")]  # 1 added -> 1/8 total = 12.5%

    metrics = SprintMetricsCalculator(sprint, planned + added).calculate_metrics()

    assert metrics["scope"]["total_issues"] == 8
    assert metrics["scope"]["added_total"] == 1
    assert metrics["execution_metrics"]["scope_creep_ratio"] == 12


@pytest.mark.skipif(NODE is None, reason="node not available in this environment")
@pytest.mark.skipif(not SPRINT_HEALTH_JS.exists(), reason="sprintHealth.js not found")
def test_js_round_half_even_matches_python_round_on_the_same_repro() -> None:
    """Actually executes the fixed JS (not just a code-reading check): proves
    sprintHealth.js's calculateSprintHealth() produces the same 12% on the
    identical 1-of-8 repro that Python does above, and that its roundHalfEven
    helper agrees with Python's round() on both sides of a .5 tie.
    """
    script = f"""
    const {{ roundHalfEven, calculateSprintHealth }} = require({json.dumps(str(SPRINT_HEALTH_JS))});

    const results = {{}};
    results.roundHalfEven_12_5 = roundHalfEven(12.5);
    results.roundHalfEven_87_5 = roundHalfEven(87.5);
    results.mathRound_12_5 = Math.round(12.5); // documents the original JS bug

    function sprintFieldHistory(created, fromIds, toIds) {{
      return {{
        created,
        items: [{{
          field: "Sprint",
          from: fromIds.join(","),
          to: toIds.join(","),
        }}],
      }};
    }}

    const sprint = {{ id: 42, name: "Sprint 42", state: "active", goal: "" }};
    const sprintStart = new Date("2026-01-01T00:00:00.000Z");
    const planned = Array.from({{ length: 7 }}, (_, i) => ({{
      key: `P-${{i + 1}}`,
      fields: {{ status: {{ name: "To Do" }}, created: "2025-12-01T09:00:00.000Z" }},
      changelog: {{ histories: [sprintFieldHistory("2026-01-01T00:00:00.000Z", [], [42])] }},
    }}));
    const added = {{
      key: "P-8",
      fields: {{ status: {{ name: "To Do" }}, created: "2025-12-01T09:00:00.000Z" }},
      changelog: {{ histories: [sprintFieldHistory("2026-01-04T00:00:00.000Z", [], [42])] }},
    }};

    const health = calculateSprintHealth(sprint, [...planned, added], sprintStart);
    results.jsScopeCreepRatio = health.executionMetrics.scopeCreepRatio;

    console.log(JSON.stringify(results));
    """

    proc = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, cwd=str(FORGE_APP_DIR), timeout=30
    )
    assert proc.returncode == 0, f"node script failed: {proc.stderr}"
    results = json.loads(proc.stdout.strip().splitlines()[-1])

    # Python round(12.5) == 12; roundHalfEven must match it.
    assert results["roundHalfEven_12_5"] == 12
    assert results["roundHalfEven_87_5"] == 88
    # Documents the bug this replaces: naive Math.round() disagreed with Python.
    assert results["mathRound_12_5"] == 13

    # The actual cross-language assertion: same input, same output, both languages.
    python_scope_creep_ratio = round(1 / 8 * 100)
    assert results["jsScopeCreepRatio"] == python_scope_creep_ratio == 12
