"""Tests for CLI command wiring via typer.testing.CliRunner."""

import re
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from fiebatt_cli.main import app

runner = CliRunner()
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class TestHelpOutput:
    """Basic CLI help and wiring tests."""

    def test_fiebatt_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "fiebatt" in result.output.lower()

    def test_generate_help_shows_options(self) -> None:
        result = runner.invoke(app, ["edits", "generate", "--help"])
        assert result.exit_code == 0
        output = ANSI_ESCAPE.sub("", result.output)
        assert "--project" in output
        assert "--start" in output
        assert "--end" in output
        assert "--bbox" in output
        assert "--prompt" in output

    def test_auth_help(self) -> None:
        result = runner.invoke(app, ["auth", "--help"])
        assert result.exit_code == 0

    def test_projects_help(self) -> None:
        result = runner.invoke(app, ["projects", "--help"])
        assert result.exit_code == 0

    def test_help_organizes_commands_by_resource(self) -> None:
        result = runner.invoke(app, ["--help"])
        output = ANSI_ESCAPE.sub("", result.output)

        assert result.exit_code == 0
        assert "projects" in output
        assert "edits" in output
        assert "jobs" in output
        assert "entities" in output
        assert "batch" in output
        assert "batch-generate" not in output


class TestAuthStatus:
    """fiebatt auth status wiring."""

    @patch("fiebatt_cli.commands.auth.FiebattClient")
    @patch("fiebatt_cli.commands.auth.get_client_kwargs")
    @patch("fiebatt_cli.commands.auth.load_config")
    def test_auth_status_wiring(
        self,
        mock_load: MagicMock,
        mock_kwargs: MagicMock,
        mock_client_cls: MagicMock,
    ) -> None:
        mock_load.return_value = {
            "session_id": "sess-123",
            "base_url": "http://localhost:8000",
            "token": None,
        }
        mock_kwargs.return_value = {
            "base_url": "http://localhost:8000",
            "session_id": "sess-123",
            "token": None,
        }
        mock_client_cls.return_value.health.return_value = {"status": "ok"}

        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0
        mock_client_cls.return_value.health.assert_called_once()


class TestProjectsCommand:
    """fiebatt projects wiring."""

    @patch("fiebatt_cli.commands.projects.FiebattClient")
    @patch("fiebatt_cli.commands.projects.get_client_kwargs")
    def test_projects_lists(
        self,
        mock_kwargs: MagicMock,
        mock_client_cls: MagicMock,
    ) -> None:
        mock_kwargs.return_value = {
            "base_url": "http://localhost:8000",
            "session_id": "sess-123",
            "token": None,
        }
        mock_client_cls.return_value.list_projects.return_value = [
            {"id": "p1", "video_url": "v.mp4", "duration": 10, "fps": 30, "width": 1920, "height": 1080},
        ]

        result = runner.invoke(app, ["projects"])
        assert result.exit_code == 0
        mock_client_cls.return_value.list_projects.assert_called_once()

    @patch("fiebatt_cli.commands.projects.FiebattClient")
    @patch("fiebatt_cli.commands.projects.get_client_kwargs")
    def test_projects_get(
        self,
        mock_kwargs: MagicMock,
        mock_client_cls: MagicMock,
    ) -> None:
        mock_kwargs.return_value = {
            "base_url": "http://localhost:8000",
            "session_id": "sess-123",
            "token": None,
        }
        mock_client_cls.return_value.get_project.return_value = {"id": "p1"}

        result = runner.invoke(app, ["projects", "get", "p1"])
        assert result.exit_code == 0
        mock_client_cls.return_value.get_project.assert_called_once_with("p1")


class TestCompatibilityAliases:
    """Legacy flat commands continue to work but stay out of top-level help."""

    def test_legacy_generate_alias(self) -> None:
        result = runner.invoke(app, ["generate", "--help"])
        assert result.exit_code == 0


class TestJsonFlag:
    """--json flag sets output format."""

    @patch("fiebatt_cli.commands.projects.FiebattClient")
    @patch("fiebatt_cli.commands.projects.get_client_kwargs")
    def test_json_flag_sets_format(
        self,
        mock_kwargs: MagicMock,
        mock_client_cls: MagicMock,
    ) -> None:
        mock_kwargs.return_value = {
            "base_url": "http://localhost:8000",
            "session_id": "s",
            "token": None,
        }
        mock_client_cls.return_value.list_projects.return_value = []

        result = runner.invoke(app, ["--json", "projects"])
        assert result.exit_code == 0
