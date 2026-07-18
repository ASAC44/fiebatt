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
REIDENTIFY_SCORE = 0.58
CONFIDENT_ABSENCE_SCORE = 0.30
REIDENTIFY_MAX_DIMENSION = 480
REIDENTIFY_SCALES = (0.85, 1.0, 1.15)


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
    right = max(
        x + 1,
        min(width, round((float(bbox["x"]) + float(bbox["w"])) * width)),
    )
    bottom = max(
        y + 1,
        min(height, round((float(bbox["y"]) + float(bbox["h"])) * height)),
    )
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


def _crop(
    frame: np.ndarray,
    box: tuple[float, float, float, float],
) -> np.ndarray:
    height, width = frame.shape[:2]
    x, y, box_width, box_height = box
    left = max(0, min(width - 1, round(x)))
    top = max(0, min(height - 1, round(y)))
    right = max(left + 1, min(width, round(x + box_width)))
    bottom = max(top + 1, min(height, round(y + box_height)))
    return frame[top:bottom, left:right]


def _matching_image(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def _feature_points(
    frame: np.ndarray,
    box: tuple[float, float, float, float],
) -> np.ndarray | None:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mask = np.zeros(gray.shape, dtype=np.uint8)
    x, y, width, height = box
    left = max(0, round(x))
    top = max(0, round(y))
    right = min(gray.shape[1], round(x + width))
    bottom = min(gray.shape[0], round(y + height))
    mask[top:bottom, left:right] = 255
    return cv2.goodFeaturesToTrack(
        gray,
        mask=mask,
        maxCorners=80,
        qualityLevel=0.01,
        minDistance=4,
        blockSize=5,
    )


def _flow_update(
    previous_frame: np.ndarray,
    frame: np.ndarray,
    points: np.ndarray | None,
    box: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float, float] | None, np.ndarray | None]:
    if points is None or len(points) < 3:
        return None, None
    previous_gray = cv2.cvtColor(previous_frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    moved, status, error = cv2.calcOpticalFlowPyrLK(
        previous_gray,
        gray,
        points,
        None,
        winSize=(21, 21),
        maxLevel=3,
        criteria=(
            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
            20,
            0.03,
        ),
    )
    if moved is None or status is None:
        return None, None
    valid = status.reshape(-1) == 1
    if error is not None:
        errors = error.reshape(-1)
        finite = errors[valid & np.isfinite(errors)]
        if finite.size:
            valid &= errors <= max(20.0, float(np.median(finite)) * 3.0)
    old = points.reshape(-1, 2)[valid]
    new = moved.reshape(-1, 2)[valid]
    if len(new) < 3:
        return None, None
    deltas = new - old
    median_delta = np.median(deltas, axis=0)
    deviations = np.linalg.norm(deltas - median_delta, axis=1)
    limit = max(2.0, float(np.median(deviations)) * 3.0)
    inliers = deviations <= limit
    old = old[inliers]
    new = new[inliers]
    if len(new) < 3:
        return None, None
    delta_x, delta_y = np.median(new - old, axis=0)
    x, y, width, height = box
    updated = (x + float(delta_x), y + float(delta_y), width, height)
    return updated, new.reshape(-1, 1, 2).astype(np.float32)


def _reidentify(
    frame: np.ndarray,
    *,
    seed_template: np.ndarray,
    seed_histogram: np.ndarray,
) -> tuple[tuple[float, float, float, float] | None, float]:
    """Find seed subject anywhere in a later frame after tracker loss.

    Optical flow handles ordinary motion. Full-frame, multi-scale matching
    handles short occlusion, fast movement, or drift without assuming the
    subject disappeared on the first bad update.
    """
    height, width = frame.shape[:2]
    downscale = min(1.0, REIDENTIFY_MAX_DIMENSION / max(height, width))
    search = (
        cv2.resize(
            frame,
            None,
            fx=downscale,
            fy=downscale,
            interpolation=cv2.INTER_AREA,
        )
        if downscale < 1.0
        else frame
    )
    search_image = _matching_image(search)
    best_box: tuple[float, float, float, float] | None = None
    best_score = -1.0

    for scale in REIDENTIFY_SCALES:
        template_width = max(
            MIN_BOX_PIXELS,
            round(seed_template.shape[1] * downscale * scale),
        )
        template_height = max(
            MIN_BOX_PIXELS,
            round(seed_template.shape[0] * downscale * scale),
        )
        if template_width >= search.shape[1] or template_height >= search.shape[0]:
            continue
        template = cv2.resize(
            seed_template,
            (template_width, template_height),
            interpolation=cv2.INTER_AREA,
        )
        response = cv2.matchTemplate(
            search_image,
            _matching_image(template),
            cv2.TM_CCOEFF_NORMED,
        )
        _, template_score, _, location = cv2.minMaxLoc(response)
        candidate = (
            location[0] / downscale,
            location[1] / downscale,
            template_width / downscale,
            template_height / downscale,
        )
        appearance = cv2.compareHist(
            seed_histogram,
            _histogram(frame, candidate),
            cv2.HISTCMP_CORREL,
        )
        appearance_score = max(0.0, min(1.0, (float(appearance) + 1.0) / 2.0))
        combined = (
            0.75 * max(0.0, float(template_score)) + 0.25 * appearance_score
        )
        if combined > best_score:
            best_box = candidate
            best_score = combined

    return best_box, max(0.0, min(1.0, best_score))


def _track_direction(
    frames: list[np.ndarray],
    *,
    seed_index: int,
    seed_box: tuple[float, float, float, float],
    seed_histogram: np.ndarray,
    seed_template: np.ndarray,
    step: int,
) -> dict[int, dict]:
    height, width = frames[seed_index].shape[:2]
    output: dict[int, dict] = {}
    previous_box = seed_box
    previous_frame = frames[seed_index]
    points = _feature_points(previous_frame, previous_box)
    can_track_motion = True
    index = seed_index + step
    while 0 <= index < len(frames):
        flow_box, moved_points = (
            _flow_update(previous_frame, frames[index], points, previous_box)
            if can_track_motion
            else (None, None)
        )
        box = flow_box or previous_box
        valid_update = flow_box is not None and _valid_box(box, width, height)
        if valid_update:
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
            appearance = cv2.compareHist(
                seed_histogram,
                _histogram(frames[index], box),
                cv2.HISTCMP_CORREL,
            )
            valid_update = (
                center_jump <= allowed_jump and appearance >= MIN_APPEARANCE_SCORE
            )
            confidence = max(0.0, min(1.0, (float(appearance) + 1.0) / 2.0))
        else:
            confidence = 0.0

        if not valid_update:
            reidentified_box, reidentified_score = _reidentify(
                frames[index],
                seed_template=seed_template,
                seed_histogram=seed_histogram,
            )
            if (
                reidentified_box is not None
                and reidentified_score >= REIDENTIFY_SCORE
            ):
                box = reidentified_box
                confidence = reidentified_score
                moved_points = _feature_points(frames[index], reidentified_box)
                can_track_motion = True
            else:
                output[index] = {
                    "frame_index": index,
                    "state": (
                        "lost"
                        if reidentified_score < CONFIDENT_ABSENCE_SCORE
                        else "uncertain"
                    ),
                    "confidence": reidentified_score,
                    "bbox": None,
                }
                can_track_motion = False
                index += step
                continue

        output[index] = {
            "frame_index": index,
            "state": "tracked",
            "confidence": confidence,
            "bbox": _normalized_box(box, width, height),
        }
        previous_box = box
        previous_frame = frames[index]
        points = (
            moved_points
            if moved_points is not None and len(moved_points) >= 3
            else _feature_points(previous_frame, previous_box)
        )
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
    seed_template = _crop(values[seed_frame_index], seed_box)
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
            seed_template=seed_template,
            step=-1,
        )
    )
    output.update(
        _track_direction(
            values,
            seed_index=seed_frame_index,
            seed_box=seed_box,
            seed_histogram=seed_histogram,
            seed_template=seed_template,
            step=1,
        )
    )
    ordered = [output[index] for index in sorted(output)]
    return TrackResult(
        tracker="opencv_optical_flow_reid",
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
