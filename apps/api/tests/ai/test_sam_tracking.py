import base64
from types import SimpleNamespace

import httpx
import pytest

from app.ai.services import sam


@pytest.mark.asyncio
async def test_track_frames_sends_bounded_tracking_contract(tmp_path, monkeypatch):
    frames = []
    for index in range(3):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(f"frame-{index}".encode())
        frames.append(str(path))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "tracking.example.test"
        assert request.url.path == "/sam/track"
        body = __import__("json").loads(request.content)
        assert base64.b64decode(body["frames_b64"][1]) == b"frame-1"
        assert body["seed_frame_index"] == 1
        assert body["max_frames"] == 3
        return httpx.Response(
            200,
            json={
                "tracker": "sam2_video",
                "processed_start_index": 0,
                "processed_end_index": 2,
                "cancelled": False,
                "frames": [
                    {"frame_index": 1, "bbox": body["bbox"], "confidence": 0.91, "state": "tracked"}
                ],
            },
        )

    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(
        sam,
        "get_settings",
        lambda: SimpleNamespace(
            vision_worker_url="https://tracking.example.test",
            sam_segmentation_url="https://segment.hf.space",
        ),
    )
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    result = await sam.track_frames(
        frames,
        seed_frame_index=1,
        bbox={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
        max_frames=3,
    )

    assert result.tracker == "sam2_video"
    assert result.processed_end_index == 2
    assert result.frames[0]["state"] == "tracked"


@pytest.mark.asyncio
async def test_huggingface_override_is_only_used_for_segmentation(tmp_path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    captured: dict = {}

    monkeypatch.setattr(
        sam,
        "get_settings",
        lambda: SimpleNamespace(
            vision_worker_url="https://tracking.example.test",
            sam_segmentation_url="https://segment.hf.space",
        ),
    )

    def segment(worker_url: str, payload: dict) -> dict:
        captured.update(worker_url=worker_url, payload=payload)
        return {"mask_b64": base64.b64encode(b"mask").decode(), "score": 0.9}

    monkeypatch.setattr(sam, "_call_huggingface_space", segment)
    result = await sam.bbox_to_mask_result(
        str(frame),
        {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
    )

    assert captured["worker_url"] == "https://segment.hf.space"
    assert base64.b64decode(captured["payload"]["image_b64"]) == b"frame"
    assert result.score == 0.9
    assert (tmp_path / "frame.mask.png").read_bytes() == b"mask"


@pytest.mark.asyncio
async def test_huggingface_space_is_not_treated_as_video_tracker(monkeypatch):
    monkeypatch.setattr(
        sam,
        "get_settings",
        lambda: SimpleNamespace(vision_worker_url="https://segment.hf.space"),
    )

    assert await sam.video_tracking_available() is False
