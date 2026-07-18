"""Shared visual and motion scoring for hard-cut seam selection."""
from __future__ import annotations

from dataclasses import dataclass

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


def seam_score(sample: SeamFrames, bbox: dict[str, float]) -> float:
    """Weight protected background and cross-cut motion above target appearance."""
    target = _bbox_mask(sample.left_at.shape, bbox, invert=False)
    background = _bbox_mask(sample.left_at.shape, bbox, invert=True)
    appearance_background = _mean_delta(sample.left_at, sample.right_at, background)
    appearance_target = _mean_delta(sample.left_at, sample.right_at, target)
    left_motion = cv2.absdiff(sample.left_before, sample.left_at)
    right_motion = cv2.absdiff(sample.right_at, sample.right_after)
    motion_delta = _mean_delta(left_motion, right_motion, np.ones_like(target))
    return 0.50 * appearance_background + 0.20 * appearance_target + 0.30 * motion_delta


def select_best_seam(
    samples: list[SeamFrames],
    *,
    bbox: dict[str, float],
    prefer_late: bool = False,
    max_score: float = MAX_SEAM_SCORE,
) -> SeamChoice:
    choice = rank_best_seam(samples, bbox=bbox, prefer_late=prefer_late)
    if choice.score > max_score:
        raise ValueError(
            f"overlap failed seam validation ({choice.score:.3f} > {max_score:.3f})"
        )
    return choice


def rank_best_seam(
    samples: list[SeamFrames],
    *,
    bbox: dict[str, float],
    prefer_late: bool = False,
) -> SeamChoice:
    """Return the strongest candidate even when it is too weak to accept."""
    if not samples:
        raise ValueError("overlap has no seam samples")
    scored = [(seam_score(sample, bbox), sample.timestamp) for sample in samples]
    score, timestamp = min(
        scored,
        key=lambda item: (item[0], -item[1] if prefer_late else item[1]),
    )
    return SeamChoice(timestamp=timestamp, score=score, samples=len(samples))
