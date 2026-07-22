"""Shared visual and motion scoring for hard-cut seam selection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np


MAX_SEAM_SCORE = 0.22


@dataclass(frozen=True, slots=True)
class SeamFrames:
    timestamp: float
    left_before: np.ndarray
    left_at: np.ndarray
    right_at: np.ndarray
    right_after: np.ndarray


@dataclass(frozen=True, slots=True)
class SeamChoice:
    timestamp: float
    score: float
    samples: int

    def metadata(self) -> dict[str, float | int]:
        return {
            "timestamp": self.timestamp,
            "score": self.score,
            "samples": self.samples,
        }


def _bbox_mask(shape: tuple[int, ...], bbox: dict[str, float], *, invert: bool) -> np.ndarray:
    height, width = shape[:2]
    left = max(0, round(float(bbox["x"]) * width))
    top = max(0, round(float(bbox["y"]) * height))
    right = min(width, round((float(bbox["x"]) + float(bbox["w"])) * width))
    bottom = min(height, round((float(bbox["y"]) + float(bbox["h"])) * height))
    mask = np.zeros((height, width), dtype=bool)
    mask[top:bottom, left:right] = True
    return ~mask if invert else mask


def _mean_delta(left: np.ndarray, right: np.ndarray, mask: np.ndarray) -> float:
    if left.shape != right.shape:
        right = cv2.resize(right, (left.shape[1], left.shape[0]))
    selected = cv2.absdiff(left, right).astype(np.float32)[mask] / 255.0
    return float(selected.mean()) if selected.size else 0.0


def _motion_vector(left: np.ndarray, right: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if left.shape != right.shape:
        right = cv2.resize(right, (left.shape[1], left.shape[0]))
    flow = cv2.calcOpticalFlowFarneback(
        cv2.cvtColor(left, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(right, cv2.COLOR_BGR2GRAY),
        None,
        0.5,
        3,
        15,
        3,
        5,
        1.2,
        0,
    )
    selected = flow[mask]
    if selected.size == 0:
        return np.zeros(2, dtype=np.float32)
    return np.median(selected, axis=0)


def _motion_vector_delta(sample: SeamFrames, target: np.ndarray) -> float:
    incoming = _motion_vector(sample.left_before, sample.left_at, target)
    outgoing = _motion_vector(sample.right_at, sample.right_after, target)
    denominator = float(np.linalg.norm(incoming) + np.linalg.norm(outgoing) + 0.5)
    return float(np.linalg.norm(incoming - outgoing) / denominator)


def seam_score(sample: SeamFrames, bbox: dict[str, float]) -> float:
    """Score both visual matching and the target's motion across a hard cut."""
    target = _bbox_mask(sample.left_at.shape, bbox, invert=False)
    background = _bbox_mask(sample.left_at.shape, bbox, invert=True)
    appearance_background = _mean_delta(sample.left_at, sample.right_at, background)
    appearance_target = _mean_delta(sample.left_at, sample.right_at, target)
    left_motion = cv2.absdiff(sample.left_before, sample.left_at)
    right_motion = cv2.absdiff(sample.right_at, sample.right_after)
    motion_delta = _mean_delta(left_motion, right_motion, np.ones_like(target))
    appearance_score = (
        0.50 * appearance_background
        + 0.20 * appearance_target
        + 0.30 * motion_delta
    )
    # Pixel differences alone miss a walk→airborne cut when the moving subject
    # occupies little of the full frame. Dense-flow direction makes that jump
    # visible without penalizing an intentional action elsewhere in the clip.
    target_motion_delta = _motion_vector_delta(sample, target)
    return 0.60 * appearance_score + 0.40 * target_motion_delta


def select_best_seam(
    samples: list[SeamFrames],
    *,
    bbox: dict[str, float],
    bbox_for_timestamp: Callable[[float], dict[str, float]] | None = None,
    prefer_late: bool = False,
    max_score: float = MAX_SEAM_SCORE,
) -> SeamChoice:
    choice = rank_best_seam(
        samples,
        bbox=bbox,
        bbox_for_timestamp=bbox_for_timestamp,
        prefer_late=prefer_late,
    )
    if choice.score > max_score:
        raise ValueError(
            f"overlap failed seam validation ({choice.score:.3f} > {max_score:.3f})"
        )
    return choice


def rank_best_seam(
    samples: list[SeamFrames],
    *,
    bbox: dict[str, float],
    bbox_for_timestamp: Callable[[float], dict[str, float]] | None = None,
    prefer_late: bool = False,
) -> SeamChoice:
    """Return the strongest candidate even when it is too weak to accept."""
    if not samples:
        raise ValueError("overlap has no seam samples")
    scored = [
        (
            seam_score(
                sample,
                bbox_for_timestamp(sample.timestamp)
                if bbox_for_timestamp is not None
                else bbox,
            ),
            sample.timestamp,
        )
        for sample in samples
    ]
    score, timestamp = min(
        scored,
        key=lambda item: (item[0], -item[1] if prefer_late else item[1]),
    )
    return SeamChoice(timestamp=timestamp, score=score, samples=len(samples))
