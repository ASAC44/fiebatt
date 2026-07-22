from pathlib import Path

import cv2
import numpy as np

from app.ai.services.wan import (
    DEFAULT_I2V_MODEL,
    _build_image_to_video_payload,
)


def _write_frame(path: Path, color: tuple[int, int, int]) -> None:
    frame = np.full((360, 640, 3), color, dtype=np.uint8)
    assert cv2.imwrite(str(path), frame)


def test_i2v_payload_uses_full_first_and_last_frames(tmp_path: Path):
    first = tmp_path / "first.jpg"
    last = tmp_path / "last.jpg"
    _write_frame(first, (10, 20, 30))
    _write_frame(last, (30, 20, 10))

    payload = _build_image_to_video_payload(
        "the car bounces once and lands naturally",
        str(first),
        last_frame_path=str(last),
        duration=6,
        resolution="720P",
    )

    assert DEFAULT_I2V_MODEL == "wan2.7-i2v-2026-04-25"
    assert [item["type"] for item in payload["input"]["media"]] == [
        "first_frame",
        "last_frame",
    ]
    assert all(
        item["url"].startswith("data:image/jpeg;base64,")
        for item in payload["input"]["media"]
    )
    assert payload["parameters"] == {
        "resolution": "720P",
        "duration": 6,
        "prompt_extend": False,
        "watermark": False,
    }


def test_i2v_payload_can_leave_future_motion_open(tmp_path: Path):
    first = tmp_path / "first.jpg"
    _write_frame(first, (10, 20, 30))

    payload = _build_image_to_video_payload(
        "the person starts running",
        str(first),
        duration=5,
    )

    assert [item["type"] for item in payload["input"]["media"]] == ["first_frame"]
