"""Bounded local object tracking without a separate GPU service."""
from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import numpy as np

from app.ai.services.sam import TrackResult


MIN_APPEARANCE_SCORE = 0.05
MIN_BOX_PIXELS = 8
MAX_CENTER_JUMP_BOXES = 1.5


def _normalized_box(
    pixel_box: tuple[float, float, float, float],
    width: int,
    height: int,
) -> dict[str, float]:
    x, y, box_width, box_height = pixel_box
    return {
        "x": max(0.0, x / width),
        "y": max(0.0, y / height),
        "w": min(1.0, box_width / width),
        "h": min(1.0, box_height / height),
    }


def _pixel_box(
    bbox: dict[str, float], width: int, height: int
) -> tuple[float, float, float, float]:
    x = max(0, min(width - 1, round(float(bbox["x"]) * width)))
    y = max(0, min(height - 1, round(float(bbox["y"]) * height)))
    right = max(x + 1, min(width, round((float(bbox["x"]) + float(bbox["w"])) * width)))
    bottom = max(y + 1, min(height, round((float(bbox["y"]) + float(bbox["h"])) * height)))
    return float(x), float(y), float(right - x), float(bottom - y)


def _box_from_mask(
    seed_mask_path: str | None,
    *,
    width: int,
    height: int,
) -> tuple[float, float, float, float] | None:
    if not seed_mask_path or not Path(seed_mask_path).is_file():
        return None
    mask = cv2.imread(seed_mask_path, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        return None
    if mask.shape != (height, width):
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    points = cv2.findNonZero((mask > 127).astype(np.uint8))
    if points is None:
        return None
    x, y, box_width, box_height = cv2.boundingRect(points)
    if box_width < MIN_BOX_PIXELS or box_height < MIN_BOX_PIXELS:
        return None
    return float(x), float(y), float(box_width), float(box_height)


def _histogram(frame: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    height, width = frame.shape[:2]
    x, y, box_width, box_height = box
    left = max(0, min(width - 1, round(x)))
    top = max(0, min(height - 1, round(y)))
    right = max(left + 1, min(width, round(x + box_width)))
    bottom = max(top + 1, min(height, round(y + box_height)))
    crop = frame[top:bottom, left:right]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [24, 24], [0, 180, 0, 256])
    return cv2.normalize(histogram, histogram).flatten()


def _valid_box(
    box: tuple[float, float, float, float], width: int, height: int
) -> bool:
    x, y, box_width, box_height = box
    return (
        np.isfinite(box).all()
        and box_width >= MIN_BOX_PIXELS
        and box_height >= MIN_BOX_PIXELS
        and x < width
        and y < height
        and x + box_width > 0
        and y + box_height > 0
        and box_width * box_height < width * height * 0.85
    )


def _track_direction(
    frames: list[np.ndarray],
    *,
    seed_index: int,
    seed_box: tuple[float, float, float, float],
    seed_histogram: np.ndarray,
    step: int,
) -> dict[int, dict]:
    tracker = cv2.TrackerMIL_create()
    tracker.init(frames[seed_index], tuple(round(value) for value in seed_box))
    height, width = frames[seed_index].shape[:2]
    output: dict[int, dict] = {}
    previous_box = seed_box
    index = seed_index + step
    while 0 <= index < len(frames):
        tracked, raw_box = tracker.update(frames[index])
        box = tuple(float(value) for value in raw_box)
        if not tracked or not _valid_box(box, width, height):
            output[index] = {
                "frame_index": index,
                "state": "lost",
                "confidence": 0.0,
                "bbox": None,
            }
            break
        previous_center = (
            previous_box[0] + previous_box[2] / 2,
            previous_box[1] + previous_box[3] / 2,
        )
        current_center = (box[0] + box[2] / 2, box[1] + box[3] / 2)
        center_jump = (
            (current_center[0] - previous_center[0]) ** 2
            + (current_center[1] - previous_center[1]) ** 2
        ) ** 0.5
        allowed_jump = MAX_CENTER_JUMP_BOXES * max(
            MIN_BOX_PIXELS,
            min(previous_box[2], previous_box[3]),
        )
        if center_jump > allowed_jump:
            output[index] = {
                "frame_index": index,
                "state": "lost",
                "confidence": 0.0,
                "bbox": None,
            }
            break
        appearance = cv2.compareHist(
            seed_histogram,
            _histogram(frames[index], box),
            cv2.HISTCMP_CORREL,
        )
        confidence = max(0.0, min(1.0, (float(appearance) + 1.0) / 2.0))
        if appearance < MIN_APPEARANCE_SCORE:
            output[index] = {
                "frame_index": index,
                "state": "lost",
                "confidence": confidence,
                "bbox": None,
            }
            break
        output[index] = {
            "frame_index": index,
            "state": "tracked",
            "confidence": confidence,
            "bbox": _normalized_box(box, width, height),
        }
        previous_box = box
        index += step
    return output


def _track(
    frame_paths: list[str],
    *,
    seed_frame_index: int,
    bbox: dict[str, float],
    seed_mask_path: str | None,
) -> TrackResult:
    frames = [cv2.imread(path, cv2.IMREAD_COLOR) for path in frame_paths]
    if any(frame is None for frame in frames):
        raise ValueError("local tracker could not read analysis frames")
    values = [frame for frame in frames if frame is not None]
    if not 0 <= seed_frame_index < len(values):
        raise ValueError("seed frame is outside analysis frames")
    height, width = values[seed_frame_index].shape[:2]
    seed_box = _box_from_mask(
        seed_mask_path,
        width=width,
        height=height,
    ) or _pixel_box(bbox, width, height)
    seed_histogram = _histogram(values[seed_frame_index], seed_box)
    output = {
        seed_frame_index: {
            "frame_index": seed_frame_index,
            "state": "tracked",
            "confidence": 1.0,
            "bbox": _normalized_box(seed_box, width, height),
        }
    }
    output.update(
        _track_direction(
            values,
            seed_index=seed_frame_index,
            seed_box=seed_box,
            seed_histogram=seed_histogram,
            step=-1,
        )
    )
    output.update(
        _track_direction(
            values,
            seed_index=seed_frame_index,
            seed_box=seed_box,
            seed_histogram=seed_histogram,
            step=1,
        )
    )
    ordered = [output[index] for index in sorted(output)]
    return TrackResult(
        tracker="opencv_mil",
        frames=ordered,
        processed_start_index=ordered[0]["frame_index"],
        processed_end_index=ordered[-1]["frame_index"],
    )


async def track_frames(
    frame_paths: list[str],
    *,
    seed_frame_index: int,
    bbox: dict[str, float] | None = None,
    seed_mask_path: str | None = None,
    max_frames: int = 240,
    max_seconds: float = 30.0,
    include_masks: bool = False,
) -> TrackResult:
    """Track selected target locally; exact per-frame masks are not fabricated."""
    del max_seconds
    if include_masks:
        raise ValueError("CPU occurrence tracker does not produce precise video masks")
    if bbox is None:
        raise ValueError("CPU occurrence tracker requires a bounding box")
    bounded = frame_paths[:max_frames]
    return await asyncio.to_thread(
        _track,
        bounded,
        seed_frame_index=seed_frame_index,
        bbox=bbox,
        seed_mask_path=seed_mask_path,
    )
