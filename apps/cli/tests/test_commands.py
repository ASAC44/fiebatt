"""Tests for CLI command wiring via typer.testing.CliRunner."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from iris_cli.main import app

runner = CliRunner()


class TestHelpOutput:
    """Basic CLI help and wiring tests."""

    def test_iris_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "iris" in result.output.lower()

    def test_generate_help_shows_options(self) -> None:
        result = runner.invoke(app, ["generate", "--help"])
        assert result.exit_code == 0
        assert "--project" in result.output
        assert "--start" in result.output
        assert "--end" in result.output
        assert "--bbox" in result.output
        assert "--prompt" in result.output

    def test_auth_help(self) -> None:
        result = runner.invoke(app, ["auth", "--help"])
        assert result.exit_code == 0

    def test_projects_help(self) -> None:
        result = runner.invoke(app, ["projects", "--help"])
        assert result.exit_code == 0


class TestAuthStatus:
    """iris auth status wiring."""

    @patch("iris_cli.commands.auth.IrisClient")
    @patch("iris_cli.commands.auth.get_client_kwargs")
    @patch("iris_cli.commands.auth.load_config")
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
    """iris projects wiring."""

    @patch("iris_cli.commands.projects.IrisClient")
    @patch("iris_cli.commands.projects.get_client_kwargs")
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


class TestJsonFlag:
    """--json flag sets output format."""

    @patch("iris_cli.commands.projects.IrisClient")
    @patch("iris_cli.commands.projects.get_client_kwargs")
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
