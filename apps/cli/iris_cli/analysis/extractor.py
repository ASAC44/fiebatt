"""Frame extraction from video files via ffmpeg subprocess."""

from pathlib import Path
import subprocess
import tempfile


def extract_frames(
    video_path: Path,
    fps: float = 1.0,
    output_dir: Path | None = None,
) -> list[tuple[float, Path]]:
    """Extract frames from video at given fps.

    Returns list of (timestamp_seconds, frame_path) tuples.

    Raises:
        FileNotFoundError: if ffmpeg is not installed
        subprocess.CalledProcessError: if ffmpeg fails
    """
    # Check ffmpeg is available
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "ffmpeg is required but not found on PATH. "
            "Install it: brew install ffmpeg (macOS) / apt install ffmpeg (Linux)"
        ) from exc

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="iris-frames-"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use ffmpeg to extract frames
    # Pattern: frame_%05d.png (1-indexed by ffmpeg)
    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "2",  # high quality
        str(output_dir / "frame_%05d.png"),
        "-y",  # overwrite
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    # Collect frames with timestamps
    frames = sorted(output_dir.glob("frame_*.png"))
    result: list[tuple[float, Path]] = []
    for i, frame in enumerate(frames):
        timestamp = i / fps
        result.append((timestamp, frame))
    return result


def download_video(url: str, dest: Path) -> Path:
    """Download video from URL to local path using httpx.

    Args:
        url: remote video URL
        dest: local destination path

    Returns:
        The destination path after successful download.
    """
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=8192):
                f.write(chunk)
    return dest
