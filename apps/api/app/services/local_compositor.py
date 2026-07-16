"""Safely composite a tracked generated target over original local footage."""
from __future__ import annotations

import asyncio
import base64
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.ai.services import sam
from app.ai.services.sam import TrackResult
from app.services import ffmpeg


MAX_COMPOSITE_FRAMES = 240
MIN_SEED_CONFIDENCE = 0.75
MIN_TRACK_CONFIDENCE = 0.72
MIN_MEAN_CONFIDENCE = 0.82
MAX_ADJACENT_AREA_RATIO = 2.8
MIN_ADJACENT_IOU = 0.08


@dataclass(frozen=True, slots=True)
class CompositeDecision:
    allowed: bool
    reason: str
    metrics: dict[str, float | int | str | bool]


@dataclass(frozen=True, slots=True)
class CompositeResult:
    applied: bool
    path: Path | None
    reason: str
    metrics: dict[str, float | int | str | bool]


def _decode_mask(value: str, shape: tuple[int, int]) -> np.ndarray:
    raw = np.frombuffer(base64.b64decode(value), dtype=np.uint8)
    mask = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise ValueError("tracker returned an unreadable mask")
    height, width = shape
    if mask.shape != (height, width):
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    return mask > 127


def evaluate_output_track(
    result: TrackResult,
    *,
    frame_count: int,
    frame_shape: tuple[int, int],
    seed_confidence: float | None,
) -> tuple[CompositeDecision, tuple[np.ndarray, ...]]:
    """Reject incomplete, stubbed, low-confidence, or unstable output masks."""
    metrics: dict[str, float | int | str | bool] = {
        "tracker": result.tracker,
        "frame_count": frame_count,
        "tracked_frames": len(result.frames),
        "cancelled": result.cancelled,
        "seed_confidence": float(seed_confidence or 0.0),
    }
    if result.tracker != "sam2_video":
        return CompositeDecision(False, "output tracker is not SAM2", metrics), ()
    if result.cancelled:
        return CompositeDecision(False, "output tracking was cancelled", metrics), ()
    if seed_confidence is None or seed_confidence < MIN_SEED_CONFIDENCE:
        return CompositeDecision(False, "generated target seed mask is uncertain", metrics), ()

    by_index = {int(frame.get("frame_index", -1)): frame for frame in result.frames}
    expected = set(range(frame_count))
    if set(by_index) != expected:
        return CompositeDecision(False, "output tracking did not cover every frame", metrics), ()

    confidences: list[float] = []
    masks: list[np.ndarray] = []
    for index in range(frame_count):
        frame = by_index[index]
        confidence = float(frame.get("confidence") or 0.0)
        mask_b64 = frame.get("mask_b64")
        if frame.get("state") != "tracked" or not isinstance(mask_b64, str):
            return CompositeDecision(False, "generated target was lost", metrics), ()
        confidences.append(confidence)
        try:
            masks.append(_decode_mask(mask_b64, frame_shape))
        except (ValueError, TypeError):
            return CompositeDecision(False, "output tracker mask is invalid", metrics), ()

    metrics["min_confidence"] = min(confidences)
    metrics["mean_confidence"] = float(np.mean(confidences))
    if min(confidences) < MIN_TRACK_CONFIDENCE or np.mean(confidences) < MIN_MEAN_CONFIDENCE:
        return CompositeDecision(False, "output tracking confidence is too low", metrics), ()

    pixel_count = frame_shape[0] * frame_shape[1]
    areas = [int(mask.sum()) for mask in masks]
    metrics["min_area_ratio"] = min(areas) / pixel_count
    metrics["max_area_ratio"] = max(areas) / pixel_count
    if min(areas) < max(16, round(pixel_count * 0.001)):
        return CompositeDecision(False, "generated target mask is effectively empty", metrics), ()
    if max(areas) > round(pixel_count * 0.80):
        return CompositeDecision(False, "generated target mask covers most of the frame", metrics), ()

    area_ratios: list[float] = []
    adjacent_ious: list[float] = []
    for previous, current, previous_area, current_area in zip(
        masks,
        masks[1:],
        areas,
        areas[1:],
        strict=False,
    ):
        area_ratios.append(max(previous_area, current_area) / max(1, min(previous_area, current_area)))
        union = np.logical_or(previous, current).sum()
        adjacent_ious.append(float(np.logical_and(previous, current).sum() / max(1, union)))
    metrics["max_adjacent_area_ratio"] = max(area_ratios, default=1.0)
    metrics["min_adjacent_iou"] = min(adjacent_ious, default=1.0)
    if max(area_ratios, default=1.0) > MAX_ADJACENT_AREA_RATIO:
        return CompositeDecision(False, "output mask area changes abruptly", metrics), ()
    if min(adjacent_ious, default=1.0) < MIN_ADJACENT_IOU:
        return CompositeDecision(False, "output mask jumps between subjects", metrics), ()

    return CompositeDecision(True, "generated target tracking is reliable", metrics), tuple(masks)


def feathered_composite_frames(
    source_frames: tuple[np.ndarray, ...],
    generated_frames: tuple[np.ndarray, ...],
    masks: tuple[np.ndarray, ...],
) -> tuple[np.ndarray, ...]:
    if not (len(source_frames) == len(generated_frames) == len(masks)):
        raise ValueError("source, generated, and mask frame counts must match")
    output: list[np.ndarray] = []
    for source, generated, mask in zip(source_frames, generated_frames, masks, strict=True):
        if source.shape != generated.shape or source.shape[:2] != mask.shape:
            raise ValueError("composite inputs must have matching dimensions")
        height, width = mask.shape
        feather = max(3, round(min(height, width) * 0.008))
        kernel = feather * 2 + 1
        alpha = cv2.GaussianBlur(mask.astype(np.float32), (kernel, kernel), 0)[..., None]
        mixed = generated.astype(np.float32) * alpha + source.astype(np.float32) * (1.0 - alpha)
        output.append(np.clip(mixed, 0, 255).astype(np.uint8))
    return tuple(output)


def _read_video_frames(path: Path) -> tuple[tuple[np.ndarray, ...], float]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"could not open video: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frames: list[np.ndarray] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            frames.append(frame)
    finally:
        capture.release()
    if not frames or fps <= 0.0:
        raise ValueError("video has no readable frames")
    return tuple(frames), fps


def _write_video_frames(path: Path, frames: tuple[np.ndarray, ...], fps: float) -> None:
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise ValueError("could not create temporary composite video")
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()


async def composite_generated_target(
    *,
    source_path: Path,
    generated_path: Path,
    bbox: dict[str, float],
    seed_frame_index: int,
    output_path: Path,
) -> CompositeResult:
    """Track the generated output itself, then preserve original outside it."""
    source_data, generated_data = await asyncio.gather(
        asyncio.to_thread(_read_video_frames, source_path),
        asyncio.to_thread(_read_video_frames, generated_path),
    )
    source_frames, source_fps = source_data
    generated_frames, generated_fps = generated_data
    frame_count = min(len(source_frames), len(generated_frames))
    base_metrics: dict[str, float | int | str | bool] = {
        "source_frames": len(source_frames),
        "generated_frames": len(generated_frames),
        "source_fps": source_fps,
        "generated_fps": generated_fps,
    }
    if frame_count > MAX_COMPOSITE_FRAMES:
        return CompositeResult(False, None, "clip exceeds bounded output tracking", base_metrics)
    if abs(len(source_frames) - len(generated_frames)) > 1:
        return CompositeResult(False, None, "source/generated frame counts differ", base_metrics)

    source_frames = source_frames[:frame_count]
    generated_frames = generated_frames[:frame_count]
    height, width = source_frames[0].shape[:2]
    generated_frames = tuple(
        frame if frame.shape[:2] == (height, width) else cv2.resize(frame, (width, height))
        for frame in generated_frames
    )
    seed_frame_index = min(max(0, seed_frame_index), frame_count - 1)

    with tempfile.TemporaryDirectory(prefix="fiebatt-output-track-") as temp_root:
        temp_dir = Path(temp_root)
        frame_paths: list[str] = []
        for index, frame in enumerate(generated_frames):
            frame_path = temp_dir / f"generated_{index:04d}.jpg"
            if not cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                raise ValueError("could not write generated tracking frame")
            frame_paths.append(str(frame_path))

        seed = await sam.bbox_to_mask_result(frame_paths[seed_frame_index], bbox)
        track = await sam.track_frames(
            frame_paths,
            seed_frame_index=seed_frame_index,
            seed_mask_path=seed.path,
            max_frames=frame_count,
            max_seconds=30.0,
            include_masks=True,
        )
        decision, masks = evaluate_output_track(
            track,
            frame_count=frame_count,
            frame_shape=(height, width),
            seed_confidence=seed.score,
        )
        metrics = {**base_metrics, **decision.metrics}
        if not decision.allowed:
            return CompositeResult(False, None, decision.reason, metrics)

        composited = await asyncio.to_thread(
            feathered_composite_frames,
            source_frames,
            generated_frames,
            masks,
        )
        silent_path = temp_dir / "composited-silent.mp4"
        await asyncio.to_thread(_write_video_frames, silent_path, composited, source_fps)
        duration = frame_count / source_fps
        await ffmpeg.conform_generated_edit(
            silent_path,
            source_path,
            duration,
            output_path,
        )
    return CompositeResult(True, output_path, decision.reason, metrics)
