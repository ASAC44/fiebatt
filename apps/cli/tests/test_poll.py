"""Tests for iris_cli.poll — polling helpers for long-running jobs."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from iris_cli.poll import poll_job, poll_export, poll_propagation


@pytest.fixture()
def mock_client() -> MagicMock:
    """A mocked IrisClient."""
    return MagicMock()


class TestPollJob:
    """poll_job polls get_job until done/error."""

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_returns_immediately_on_done(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_job.return_value = {"status": "done", "result": "ok"}
        result = poll_job(mock_client, "job-1")
        assert result["status"] == "done"
        mock_client.get_job.assert_called_once_with("job-1")
        _sleep.assert_not_called()

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_returns_immediately_on_error(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_job.return_value = {"status": "error", "error": "failed"}
        result = poll_job(mock_client, "job-1")
        assert result["status"] == "error"
        _sleep.assert_not_called()

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_loops_until_done(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_job.side_effect = [
            {"status": "processing"},
            {"status": "processing"},
            {"status": "done", "result": "finished"},
        ]
        result = poll_job(mock_client, "job-1")
        assert result["status"] == "done"
        assert mock_client.get_job.call_count == 3
        assert _sleep.call_count == 2

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_calls_on_update_callback(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_job.side_effect = [
            {"status": "processing"},
            {"status": "done"},
        ]
        updates: list[dict[str, Any]] = []
        poll_job(mock_client, "job-1", on_update=updates.append)
        assert len(updates) == 2
        assert updates[0]["status"] == "processing"
        assert updates[1]["status"] == "done"

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_respects_interval(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_job.side_effect = [
            {"status": "processing"},
            {"status": "done"},
        ]
        poll_job(mock_client, "job-1", interval=5.0)
        _sleep.assert_called_once_with(5.0)


class TestPollExport:
    """poll_export polls get_export until done/error."""

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_returns_immediately_on_done(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_export.return_value = {"status": "done", "url": "https://example.com"}
        result = poll_export(mock_client, "exp-1")
        assert result["status"] == "done"
        mock_client.get_export.assert_called_once_with("exp-1")
        _sleep.assert_not_called()

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_returns_immediately_on_error(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_export.return_value = {"status": "error"}
        result = poll_export(mock_client, "exp-1")
        assert result["status"] == "error"

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_loops_until_done(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_export.side_effect = [
            {"status": "processing"},
            {"status": "done"},
        ]
        result = poll_export(mock_client, "exp-1")
        assert result["status"] == "done"
        assert mock_client.get_export.call_count == 2

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_calls_on_update_callback(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_export.side_effect = [
            {"status": "rendering"},
            {"status": "done"},
        ]
        updates: list[dict[str, Any]] = []
        poll_export(mock_client, "exp-1", on_update=updates.append)
        assert len(updates) == 2


class TestPollPropagation:
    """poll_propagation polls get_propagation until done/error."""

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_returns_immediately_on_done(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_propagation.return_value = {"status": "done"}
        result = poll_propagation(mock_client, "prop-1")
        assert result["status"] == "done"
        mock_client.get_propagation.assert_called_once_with("prop-1")
        _sleep.assert_not_called()

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_returns_immediately_on_error(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_propagation.return_value = {"status": "error"}
        result = poll_propagation(mock_client, "prop-1")
        assert result["status"] == "error"

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_loops_until_done(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_propagation.side_effect = [
            {"status": "processing"},
            {"status": "processing"},
            {"status": "done"},
        ]
        result = poll_propagation(mock_client, "prop-1")
        assert result["status"] == "done"
        assert mock_client.get_propagation.call_count == 3
        assert _sleep.call_count == 2

    @patch("iris_cli.poll.time.sleep", return_value=None)
    def test_calls_on_update_callback(self, _sleep: MagicMock, mock_client: MagicMock) -> None:
        mock_client.get_propagation.side_effect = [
            {"status": "propagating"},
            {"status": "done"},
        ]
        updates: list[dict[str, Any]] = []
        poll_propagation(mock_client, "prop-1", on_update=updates.append)
        assert len(updates) == 2
        assert updates[0]["status"] == "propagating"
        assert updates[1]["status"] == "done"
