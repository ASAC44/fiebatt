from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.services.cpu_tracking import track_frames


def _moving_target_frames(tmp_path, *, count: int = 11, visible_until: int = 10):
    paths = []
    for index in range(count):
        frame = np.full((120, 200, 3), 28, dtype=np.uint8)
        if index <= visible_until:
            left = 40 + index * 5
            target = np.zeros((36, 36, 3), dtype=np.uint8)
            target[::2, ::2] = (40, 220, 240)
            target[1::2, 1::2] = (230, 60, 180)
            frame[42:78, left:left + 36] = target
        path = tmp_path / f"{index:03d}.jpg"
        assert cv2.imwrite(str(path), frame)
        paths.append(str(path))
    return paths


@pytest.mark.asyncio
async def test_cpu_tracker_follows_target_in_both_directions(tmp_path):
    paths = _moving_target_frames(tmp_path)
    result = await track_frames(
        paths,
        seed_frame_index=5,
        bbox={"x": 65 / 200, "y": 42 / 120, "w": 36 / 200, "h": 36 / 120},
    )

    states = {frame["frame_index"]: frame["state"] for frame in result.frames}
    assert result.tracker == "opencv_mil"
    assert all(states[index] == "tracked" for index in range(11))


@pytest.mark.asyncio
async def test_cpu_tracker_stops_when_target_disappears(tmp_path):
    paths = _moving_target_frames(tmp_path, visible_until=7)
    result = await track_frames(
        paths,
        seed_frame_index=5,
        bbox={"x": 65 / 200, "y": 42 / 120, "w": 36 / 200, "h": 36 / 120},
    )

    states = {frame["frame_index"]: frame["state"] for frame in result.frames}
    assert states[7] == "tracked"
    assert any(state == "lost" for index, state in states.items() if index >= 8)
