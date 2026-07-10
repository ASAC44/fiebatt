"""Tests for ffmpeg utilities. These run locally, no API keys needed."""

import os
import subprocess
import pytest
from pathlib import Path

from ai.services.ffmpeg import (
    get_video_info,
    extract_frame,
    extract_keyframes,
    crop_bbox_from_frame,
    sample_frames_from_clip,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_VIDEO = str(FIXTURES_DIR / "test_clip.mp4")


@pytest.fixture(autouse=True)
def ensure_test_video():
    """Generate a 5-second test video if it doesn't exist."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    if not Path(TEST_VIDEO).exists():
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i",
                "testsrc=duration=5:size=640x360:rate=24",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                TEST_VIDEO,
            ],
            capture_output=True,
            check=True,
        )
    yield


def test_get_video_info():
    info = get_video_info(TEST_VIDEO)
    assert info["duration"] == pytest.approx(5.0, abs=0.5)
    assert info["fps"] == pytest.approx(24.0, abs=1.0)
    assert info["width"] == 640
    assert info["height"] == 360


def test_extract_frame(tmp_path):
    out = str(tmp_path / "frame.png")
    result = extract_frame(TEST_VIDEO, 2.0, out)
    assert Path(result).exists()
    assert Path(result).stat().st_size > 0


def test_extract_keyframes(tmp_path):
    out_dir = str(tmp_path / "keyframes")
    paths = extract_keyframes(TEST_VIDEO, out_dir, fps=1.0)
    # 5 second video at 1fps = 5 keyframes
    assert len(paths) >= 4
    assert all(Path(p).exists() for p in paths)


def test_crop_bbox_from_frame(tmp_path):
    frame_path = str(tmp_path / "frame.png")
    extract_frame(TEST_VIDEO, 1.0, frame_path)

    crop_path = crop_bbox_from_frame(
        frame_path,
        {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
    )
    assert Path(crop_path).exists()
    assert Path(crop_path).stat().st_size > 0


def test_sample_frames_from_clip(tmp_path):
    paths = sample_frames_from_clip(TEST_VIDEO, num_frames=3)
    assert len(paths) == 3
    assert all(Path(p).exists() for p in paths)
