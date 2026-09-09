"""Tests for sprint_report_generator.cli: get_config(), parse_team_sprints(), and
main()'s single-sprint vs. multi-team dispatch.
"""

from __future__ import annotations

import pytest

from sprint_report_generator import cli


def _clear_jira_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "JIRA_BASE_URL",
        "JIRA_USERNAME",
        "JIRA_API_TOKEN",
        "JIRA_PROJECT_KEY",
        "JIRA_BOARD_ID",
        "JIRA_SPRINT_ID",
        "JIRA_TEAM_SPRINTS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_get_config_exits_when_required_vars_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_jira_env(monkeypatch)
    with pytest.raises(SystemExit) as exc_info:
        cli.get_config()
    assert exc_info.value.code == 1


def test_get_config_reads_all_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_jira_env(monkeypatch)
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_USERNAME", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token-123")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")
    monkeypatch.setenv("JIRA_BOARD_ID", "7")
    monkeypatch.setenv("JIRA_SPRINT_ID", "42")
    monkeypatch.setenv("JIRA_TEAM_SPRINTS", "Payments:101,Platform:102")

    config = cli.get_config()

    assert config == {
        "base_url": "https://example.atlassian.net",
        "username": "user@example.com",
        "api_token": "token-123",
        "project_key": "PROJ",
        "board_id": "7",
        "sprint_id": "42",
        "team_sprints": "Payments:101,Platform:102",
    }


def test_get_config_succeeds_with_only_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_jira_env(monkeypatch)
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_USERNAME", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token-123")

    config = cli.get_config()

    assert config["base_url"] == "https://example.atlassian.net"
    assert config["project_key"] is None
    assert config["sprint_id"] is None
    assert config["team_sprints"] is None


def test_parse_team_sprints_parses_repeatable_name_colon_id_pairs() -> None:
    result = cli.parse_team_sprints(["Payments:101", "Platform:102"])
    assert result == {"Payments": 101, "Platform": 102}


def test_parse_team_sprints_rejects_missing_colon() -> None:
    with pytest.raises(ValueError, match="Invalid --team value"):
        cli.parse_team_sprints(["Payments101"])


def test_parse_team_sprints_rejects_non_numeric_sprint_id() -> None:
    with pytest.raises(ValueError, match="sprint ID must be a number"):
        cli.parse_team_sprints(["Payments:not-a-number"])


def test_parse_team_sprints_rejects_empty_team_name() -> None:
    with pytest.raises(ValueError, match="missing team name"):
        cli.parse_team_sprints([":101"])


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_USERNAME", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token-123")


def test_main_dispatches_to_multi_team_rollup_when_team_flags_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """--team should route main() to generate_multi_team_report(), not the
    single-sprint generate_report() path — verified by call recording on a
    stub generator rather than hitting real Jira.
    """
    _clear_jira_env(monkeypatch)
    _set_required_env(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        ["srg", "--team", "Payments:101", "--team", "Platform:102", "--output-dir", str(tmp_path)],
    )

    calls = {}

    class _StubGenerator:
        def __init__(self, jira_client):
            pass

        def generate_multi_team_report(self, team_sprint_ids, output_dir):
            calls["team_sprint_ids"] = team_sprint_ids
            calls["output_dir"] = output_dir
            return {
                "teams": [],
                "rollup": {
                    "team_count": 2,
                    "totals": {"total_completed": 0, "total_issues": 0},
                    "execution_focus_ratio": 0,
                    "scope_creep_ratio": 0,
                },
                "output_directory": str(output_dir),
            }

        def generate_report(self, *args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("single-sprint generate_report() should not run in --team mode")

    monkeypatch.setattr(cli, "JiraClient", lambda **kwargs: object())
    monkeypatch.setattr(cli, "SprintReportGenerator", _StubGenerator)

    cli.main()

    assert calls["team_sprint_ids"] == {"Payments": 101, "Platform": 102}


def test_main_rejects_malformed_team_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    _clear_jira_env(monkeypatch)
    _set_required_env(monkeypatch)
    monkeypatch.setattr("sys.argv", ["srg", "--team", "not-valid", "--output-dir", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "Invalid --team value" in capsys.readouterr().out
