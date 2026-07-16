import base64

import httpx
import pytest

from app.ai.services import sam


@pytest.mark.asyncio
async def test_bbox_to_mask_result_preserves_worker_metadata(tmp_path, monkeypatch):
    frame_path = tmp_path / "frame.png"
    frame_path.write_bytes(b"frame")
    mask_bytes = b"mask"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sam/segment"
        return httpx.Response(
            200,
            json={
                "mask_b64": base64.b64encode(mask_bytes).decode(),
                "score": 0.93,
                "candidate_count": 3,
            },
        )

    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return original_client(
            *args,
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    result = await sam.bbox_to_mask_result(
        str(frame_path), {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}
    )

    assert result.path == str(tmp_path / "frame.mask.png")
    assert (tmp_path / "frame.mask.png").read_bytes() == mask_bytes
    assert result.score == pytest.approx(0.93)
    assert result.candidate_count == 3
