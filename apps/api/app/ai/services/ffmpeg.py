"""ffmpeg utilities — clip extraction, keyframe sampling, stitching, cropping.

All video manipulation goes through ffmpeg. This service is shared between
Person 2 (backend) and Person 3 (AI), but Person 3 owns the logic.
"""

import subprocess
from pathlib import Path


def extract_clip(
    video_path: str,
    start_ts: float,
    end_ts: float,
    output_path: str,
) -> str:
    """Extract a clip from a video at the given timestamps.

    Args:
        video_path: Path to source video
        start_ts: Start timestamp in seconds
        end_ts: End timestamp in seconds
        output_path: Where to save the extracted clip

    Returns:
        Path to the extracted clip
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    duration = end_ts - start_ts

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start_ts),
            "-i", video_path,
            "-t", str(duration),
            "-c", "copy",
            output_path,
        ],
        capture_output=True,
        check=True,
    )
    return output_path


def _probe_duration_sync(video_path: str) -> float:
    import json
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path,
        ],
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


_SAFE_END_EPSILON = 0.05


def extract_frame(
    video_path: str,
    timestamp: float,
    output_path: str,
) -> str:
    """Extract a single frame at a given timestamp.

    Clamps *timestamp* to a safe distance from the end of the video to
    avoid ffmpeg encoder failures on the final partial GOP.

    Args:
        video_path: Path to source video
        timestamp: Timestamp in seconds
        output_path: Where to save the frame (png)

    Returns:
        Path to the extracted frame
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    duration = _probe_duration_sync(video_path)
    safe_ts = min(max(timestamp, 0.0), duration - _SAFE_END_EPSILON)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{safe_ts:.3f}",
            "-i", video_path,
            "-frames:v", "1",
            output_path,
        ],
        capture_output=True,
        check=True,
    )
    return output_path


def extract_keyframes(
    video_path: str,
    output_dir: str,
    fps: float = 1.0,
) -> list[str]:
    """Extract keyframes at a given rate (default 1 per second).

    Args:
        video_path: Path to source video
        output_dir: Directory to save keyframes
        fps: Frames per second to extract

    Returns:
        List of paths to extracted keyframe images, sorted by timestamp
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps={fps}",
            str(out / "keyframe_%04d.png"),
        ],
        capture_output=True,
        check=True,
    )

    return sorted(str(p) for p in out.glob("keyframe_*.png"))


def crop_bbox_from_frame(
    frame_path: str,
    bbox: dict[str, float],
) -> str:
    """Crop a bounding box region from a frame.

    Args:
        frame_path: Path to the full frame image
        bbox: Normalized bounding box {x, y, w, h} (0-1, top-left origin)

    Returns:
        Path to the cropped image
    """
    output_path = str(Path(frame_path).with_suffix(".crop.png"))

    # ffmpeg crop filter uses pixel coordinates, so we need to convert
    # from normalized 0-1 to the actual crop expression
    crop_filter = (
        f"crop="
        f"iw*{bbox['w']}:ih*{bbox['h']}:"
        f"iw*{bbox['x']}:ih*{bbox['y']}"
    )

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", frame_path,
            "-vf", crop_filter,
            output_path,
        ],
        capture_output=True,
        check=True,
    )
    return output_path


def normalize_fps(
    video_path: str,
    target_fps: float,
    output_path: str,
) -> str:
    """Re-encode a video to match a target fps.

    Used to normalize generated clips before stitching so crossfade
    doesn't jitter from fps mismatch.

    Args:
        video_path: Path to input video
        target_fps: Target frames per second
        output_path: Where to save the normalized video

    Returns:
        Path to the normalized video
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps={target_fps}",
            "-c:v", "libx264",
            "-preset", "fast",
            output_path,
        ],
        capture_output=True,
        check=True,
    )
    return output_path


def stitch_with_crossfade(
    before_path: str,
    replacement_path: str,
    after_path: str,
    output_path: str,
    crossfade_frames: int = 3,
) -> str:
    """Stitch a replacement clip into a video with crossfade transitions.

    Concatenates [before_segment, replacement, after_segment] with
    a short crossfade dissolve at each cut point.

    Args:
        before_path: Video segment before the replacement
        replacement_path: The generated replacement clip
        after_path: Video segment after the replacement
        output_path: Where to save the final stitched video
        crossfade_frames: Number of frames for each crossfade

    Returns:
        Path to the stitched video
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Use ffmpeg concat with xfade filter for crossfade
    # Duration of crossfade in seconds (assuming ~24fps)
    xfade_duration = crossfade_frames / 24.0

    filter_complex = (
        f"[0:v][1:v]xfade=transition=fade:duration={xfade_duration}:offset=0[v01];"
        f"[v01][2:v]xfade=transition=fade:duration={xfade_duration}:offset=0[vout]"
    )

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", before_path,
            "-i", replacement_path,
            "-i", after_path,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", "fast",
            output_path,
        ],
        capture_output=True,
        check=True,
    )
    return output_path


def sample_frames_from_clip(
    video_path: str,
    num_frames: int = 3,
) -> list[str]:
    """Sample evenly-spaced frames from a clip for quality scoring.

    Args:
        video_path: Path to video clip
        num_frames: Number of frames to sample

    Returns:
        List of paths to sampled frame images
    """
    output_dir = Path(video_path).parent / "sampled_frames"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get duration first
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            video_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(result.stdout.strip())

    paths: list[str] = []
    for i in range(num_frames):
        ts = (i + 0.5) * duration / num_frames
        out = str(output_dir / f"sample_{i:02d}.png")
        extract_frame(video_path, ts, out)
        paths.append(out)

    return paths


def get_video_info(video_path: str) -> dict:
    """Get video metadata (duration, fps, resolution).

    Returns:
        {"duration": float, "fps": float, "width": int, "height": int}
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,duration:stream_tags=rotate:stream_side_data=rotation",
            "-show_entries", "format=duration",
            "-of", "json",
            video_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    import json
    data = json.loads(result.stdout)

    stream = data.get("streams", [{}])[0]
    fmt = data.get("format", {})

    # Parse fps from "30/1" format
    fps_str = stream.get("r_frame_rate", "24/1")
    num, den = fps_str.split("/")
    fps = float(num) / float(den)

    duration = float(stream.get("duration", 0) or fmt.get("duration", 0))

    raw_rotation = next(
        (
            side_data.get("rotation")
            for side_data in stream.get("side_data_list", [])
            if "rotation" in side_data
        ),
        stream.get("tags", {}).get("rotate", 0),
    )
    try:
        rotation = int(round(float(raw_rotation or 0))) % 360
    except (TypeError, ValueError):
        rotation = 0
    encoded_width = int(stream.get("width", 0))
    encoded_height = int(stream.get("height", 0))
    width, height = (
        (encoded_height, encoded_width)
        if rotation % 180
        else (encoded_width, encoded_height)
    )

    return {
        "duration": duration,
        "fps": fps,
        "width": width,
        "height": height,
        "rotation": rotation,
    }
