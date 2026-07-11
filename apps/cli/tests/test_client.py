"""Tests for iris_cli.client — IrisClient construction, headers, URL building, and HTTP dispatch."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from iris_cli.client import IrisClient


@pytest.fixture()
def client() -> IrisClient:
    """A default IrisClient for tests."""
    return IrisClient(
        base_url="http://localhost:8000",
        session_id="test-session-id",
        token="test-token",
    )


@pytest.fixture()
def client_no_token() -> IrisClient:
    """An IrisClient without a token."""
    return IrisClient(
        base_url="http://localhost:8000",
        session_id="test-session-id",
        token=None,
    )


class TestConstructor:
    """IrisClient constructor stores fields correctly."""

    def test_sets_base_url(self, client: IrisClient) -> None:
        assert client.base_url == "http://localhost:8000"

    def test_strips_trailing_slash(self) -> None:
        c = IrisClient(base_url="http://localhost:8000/", session_id="s", token=None)
        assert c.base_url == "http://localhost:8000"

    def test_sets_session_id(self, client: IrisClient) -> None:
        assert client.session_id == "test-session-id"

    def test_sets_token(self, client: IrisClient) -> None:
        assert client.token == "test-token"


class TestHeaders:
    """_headers() includes the right authorization and session headers."""

    def test_includes_session_id(self, client: IrisClient) -> None:
        headers = client._headers()
        assert headers["X-Session-Id"] == "test-session-id"

    def test_includes_authorization_when_token_set(self, client: IrisClient) -> None:
        headers = client._headers()
        assert headers["Authorization"] == "Bearer test-token"

    def test_omits_authorization_when_token_none(self, client_no_token: IrisClient) -> None:
        headers = client_no_token._headers()
        assert "Authorization" not in headers

    def test_omits_authorization_when_token_empty(self) -> None:
        c = IrisClient(base_url="http://localhost:8000", session_id="s", token="")
        headers = c._headers()
        assert "Authorization" not in headers


class TestUrl:
    """_url() constructs the correct full URL."""

    def test_simple_path(self, client: IrisClient) -> None:
        assert client._url("/projects") == "http://localhost:8000/api/projects"

    def test_nested_path(self, client: IrisClient) -> None:
        assert client._url("/projects/abc123") == "http://localhost:8000/api/projects/abc123"

    def test_health_path(self, client: IrisClient) -> None:
        assert client._url("/health") == "http://localhost:8000/api/health"


class TestHttpMethods:
    """Each API method calls _request with the correct HTTP method and path."""

    @pytest.mark.parametrize(
        "method_name,call_kwargs,expected_http_method,expected_path",
        [
            ("list_projects", {}, "GET", "/projects"),
            ("get_project", {"project_id": "p1"}, "GET", "/projects/p1"),
            ("get_job", {"job_id": "j1"}, "GET", "/jobs/j1"),
            ("health", {}, "GET", "/health"),
            ("get_entity", {"entity_id": "e1"}, "GET", "/entities/e1"),
            ("get_timeline", {"project_id": "p1"}, "GET", "/timeline/p1"),
            ("get_export", {"export_job_id": "ex1"}, "GET", "/export/ex1"),
            ("get_propagation", {"propagation_job_id": "pp1"}, "GET", "/propagate/pp1"),
        ],
    )
    def test_get_methods(
        self,
        client: IrisClient,
        method_name: str,
        call_kwargs: dict[str, Any],
        expected_http_method: str,
        expected_path: str,
    ) -> None:
        with patch.object(client, "_request", return_value={}) as mock_req:
            getattr(client, method_name)(**call_kwargs)
            mock_req.assert_called_once()
            args = mock_req.call_args
            assert args[0][0] == expected_http_method
            assert args[0][1] == expected_path

    @pytest.mark.parametrize(
        "method_name,call_kwargs,expected_path",
        [
            (
                "generate",
                {
                    "project_id": "p1",
                    "start_ts": 0.0,
                    "end_ts": 1.0,
                    "bbox": {"x": 0, "y": 0, "w": 1, "h": 1},
                    "prompt": "test",
                },
                "/generate",
            ),
            (
                "accept",
                {"job_id": "j1", "variant_index": 0},
                "/accept",
            ),
            (
                "identify",
                {
                    "project_id": "p1",
                    "frame_ts": 0.5,
                    "bbox": {"x": 0, "y": 0, "w": 1, "h": 1},
                },
                "/identify",
            ),
            (
                "mask",
                {
                    "project_id": "p1",
                    "frame_ts": 0.5,
                    "bbox": {"x": 0, "y": 0, "w": 1, "h": 1},
                },
                "/mask",
            ),
            (
                "export_video",
                {"project_id": "p1"},
                "/export",
            ),
            (
                "narrate",
                {"variant_id": "v1"},
                "/narrate",
            ),
        ],
    )
    def test_post_methods(
        self,
        client: IrisClient,
        method_name: str,
        call_kwargs: dict[str, Any],
        expected_path: str,
    ) -> None:
        with patch.object(client, "_request", return_value={}) as mock_req:
            getattr(client, method_name)(**call_kwargs)
            mock_req.assert_called_once()
            args = mock_req.call_args
            assert args[0][0] == "POST"
            assert args[0][1] == expected_path

    def test_generate_includes_reference_frame(self, client: IrisClient) -> None:
        with patch.object(client, "_request", return_value={}) as mock_req:
            client.generate(
                project_id="p1",
                start_ts=0.0,
                end_ts=1.0,
                bbox={"x": 0, "y": 0, "w": 1, "h": 1},
                prompt="test",
                reference_frame_ts=0.5,
            )
            body = mock_req.call_args[1]["json"]
            assert body["reference_frame_ts"] == 0.5

    def test_generate_omits_reference_frame_when_none(self, client: IrisClient) -> None:
        with patch.object(client, "_request", return_value={}) as mock_req:
            client.generate(
                project_id="p1",
                start_ts=0.0,
                end_ts=1.0,
                bbox={"x": 0, "y": 0, "w": 1, "h": 1},
                prompt="test",
                reference_frame_ts=None,
            )
            body = mock_req.call_args[1]["json"]
            assert "reference_frame_ts" not in body
