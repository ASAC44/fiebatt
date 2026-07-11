from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from app.services import ffmpeg
from app.services.ffmpeg import _run


_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
_SATAVG_RE = re.compile(r"lavfi\.signalstats\.SATAVG=([0-9.]+)")


async def apply_grade(
    input_path: Path,
    output_path: Path,
    adjustments: dict[str, Any],
) -> Path:
    filter_chain = _build_filter_chain(adjustments)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
    ]
    if filter_chain:
        cmd.extend(["-vf", filter_chain])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    await _run(cmd)
    return output_path


async def apply_grade_to_frame(
    input_path: Path,
    output_path: Path,
    adjustments: dict[str, Any],
) -> Path:
    filter_chain = _build_filter_chain(adjustments)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
    ]
    if filter_chain:
        cmd.extend(["-vf", filter_chain])
    cmd.append(str(output_path))
    await _run(cmd)
    return output_path


async def match_color_histogram(
    source_path: Path,
    reference_path: Path,
    output_path: Path,
) -> Path:
    source_stats = await _measure_visual_stats(source_path)
    reference_stats = await _measure_visual_stats(reference_path)

    brightness = _clamp(
        (reference_stats["brightness"] - source_stats["brightness"]) / 255.0,
        -1.0,
        1.0,
    )

    source_saturation = max(source_stats["saturation"], 1.0)
    saturation = _clamp(reference_stats["saturation"] / source_saturation, 0.1, 3.0)

    return await apply_grade(
        input_path=source_path,
        output_path=output_path,
        adjustments={
            "brightness": brightness * 100.0,
            "saturation": (saturation - 1.0) * 100.0,
        },
    )


def _build_filter_chain(adjustments: dict[str, Any]) -> str:
    eq_parts: list[str] = []
    filters: list[str] = []

    brightness = adjustments.get("brightness")
    if brightness is not None:
        value = _clamp(float(brightness), -100.0, 100.0) / 100.0
        eq_parts.append(f"brightness={value:.4f}")

    contrast = adjustments.get("contrast")
    if contrast is not None:
        value = 1.0 + (_clamp(float(contrast), -100.0, 100.0) / 100.0)
        eq_parts.append(f"contrast={value:.4f}")

    saturation = adjustments.get("saturation")
    if saturation is not None:
        value = 1.0 + (_clamp(float(saturation), -100.0, 100.0) / 100.0)
        eq_parts.append(f"saturation={value:.4f}")

    gamma = adjustments.get("gamma")
    if gamma is not None:
        value = _clamp(float(gamma), 0.1, 3.0)
        eq_parts.append(f"gamma={value:.4f}")

    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    hue_shift = adjustments.get("hue_shift")
    if hue_shift is not None:
        value = _clamp(float(hue_shift), -180.0, 180.0)
        filters.append(f"hue=h={value:.4f}")

    temperature = adjustments.get("temperature")
    if temperature is not None:
        value = _clamp(float(temperature), 2000.0, 10000.0)
        filters.append(f"colortemperature=temperature={value:.0f}")

    return ",".join(filters)


async def _measure_visual_stats(path: Path) -> dict[str, float]:
    probe_data = await ffmpeg.probe(path)
    is_video = probe_data.get("duration", 0.0) > 0.0

    if not is_video:
        return await _signalstats_for_input(path)

    ts = max(probe_data["duration"] / 2.0, 0.0)
    with tempfile.TemporaryDirectory(prefix="iris-color-") as tmp_dir:
        frame_path = Path(tmp_dir) / "sample.jpg"
        await ffmpeg.extract_frame(path, ts, frame_path)
        return await _signalstats_for_input(frame_path)


async def _signalstats_for_input(path: Path) -> dict[str, float]:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-vf",
        "signalstats,metadata=mode=print",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    _, stderr = await _run(cmd)
    stderr_text = stderr.decode(errors="replace")

    yavg_match = _YAVG_RE.search(stderr_text)
    satavg_match = _SATAVG_RE.search(stderr_text)
    if yavg_match is None or satavg_match is None:
        raise ffmpeg.FfmpegError(cmd, "failed to parse signalstats output", 0)

    return {
        "brightness": float(yavg_match.group(1)),
        "saturation": float(satavg_match.group(1)),
    }


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
