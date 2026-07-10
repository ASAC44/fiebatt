from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, check=True, capture_output=True, timeout=10)


def _make_color_clip(path: Path, color: str) -> None:
    _run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=64x64:r=10:d=1.0",
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", "1.0",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(path),
    ])


def _average_rgb_at(path: Path, ts: float) -> tuple[float, float, float]:
    result = _run([
        "ffmpeg", "-v", "error",
        "-ss", f"{ts:.3f}",
        "-i", str(path),
        "-frames:v", "1",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "pipe:1",
    ])
    stdout = result.stdout
    pixels = len(stdout) // 3
    assert pixels > 0
    red = sum(stdout[0::3]) / pixels
    green = sum(stdout[1::3]) / pixels
    blue = sum(stdout[2::3]) / pixels
    return red, green, blue


def test_concat_clips_xfade_outputs_blended_frames(tmp_path):
    red = tmp_path / "red.mp4"
    blue = tmp_path / "blue.mp4"
    out = tmp_path / "xfade.mp4"

    _make_color_clip(red, "red")
    _make_color_clip(blue, "blue")
    script = (
        "import asyncio; "
        "from pathlib import Path; "
        "from app.services import ffmpeg; "
        f"asyncio.run(ffmpeg.concat_clips([Path({str(red)!r}), Path({str(blue)!r})], "
        f"Path({str(out)!r}), transitions=[0.2]))"
    )
    env = {
        **os.environ,
        "PYTHONPATH": f"{Path.cwd() / 'apps'}:{Path.cwd() / 'apps/backend'}",
    }
    subprocess.run([sys.executable, "-c", script], check=True, env=env, timeout=10)

    before = _average_rgb_at(out, 0.70)
    blended = _average_rgb_at(out, 0.90)
    after = _average_rgb_at(out, 1.10)

    assert before[0] > 180
    assert before[2] < 80
    assert blended[0] > 60
    assert blended[2] > 60
    assert blended[0] < before[0] - 40
    assert blended[2] > before[2] + 40
    assert after[2] > 180
    assert after[0] < 80
