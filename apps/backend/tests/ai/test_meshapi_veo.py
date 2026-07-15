from types import SimpleNamespace

import pytest

from app.ai.services import meshapi_veo


def test_build_payload_matches_mesh_video_contract(tmp_path):
    reference = tmp_path / "first-frame.png"
    reference.write_bytes(b"png-bytes")

    payload = meshapi_veo._build_payload(
        model="google/veo-3",
        prompt="The subject turns toward camera",
        reference_frame_path=str(reference),
        duration=5,
        aspect_ratio="16:9",
        resolution="720P",
    )

    assert payload == {
        "model": "google/veo-3",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,cG5nLWJ5dGVz"},
            },
            {"type": "text", "text": "The subject turns toward camera"},
        ],
        "duration": 5,
        "ratio": "16:9",
        "resolution": "720p",
        "generate_audio": False,
    }
    assert "prompt" not in payload
    assert "aspect_ratio" not in payload
    assert "reference_image" not in payload


def test_extracts_mesh_succeeded_video_url():
    payload = {
        "id": "t-123",
        "status": "succeeded",
        "content": {"video_url": "https://cdn.example.test/result.mp4"},
    }

    assert meshapi_veo._extract_video_url(payload) == "https://cdn.example.test/result.mp4"


def test_generation_endpoint_uses_singular_video_path(monkeypatch):
    settings = SimpleNamespace(
        mesh_api_base_url="https://api.meshapi.ai/v1",
        mesh_video_endpoint="/video/generations",
    )
    monkeypatch.setattr(meshapi_veo, "get_settings", lambda: settings)

    assert meshapi_veo._generation_endpoint() == (
        "https://api.meshapi.ai/v1/video/generations"
    )
    assert meshapi_veo._generation_endpoint("t-123") == (
        "https://api.meshapi.ai/v1/video/generations/t-123"
    )


@pytest.mark.asyncio
async def test_expired_mesh_task_is_terminal(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "t-expired",
                "status": "expired",
                "error": {"code": "deadline", "message": "task expired"},
            }

    class Client:
        async def get(self, *args, **kwargs):
            return Response()

    settings = SimpleNamespace(
        mesh_api_base_url="https://api.meshapi.ai/v1",
        mesh_video_endpoint="/video/generations",
        mesh_api_key="rsk_test",
        video_generation_timeout=60,
    )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(meshapi_veo, "get_settings", lambda: settings)
    monkeypatch.setattr(meshapi_veo.asyncio, "sleep", no_sleep)

    with pytest.raises(RuntimeError, match="status expired"):
        await meshapi_veo._poll_for_video(Client(), "t-expired", on_tick=None)
