"""Thin async wrappers around ffmpeg and ffprobe.

All functions shell out to the ffmpeg/ffprobe binaries on PATH. Stderr is
captured and included in FfmpegError messages to make 3am debugging humane.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Iterable


class FfmpegError(RuntimeError):
    def __init__(self, cmd: list[str], stderr: str, returncode: int):
        self.cmd = cmd
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(
            f"ffmpeg failed (rc={returncode}): {' '.join(cmd)}\n--- stderr ---\n{stderr}"
        )


async def _run(cmd: list[str]) -> tuple[bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(cmd, stderr.decode(errors="replace"), proc.returncode or -1)
    return stdout, stderr


async def probe(path: str | Path) -> dict:
    """Return {duration, fps, width, height} for a video file."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    stdout, _ = await _run(cmd)
    data = json.loads(stdout.decode())

    duration = float(data.get("format", {}).get("duration", 0.0))

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise FfmpegError(cmd, "no video stream found", 0)

    fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1")
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)

    return {
        "duration": duration,
        "fps": fps,
        "width": width,
        "height": height,
    }


def _parse_fps(rate: str) -> float:
    if "/" in rate:
        num, denom = rate.split("/", 1)
        try:
            n, d = float(num), float(denom)
            return n / d if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


async def extract_clip(
    src: str | Path,
    start: float,
    end: float,
    out: str | Path,
    *,
    vf: str | None = None,
    with_audio: bool = True,
) -> Path:
    """Cut [start, end] from src. Re-encodes for frame-accurate cuts on short segments."""
    out = Path(out)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
    ]
    if vf:
        cmd.extend(["-vf", vf])
    cmd.extend([
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-movflags", "+faststart",
    ])
    if with_audio:
        cmd.extend(["-c:a", "aac"])
    else:
        cmd.append("-an")
    cmd.append(str(out))
    await _run(cmd)
    return out


async def normalize_fps(
    src: str | Path,
    fps: float,
    out: str | Path,
) -> Path:
    """Re-encode src at the given fps. Call on generated clips before stitching."""
    out = Path(out)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", f"fps={fps:.4f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(out),
    ]
    await _run(cmd)
    return out


async def stitch_crossfade(
    base: str | Path,
    replacement: str | Path,
    at_ts: float,
    duration: float,
    out: str | Path,
    xfade_len: float = 0.1,
) -> Path:
    """Replace `duration` seconds of `base` starting at `at_ts` with `replacement`.

    Structure: [pre][xfade -> replacement][xfade -> post]. Uses the xfade filter
    for video and acrossfade for audio.
    """
    out = Path(out)
    # clamp tiny overshoots
    x = max(0.02, min(xfade_len, duration / 2))
    filter_complex = (
        f"[0:v]trim=0:{at_ts:.3f},setpts=PTS-STARTPTS[pre_v];"
        f"[0:v]trim={at_ts:.3f}:{at_ts + duration:.3f},setpts=PTS-STARTPTS[mid_v];"
        f"[0:v]trim={at_ts + duration:.3f},setpts=PTS-STARTPTS[post_v];"
        f"[1:v]setpts=PTS-STARTPTS[rep_v];"
        f"[pre_v][rep_v]xfade=transition=fade:duration={x:.3f}:offset={max(0, at_ts - x):.3f}[pre_mix];"
        f"[pre_mix][post_v]xfade=transition=fade:duration={x:.3f}:offset={max(0, at_ts + duration - x):.3f}[v];"
        f"[0:a]atrim=0:{at_ts:.3f},asetpts=PTS-STARTPTS[pre_a];"
        f"[0:a]atrim={at_ts + duration:.3f},asetpts=PTS-STARTPTS[post_a];"
        f"[pre_a]anullsrc=duration={duration:.3f}:r=44100:cl=stereo[mid_silence];"
        f"[pre_a][mid_silence][post_a]concat=n=3:v=0:a=1[a]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(base),
        "-i", str(replacement),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(out),
    ]
    await _run(cmd)
    return out


async def simple_replace(
    base: str | Path,
    replacement: str | Path,
    at_ts: float,
    duration: float,
    out: str | Path,
) -> Path:
    """Hard-cut version of stitch (no crossfade). Cheaper, less visually smooth.

    Kept as a fallback if xfade filter chokes on weird durations.
    """
    out = Path(out)
    filter_complex = (
        f"[0:v]trim=0:{at_ts:.3f},setpts=PTS-STARTPTS[pre_v];"
        f"[0:v]trim={at_ts + duration:.3f},setpts=PTS-STARTPTS[post_v];"
        f"[1:v]setpts=PTS-STARTPTS[rep_v];"
        f"[pre_v][rep_v][post_v]concat=n=3:v=1:a=0[v];"
        f"[0:a]atrim=0:{at_ts:.3f},asetpts=PTS-STARTPTS[pre_a];"
        f"[0:a]atrim={at_ts + duration:.3f},asetpts=PTS-STARTPTS[post_a];"
        f"anullsrc=duration={duration:.3f}:r=44100:cl=stereo[mid_a];"
        f"[pre_a][mid_a][post_a]concat=n=3:v=0:a=1[a]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(base),
        "-i", str(replacement),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(out),
    ]
    await _run(cmd)
    return out


async def concat_mp4s(paths: Iterable[str | Path], out: str | Path) -> Path:
    """Concatenate MP4s via the concat demuxer. Inputs must share codec+fps+size."""
    out = Path(out)
    list_path = out.with_suffix(".concat.txt")
    with list_path.open("w") as f:
        for p in paths:
            f.write(f"file '{Path(p).absolute()}'\n")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(out),
    ]
    try:
        await _run(cmd)
    finally:
        try:
            list_path.unlink()
        except FileNotFoundError:
            pass
    return out


async def _probe_duration(src: str | Path) -> float:
    """Return the duration (in seconds) of a media file via ffprobe."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(src),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    data = json.loads(stdout)
    return float(data["format"]["duration"])


_SAFE_END_EPSILON = 0.05  # keep at least 50ms away from the end


async def extract_frame(src: str | Path, ts: float, out: str | Path) -> Path:
    """Grab a single frame at timestamp `ts` as a JPEG.

    Clamps *ts* to a safe distance from the end of the video to avoid
    ffmpeg encoder failures on the final partial GOP.
    """
    out = Path(out)
    duration = await _probe_duration(src)
    safe_ts = min(max(ts, 0.0), duration - _SAFE_END_EPSILON)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{safe_ts:.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-q:v", "2",
        str(out),
    ]
    await _run(cmd)
    return out


async def crop_bbox_from_frame(
    frame_path: str | Path,
    bbox: dict[str, float],
    out: str | Path | None = None,
) -> Path:
    """Crop a normalized bbox from a still frame image."""
    frame_path = Path(frame_path)
    out = Path(out) if out is not None else frame_path.with_suffix(".crop.png")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(frame_path),
        "-vf",
        (
            f"crop="
            f"iw*{bbox['w']}:ih*{bbox['h']}:"
            f"iw*{bbox['x']}:ih*{bbox['y']}"
        ),
        str(out),
    ]
    await _run(cmd)
    return out


async def extract_keyframes(
    src: str | Path,
    fps: float,
    out_pattern: str | Path,
) -> list[Path]:
    """Sample `fps` keyframes per second from src. Returns written paths in order."""
    out_pattern = Path(out_pattern)
    out_pattern.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", f"fps={fps:.4f}",
        "-q:v", "2",
        str(out_pattern),
    ]
    await _run(cmd)
    # pattern is like 'prefix_%04d.jpg' — glob siblings
    parent = out_pattern.parent
    stem = out_pattern.stem.split("%")[0].rstrip("_")
    files = sorted(parent.glob(f"{stem}*.jpg"))
    return files
