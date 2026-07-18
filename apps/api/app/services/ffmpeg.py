"""Thin async wrappers around ffmpeg and ffprobe.

All functions shell out to the ffmpeg/ffprobe binaries on PATH. Stderr is
captured and included in FfmpegError messages to make 3am debugging humane.
"""
from __future__ import annotations

import json
import subprocess
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
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise FfmpegError(cmd, proc.stderr.decode(errors="replace"), proc.returncode or -1)
    return proc.stdout, proc.stderr


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
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))

    fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1")
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)

    return {
        "duration": duration,
        "fps": fps,
        "width": width,
        "height": height,
        "has_audio": has_audio,
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


async def render_clip_span(
    src: str | Path,
    start: float,
    end: float,
    out: str | Path,
    *,
    width: int,
    height: int,
    fps: float,
    volume: float = 1.0,
) -> Path:
    """Render a normalized clip span with guaranteed h264+aac streams."""
    out = Path(out)
    duration = max(0.0, end - start)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps:.4f}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(src),
        "-f", "lavfi",
        "-t", f"{duration:.3f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
    ]
    if volume < 0.999:
        cmd.extend(["-af", f"volume={max(0.0, min(1.0, volume)):.3f}"])
    cmd.append(str(out))
    await _run(cmd)
    return out


async def conform_generated_edit(
    generated: str | Path,
    source: str | Path,
    duration: float,
    out: str | Path,
) -> Path:
    """Trim generated video to the edit window and restore source audio."""
    generated_meta = await probe(generated)
    source_meta = await probe(source)
    generated_duration = float(generated_meta["duration"])
    shortfall = duration - generated_duration
    allowed_shortfall = min(0.5, duration * 0.05)
    if shortfall > allowed_shortfall:
        raise ValueError(
            f"generated clip is too short ({generated_duration:.2f}s for {duration:.2f}s edit)"
        )

    out = Path(out)
    # Providers commonly return a few frames less than requested. Stretch only
    # that small drift across the clip instead of rejecting it or freezing the
    # final frame, which would create a visible exit seam.
    timing = (
        f"setpts={duration / generated_duration:.8f}*PTS,"
        if shortfall > 0.0 and generated_duration > 0.0
        else ""
    )
    vf = (
        f"{timing}trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
        f"scale={source_meta['width']}:{source_meta['height']}:force_original_aspect_ratio=decrease,"
        f"pad={source_meta['width']}:{source_meta['height']}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={source_meta['fps']:.4f}"
    )
    cmd = ["ffmpeg", "-y", "-i", str(generated), "-i", str(source)]
    if source_meta["has_audio"]:
        filter_complex = (
            f"[0:v]{vf}[v];"
            f"[1:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a]"
        )
        audio_map = "[a]"
    else:
        cmd.extend([
            "-f", "lavfi", "-t", f"{duration:.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        ])
        filter_complex = f"[0:v]{vf}[v]"
        audio_map = "2:a:0"

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        str(out),
    ])
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


async def prepend_video_handoff(
    base: str | Path,
    handoff: str | Path,
    duration: float,
    out: str | Path,
) -> Path:
    """Replace the beginning of a silent source clip with a prior chunk overlap."""
    if duration <= 0:
        raise ValueError("handoff duration must be positive")
    out = Path(out)
    filter_complex = (
        f"[1:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS[handoff];"
        f"[0:v]trim=start={duration:.3f},setpts=PTS-STARTPTS[tail];"
        "[handoff][tail]concat=n=2:v=1:a=0[v]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(base),
        "-i", str(handoff),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-an",
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


async def concat_video_clips(paths: list[str | Path], out: str | Path) -> Path:
    """Concatenate normalized video-only spans without inventing a dissolve."""
    if not paths:
        raise ValueError("at least one video span is required")
    out = Path(out)
    if len(paths) == 1:
        metadata = await probe(paths[0])
        return await extract_clip(
            paths[0],
            0.0,
            float(metadata["duration"]),
            out,
            with_audio=False,
        )
    inputs = [item for path in paths for item in ("-i", str(path))]
    reset = ";".join(
        f"[{index}:v]setpts=PTS-STARTPTS[v{index}]"
        for index in range(len(paths))
    )
    labels = "".join(f"[v{index}]" for index in range(len(paths)))
    filter_complex = f"{reset};{labels}concat=n={len(paths)}:v=1:a=0[v]"
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        str(out),
    ]
    await _run(cmd)
    return out


async def concat_clips(\
    paths: list[Path],
    out: str | Path,
    *,
    transitions: list[float] | None = None,
) -> Path:
    """Concatenate MP4s with per-boundary transition control.

    ``transitions`` is a list of (len(paths)-1) floats — the dissolve duration
    in seconds at each seam.  0.0 means a hard cut; anything larger triggers an
    xfade dissolve of that length.  When omitted, all seams are hard cuts.

    This replaces the old ``concat_with_xfade`` which applied the same 0.5s
    dissolve to *every* boundary — including AI→original seams where the frames
    are visually disconnected, producing the "flicker smear" the user sees.
    """
    out_path = Path(out)
    if len(paths) == 1:
        paths[0].rename(out_path)
        return out_path

    n = len(paths)
    # default: hard cut everywhere
    xd = transitions if transitions is not None else [0.0] * (n - 1)
    assert len(xd) == n - 1, "transitions must have len(paths)-1 entries"

    metadata = [await probe(path) for path in paths]
    durations = [float(item["duration"]) for item in metadata]
    target_fps = next(
        (float(item["fps"]) for item in metadata if float(item.get("fps") or 0.0) > 0.0),
        30.0,
    )

    def normalized_inputs(index: int) -> str:
        # FFmpeg 7 requires both xfade inputs to advertise an explicit,
        # matching constant frame rate and time base. Provider output can
        # omit either even when ffprobe reports a nominal FPS.
        return (
            f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,"
            f"fps={target_fps:.6f}[v_in_{index}];"
            f"[{index}:a]asetpts=PTS-STARTPTS[a_in_{index}]"
        )

    # clamp each dissolve to at most half the shorter adjacent clip
    xd = [
        max(0.0, min(x, min(durations[i], durations[i + 1]) / 2))
        for i, x in enumerate(xd)
    ]

    # For hard cuts, use the concat filter rather than the concat demuxer.
    # The demuxer can preserve packet-duration leftovers at joins, creating a
    # small audio tail even when each rendered part is correctly bounded.
    if all(x == 0.0 for x in xd):
        input_filters = [normalized_inputs(i) for i in range(n)]
        concat_inputs = "".join(
            f"[v_in_{i}][a_in_{i}]" for i in range(n)
        )
        filter_complex = ";".join(input_filters) + (
            f";{concat_inputs}concat=n={n}:v=1:a=1[v][a]"
        )
        cmd = [
            "ffmpeg", "-y",
            *[item for p in paths for item in ("-i", str(p))],
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(out_path),
        ]
        await _run(cmd)
        return out_path

    # Build a chained xfade filter that handles both hard-cut and dissolve
    # boundaries.  At hard-cut boundaries we use duration=0.001 (minimum
    # xfade accepts) so it's visually indistinguishable from a hard cut.
    MIN_X = 0.001  # ffmpeg xfade minimum duration

    # running offset tracks cumulative pts of the *output* stream, which
    # shrinks by the dissolve duration at every dissolve boundary.
    # Reset every input independently.  Providers often return a non-zero
    # start PTS or a different time base; xfade interprets offsets in the
    # first input's PTS domain, so leaving those timestamps intact causes
    # frame jumps at the seam.
    input_filters: list[str] = []
    for i in range(n):
        input_filters.append(normalized_inputs(i))

    video_labels: list[str] = []
    audio_labels: list[str] = []
    offset = 0.0

    for i in range(n - 1):
        x = xd[i] if xd[i] > 0.0 else MIN_X
        # offset = pts in the output when this dissolve should start
        offset += durations[i] - x
        prev_v = f"[v_{i-1}]" if i > 0 else f"[v_in_{i}]"
        prev_a = f"[a_{i-1}]" if i > 0 else f"[a_in_{i}]"
        next_v = f"[v_in_{i + 1}]"
        next_a = f"[a_in_{i + 1}]"
        label_v = "[v]" if i == n - 2 else f"[v_{i}]"
        label_a = "[a]" if i == n - 2 else f"[a_{i}]"
        video_labels.append(
            # Use a true alpha fade. `dissolve` is a patterned pixel effect
            # and is responsible for the box-like animation at the seam.
            f"{prev_v}{next_v}xfade=transition=fade:"
            f"duration={x:.4f}:offset={max(0.0, offset):.4f}{label_v}"
        )
        audio_labels.append(
            f"{prev_a}{next_a}acrossfade=d={x:.4f}{label_a}"
        )

    filter_complex = ";".join(input_filters + video_labels + audio_labels)
    output_duration = max(0.001, sum(durations) - sum(x if x > 0.0 else MIN_X for x in xd))
    cmd = [
        "ffmpeg", "-y",
        *[item for p in paths for item in ("-i", str(p))],
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-t", f"{output_duration:.3f}",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    await _run(cmd)
    return out_path


async def concat_with_xfade(
    paths: list[Path],
    out: str | Path,
    xfade_duration: float = 0.5,
) -> Path:
    """Legacy: apply the same dissolve duration to every seam.

    Prefer ``concat_clips`` with per-boundary ``transitions`` for new callers.
    """
    n = len(paths)
    return await concat_clips(
        paths,
        out,
        transitions=[xfade_duration] * max(0, n - 1),
    )



async def _probe_duration(src: str | Path) -> float:
    """Return the duration (in seconds) of a media file via ffprobe."""
    data = await probe(src)
    return float(data["duration"])


_MIN_SAFE_END_EPSILON = 0.1


async def extract_frame(src: str | Path, ts: float, out: str | Path) -> Path:
    """Grab a single frame at timestamp `ts` as a JPEG.

    Clamps *ts* to a safe distance from the end of the video to avoid
    ffmpeg encoder failures on the final partial GOP.
    """
    out = Path(out)
    metadata = await probe(src)
    duration = float(metadata["duration"])
    fps = float(metadata.get("fps") or 0.0)
    # Container duration can extend beyond the final decodable video frame.
    # Leave at least three frame intervals (and never less than 100 ms) so a
    # request at the timeline end still lands on a real frame for VFR/29.97
    # fps inputs.
    end_guard = max(_MIN_SAFE_END_EPSILON, 3.0 / fps if fps > 0 else 0.0)
    safe_ts = min(max(ts, 0.0), max(0.0, duration - end_guard))

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{safe_ts:.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-q:v", "2",
        "-pix_fmt", "yuvj420p",
        str(out),
    ]
    await _run(cmd)
    if not out.is_file() or out.stat().st_size == 0:
        raise FfmpegError(cmd, "ffmpeg completed without producing a frame", 0)
    return out


async def extract_sampled_frames(
    src: str | Path,
    *,
    start_ts: float,
    end_ts: float,
    fps: float,
    output_dir: str | Path,
    max_width: int = 640,
) -> tuple[list[str], list[float]]:
    """Decode a bounded analysis window once instead of one process per frame."""
    if start_ts < 0.0 or end_ts <= start_ts:
        raise ValueError("sample window must have positive duration")
    if fps <= 0.0:
        raise ValueError("sample fps must be positive")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    pattern = destination / "frame_%06d.jpg"
    sample_duration = end_ts - start_ts + 0.5 / fps
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_ts:.6f}",
        "-i",
        str(src),
        "-t",
        f"{sample_duration:.6f}",
        "-vf",
        f"fps={fps:.6f},scale=w=min({max_width}\\,iw):h=-2",
        "-q:v",
        "3",
        "-start_number",
        "0",
        str(pattern),
    ]
    await _run(cmd)
    paths = sorted(destination.glob("frame_*.jpg"))
    if not paths:
        raise FfmpegError(cmd, "ffmpeg produced no analysis frames", 0)
    timestamps = [min(end_ts, start_ts + index / fps) for index in range(len(paths))]
    return [str(path) for path in paths], timestamps


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
