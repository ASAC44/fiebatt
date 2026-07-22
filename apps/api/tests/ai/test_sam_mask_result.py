import base64

import httpx
import cv2
import numpy as np
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


def test_mask_geometry_gate_removes_disconnected_debris(tmp_path):
    frame_path = tmp_path / "frame.png"
    mask_path = tmp_path / "mask.png"
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[25:55, 25:55] = 255
    mask[80:92, 80:92] = 255
    assert cv2.imwrite(str(frame_path), frame)
    assert cv2.imwrite(str(mask_path), mask)

    metrics = sam._clean_mask_for_bbox(
        str(frame_path),
        str(mask_path),
        {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4},
        confidence=0.9,
    )

    cleaned = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    assert cleaned is not None
    assert cleaned[30, 30] == 255
    assert cleaned[85, 85] == 0
    assert metrics["components_removed"] == 1


def test_mask_geometry_gate_rejects_unrelated_component(tmp_path):
    frame_path = tmp_path / "frame.png"
    mask_path = tmp_path / "mask.png"
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[70:90, 70:90] = 255
    assert cv2.imwrite(str(frame_path), frame)
    assert cv2.imwrite(str(mask_path), mask)

    with pytest.raises(sam.UnusableMaskError, match="does not overlap"):
        sam._clean_mask_for_bbox(
            str(frame_path),
            str(mask_path),
            {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3},
            confidence=0.9,
        )


def test_mask_geometry_gate_rejects_live_fragment_pattern(tmp_path):
    frame_path = tmp_path / "frame.png"
    mask_path = tmp_path / "mask.png"
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=np.uint8)
    # Largest component covers roughly 3% of the 40x40 prompt box and misses
    # its center. Eighteen extra specks reproduce the production failure.
    mask[22:29, 22:29] = 255
    for index in range(18):
        x = 60 + (index % 6) * 5
        y = 5 + (index // 6) * 5
        mask[y:y + 2, x:x + 2] = 255
    assert cv2.imwrite(str(frame_path), frame)
    assert cv2.imwrite(str(mask_path), mask)

    with pytest.raises(sam.UnusableMaskError, match="covers too little"):
        sam._clean_mask_for_bbox(
            str(frame_path),
            str(mask_path),
            {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4},
            confidence=0.9,
        )
