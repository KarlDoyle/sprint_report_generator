"""Tests for sprint_report_generator.cli.get_config()."""

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

    config = cli.get_config()

    assert config == {
        "base_url": "https://example.atlassian.net",
        "username": "user@example.com",
        "api_token": "token-123",
        "project_key": "PROJ",
        "board_id": "7",
        "sprint_id": "42",
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
