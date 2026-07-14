from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

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
