"""Deterministic, multi-frame checks for generated local-edit boundaries."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services import ffmpeg
from app.services.generation_window import GenerationWindow


@dataclass(frozen=True, slots=True)
class ContinuityThresholds:
    duration_delta_s: float = 0.16
    fps_delta_ratio: float = 0.08
    handle_pixel_delta: float = 0.14
    handle_background_delta: float = 0.09
    handle_color_delta: float = 0.10
    handle_target_histogram_delta: float = 0.40
    subject_motion_jump: float = 0.78
    camera_motion_jump: float = 0.72
    target_trajectory_jump: float = 0.45
    frozen_motion_px: float = 0.08
    moving_source_px: float = 0.65


DEFAULT_THRESHOLDS = ContinuityThresholds()


@dataclass(frozen=True, slots=True)
class ContinuityIssue:
    code: str
    value: float
    threshold: float
    boundary: str | None = None

    def metadata(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ContinuityReport:
    passed: bool
    metrics: dict[str, float | None]
    issues: list[ContinuityIssue] = field(default_factory=list)
    sampled_frames: int = 0

    def metadata(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "metrics": self.metrics,
            "issues": [issue.metadata() for issue in self.issues],
            "sampled_frames": self.sampled_frames,
        }

    def correction_evidence(self) -> str:
        if not self.issues:
            return ""
        return "; ".join(
            f"{issue.code} at {issue.boundary or 'clip'} "
            f"(measured {issue.value:.3f}, limit {issue.threshold:.3f})"
            for issue in self.issues
        )


@dataclass(frozen=True, slots=True)
class ContinuitySamples:
    source_pre: tuple[np.ndarray, ...] = ()
    generated_pre: tuple[np.ndarray, ...] = ()
    source_post: tuple[np.ndarray, ...] = ()
    generated_post: tuple[np.ndarray, ...] = ()
    generated_entry: tuple[np.ndarray, ...] = ()
    generated_exit: tuple[np.ndarray, ...] = ()
    source_tail: tuple[np.ndarray, ...] = ()
    generated_tail: tuple[np.ndarray, ...] = ()


def _bbox_mask(
    shape: tuple[int, ...],
    bbox: dict[str, float],
    *,
    invert: bool = False,
    expand: float = 0.08,
) -> np.ndarray:
    height, width = shape[:2]
    x = max(0.0, float(bbox.get("x", 0.0)) - expand)
    y = max(0.0, float(bbox.get("y", 0.0)) - expand)
    right = min(1.0, float(bbox.get("x", 0.0)) + float(bbox.get("w", 1.0)) + expand)
    bottom = min(1.0, float(bbox.get("y", 0.0)) + float(bbox.get("h", 1.0)) + expand)
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[round(y * height) : round(bottom * height), round(x * width) : round(right * width)] = 255
    return cv2.bitwise_not(mask) if invert else mask


def _normalized_difference(
    left: np.ndarray,
    right: np.ndarray,
    mask: np.ndarray | None = None,
) -> float:
    delta = cv2.absdiff(left, right).astype(np.float32) / 255.0
    if mask is None:
        return float(delta.mean())
    selected = delta[mask > 0]
    return float(selected.mean()) if selected.size else 0.0


def _paired_mean(
    left: tuple[np.ndarray, ...],
    right: tuple[np.ndarray, ...],
    metric,
) -> float | None:
    values = [metric(a, b) for a, b in zip(left, right, strict=False)]
    return float(np.mean(values)) if values else None


def _color_delta(left: np.ndarray, right: np.ndarray) -> float:
    left_lab = cv2.cvtColor(left, cv2.COLOR_BGR2LAB)
    right_lab = cv2.cvtColor(right, cv2.COLOR_BGR2LAB)
    return _normalized_difference(left_lab, right_lab)


def _target_histogram_delta(
    left: np.ndarray,
    right: np.ndarray,
    mask: np.ndarray,
) -> float:
    left_hist = cv2.calcHist([left], [0, 1], mask, [16, 16], [0, 256, 0, 256])
    right_hist = cv2.calcHist([right], [0, 1], mask, [16, 16], [0, 256, 0, 256])
    cv2.normalize(left_hist, left_hist)
    cv2.normalize(right_hist, right_hist)
    return float(cv2.compareHist(left_hist, right_hist, cv2.HISTCMP_BHATTACHARYYA))


def _motion_vector(
    left: np.ndarray,
    right: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    left_gray = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    right_gray = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(
        left_gray,
        right_gray,
        None,
        0.5,
        3,
        15,
        3,
        5,
        1.2,
        0,
    )
    selected = flow[mask > 0]
    if selected.size == 0:
        return np.zeros(2, dtype=np.float32)
    return np.median(selected, axis=0)


def _motion_jump(frames: tuple[np.ndarray, ...], mask: np.ndarray) -> float | None:
    if len(frames) != 3:
        return None
    incoming = _motion_vector(frames[0], frames[1], mask)
    outgoing = _motion_vector(frames[1], frames[2], mask)
    denominator = float(np.linalg.norm(incoming) + np.linalg.norm(outgoing) + 0.5)
    return float(np.linalg.norm(incoming - outgoing) / denominator)


def _structure_centroid(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    points = np.argwhere((edges > 0) & (mask > 0))
    if not len(points):
        return np.array([0.5, 0.5], dtype=np.float32)
    height, width = gray.shape
    y, x = points.mean(axis=0)
    return np.array([x / max(width, 1), y / max(height, 1)], dtype=np.float32)


def _trajectory_jump(frames: tuple[np.ndarray, ...], mask: np.ndarray) -> float | None:
    if len(frames) != 3:
        return None
    points = [_structure_centroid(frame, mask) for frame in frames]
    incoming = points[1] - points[0]
    outgoing = points[2] - points[1]
    return float(np.linalg.norm(incoming - outgoing))


def _mean_motion(frames: tuple[np.ndarray, ...], mask: np.ndarray) -> float | None:
    if len(frames) < 2:
        return None
    values = [
        float(np.linalg.norm(_motion_vector(left, right, mask)))
        for left, right in zip(frames, frames[1:], strict=False)
    ]
    return float(np.mean(values)) if values else None


def score_continuity_samples(
    samples: ContinuitySamples,
    *,
    bbox: dict[str, float],
    source_duration: float,
    generated_duration: float,
    source_fps: float,
    generated_fps: float,
    thresholds: ContinuityThresholds = DEFAULT_THRESHOLDS,
) -> ContinuityReport:
    """Score aligned source/generated handles and three-frame seam neighborhoods."""
    all_frames = (
        samples.source_pre
        or samples.generated_pre
        or samples.source_post
        or samples.generated_post
        or samples.generated_entry
        or samples.generated_exit
        or samples.source_tail
        or samples.generated_tail
    )
    if not all_frames:
        raise ValueError("continuity validator received no frames")
    target_mask = _bbox_mask(all_frames[0].shape, bbox)
    background_mask = _bbox_mask(all_frames[0].shape, bbox, invert=True)

    handle_pairs = (
        ("pre", samples.source_pre, samples.generated_pre),
        ("post", samples.source_post, samples.generated_post),
    )
    metrics: dict[str, float | None] = {
        "duration_delta_s": abs(generated_duration - source_duration),
        "fps_delta_ratio": abs(generated_fps - source_fps) / max(source_fps, 1.0),
    }
    for name, source, generated in handle_pairs:
        metrics[f"{name}_handle_pixel_delta"] = _paired_mean(
            source, generated, _normalized_difference
        )
        metrics[f"{name}_handle_background_delta"] = _paired_mean(
            source,
            generated,
            lambda left, right: _normalized_difference(left, right, background_mask),
        )
        metrics[f"{name}_handle_color_delta"] = _paired_mean(source, generated, _color_delta)
        metrics[f"{name}_target_histogram_delta"] = _paired_mean(
            source,
            generated,
            lambda left, right: _target_histogram_delta(left, right, target_mask),
        )

    for name, frames in (
        ("entry", samples.generated_entry),
        ("exit", samples.generated_exit),
    ):
        metrics[f"{name}_subject_motion_jump"] = _motion_jump(frames, target_mask)
        metrics[f"{name}_camera_motion_jump"] = _motion_jump(frames, background_mask)
        metrics[f"{name}_target_trajectory_jump"] = _trajectory_jump(frames, target_mask)

    metrics["source_tail_motion_px"] = _mean_motion(samples.source_tail, target_mask)
    metrics["generated_tail_motion_px"] = _mean_motion(samples.generated_tail, target_mask)

    issues: list[ContinuityIssue] = []

    def exceed(code: str, threshold: float, *, boundary: str | None = None) -> None:
        value = metrics.get(code)
        if value is not None and value > threshold:
            issues.append(ContinuityIssue(code, value, threshold, boundary))

    exceed("duration_delta_s", thresholds.duration_delta_s)
    exceed("fps_delta_ratio", thresholds.fps_delta_ratio)
    for boundary in ("pre", "post"):
        exceed(f"{boundary}_handle_pixel_delta", thresholds.handle_pixel_delta, boundary=boundary)
        exceed(
            f"{boundary}_handle_background_delta",
            thresholds.handle_background_delta,
            boundary=boundary,
        )
        exceed(f"{boundary}_handle_color_delta", thresholds.handle_color_delta, boundary=boundary)
        exceed(
            f"{boundary}_target_histogram_delta",
            thresholds.handle_target_histogram_delta,
            boundary=boundary,
        )
    for boundary in ("entry", "exit"):
        exceed(
            f"{boundary}_subject_motion_jump",
            thresholds.subject_motion_jump,
            boundary=boundary,
        )
        exceed(
            f"{boundary}_camera_motion_jump",
            thresholds.camera_motion_jump,
            boundary=boundary,
        )
        exceed(
            f"{boundary}_target_trajectory_jump",
            thresholds.target_trajectory_jump,
            boundary=boundary,
        )
    source_tail = metrics["source_tail_motion_px"]
    generated_tail = metrics["generated_tail_motion_px"]
    if (
        source_tail is not None
        and generated_tail is not None
        and source_tail > thresholds.moving_source_px
        and generated_tail < thresholds.frozen_motion_px
    ):
        issues.append(
            ContinuityIssue(
                "frozen_tail",
                generated_tail,
                thresholds.frozen_motion_px,
                "post",
            )
        )

    sampled_frames = sum(len(getattr(samples, field_name)) for field_name in samples.__dataclass_fields__)
    return ContinuityReport(
        passed=not issues,
        metrics=metrics,
        issues=issues,
        sampled_frames=sampled_frames,
    )


def _handle_times(start: float, end: float, count: int = 3) -> list[float]:
    if end - start < 0.08:
        return []
    return [start + (index + 1) * (end - start) / (count + 1) for index in range(count)]


def _triplet(center: float, duration: float, fps: float) -> list[float]:
    step = max(1.0 / max(fps, 1.0), 0.04)
    values = [max(0.0, center - step), center, min(duration - step / 2, center + step)]
    return values if values[0] < values[1] < values[2] else []


def _read_frames(path: Path, timestamps: list[float]) -> tuple[np.ndarray, ...]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"could not open video for continuity validation: {path}")
    frames: list[np.ndarray] = []
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frame_step = 1.0 / fps if fps > 0 else 0.04
        last_frame_time = (
            max(0.0, (frame_count - 1) / fps)
            if fps > 0 and frame_count > 0
            else None
        )
        for timestamp in timestamps:
            safe_timestamp = max(0.0, timestamp)
            if last_frame_time is not None:
                safe_timestamp = min(safe_timestamp, last_frame_time)
            frame = None
            # Container duration often extends half a frame beyond the final
            # decodable frame. Back off instead of discarding a good render.
            for backoff in (0.0, frame_step, frame_step * 2):
                candidate = max(0.0, safe_timestamp - backoff)
                capture.set(cv2.CAP_PROP_POS_MSEC, candidate * 1000.0)
                ok, candidate_frame = capture.read()
                if ok and candidate_frame is not None:
                    frame = candidate_frame
                    break
            if frame is None:
                raise ValueError(f"could not read continuity frame at {timestamp:.3f}s")
            frames.append(frame)
    finally:
        capture.release()
    return tuple(frames)


def _match_shapes(
    source: tuple[np.ndarray, ...],
    generated: tuple[np.ndarray, ...],
) -> tuple[tuple[np.ndarray, ...], tuple[np.ndarray, ...]]:
    if not source or not generated:
        return source, generated
    height, width = source[0].shape[:2]
    resized = tuple(
        frame if frame.shape[:2] == (height, width) else cv2.resize(frame, (width, height))
        for frame in generated
    )
    return source, resized


async def validate_generated_continuity(
    *,
    source_path: Path,
    generated_path: Path,
    window: GenerationWindow,
    bbox: dict[str, float],
    thresholds: ContinuityThresholds = DEFAULT_THRESHOLDS,
) -> ContinuityReport:
    """Sample only handles, seam neighborhoods, and tail; never scan the full clip."""
    source_meta, generated_meta = await asyncio.gather(
        ffmpeg.probe(source_path),
        ffmpeg.probe(generated_path),
    )
    source_duration = float(source_meta["duration"])
    generated_duration = float(generated_meta["duration"])
    source_fps = float(source_meta["fps"])
    generated_fps = float(generated_meta["fps"])
    usable_duration = min(source_duration, generated_duration)

    pre_times = _handle_times(0.0, min(window.edit_start_offset, usable_duration))
    post_times = _handle_times(
        min(window.edit_end_offset, usable_duration),
        usable_duration,
    )
    entry_times = _triplet(window.edit_start_offset, usable_duration, generated_fps)
    exit_times = _triplet(window.edit_end_offset, usable_duration, generated_fps)
    tail_step = max(1.0 / max(generated_fps, 1.0), 0.04)
    tail_times = [
        max(0.0, usable_duration - tail_step * multiplier)
        for multiplier in (2.5, 1.5, 0.5)
    ]

    groups = await asyncio.gather(
        asyncio.to_thread(_read_frames, source_path, pre_times),
        asyncio.to_thread(_read_frames, generated_path, pre_times),
        asyncio.to_thread(_read_frames, source_path, post_times),
        asyncio.to_thread(_read_frames, generated_path, post_times),
        asyncio.to_thread(_read_frames, generated_path, entry_times),
        asyncio.to_thread(_read_frames, generated_path, exit_times),
        asyncio.to_thread(_read_frames, source_path, tail_times),
        asyncio.to_thread(_read_frames, generated_path, tail_times),
    )
    source_pre, generated_pre = _match_shapes(groups[0], groups[1])
    source_post, generated_post = _match_shapes(groups[2], groups[3])
    source_tail, generated_tail = _match_shapes(groups[6], groups[7])
    reference = source_pre or source_post or source_tail
    height, width = reference[0].shape[:2]

    def resize_group(frames: tuple[np.ndarray, ...]) -> tuple[np.ndarray, ...]:
        return tuple(
            frame if frame.shape[:2] == (height, width) else cv2.resize(frame, (width, height))
            for frame in frames
        )

    return score_continuity_samples(
        ContinuitySamples(
            source_pre=source_pre,
            generated_pre=generated_pre,
            source_post=source_post,
            generated_post=generated_post,
            generated_entry=resize_group(groups[4]),
            generated_exit=resize_group(groups[5]),
            source_tail=source_tail,
            generated_tail=generated_tail,
        ),
        bbox=bbox,
        source_duration=source_duration,
        generated_duration=generated_duration,
        source_fps=source_fps,
        generated_fps=generated_fps,
        thresholds=thresholds,
    )
