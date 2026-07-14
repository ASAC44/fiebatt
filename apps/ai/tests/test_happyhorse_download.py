from pathlib import Path

import httpx
import pytest

from ai.services import happyhorse


@pytest.mark.asyncio
async def test_download_retries_transient_connect_timeout(monkeypatch, tmp_path: Path):
    attempts = 0

    class Response:
        content = b"generated-video"

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectTimeout("")
            return Response()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(happyhorse.httpx, "AsyncClient", Client)
    monkeypatch.setattr(happyhorse.asyncio, "sleep", no_sleep)

    output = tmp_path / "result.mp4"
    result = await happyhorse._download_video("https://cdn.example/result.mp4", output)

    assert attempts == 3
    assert result == output
    assert output.read_bytes() == b"generated-video"


@pytest.mark.asyncio
async def test_download_surfaces_exception_type_when_message_is_empty(monkeypatch, tmp_path: Path):
    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url):
            raise httpx.ConnectTimeout("")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(happyhorse.httpx, "AsyncClient", Client)
    monkeypatch.setattr(happyhorse.asyncio, "sleep", no_sleep)

    with pytest.raises(RuntimeError, match="ConnectTimeout"):
        await happyhorse._download_video(
            "https://cdn.example/result.mp4",
            tmp_path / "result.mp4",
        )
