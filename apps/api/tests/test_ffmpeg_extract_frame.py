from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import cv2

from app.services import ffmpeg


def test_extract_frame_near_container_end(tmp_path: Path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "frame.jpg"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi",
            "-i", "testsrc2=s=128x72:r=30000/1001:d=1.0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(source),
        ],
        check=True,
        timeout=10,
    )

    result = asyncio.run(ffmpeg.extract_frame(source, 0.999, output))

    assert result == output
    assert output.stat().st_size > 0


def test_extract_sampled_frames_decodes_window_once(tmp_path: Path):
    source = tmp_path / "source.mp4"
    output_dir = tmp_path / "analysis"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi",
            "-i", "testsrc2=s=1280x720:r=24:d=3.0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(source),
        ],
        check=True,
        timeout=10,
    )

    paths, timestamps = asyncio.run(
        ffmpeg.extract_sampled_frames(
            source,
            start_ts=0.5,
            end_ts=2.5,
            fps=4.0,
            output_dir=output_dir,
        )
    )

    assert 8 <= len(paths) <= 10
    assert len(paths) == len(timestamps)
    frame = cv2.imread(paths[0])
    assert frame is not None
    assert frame.shape[1] == 640
