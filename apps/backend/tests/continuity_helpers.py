"""Small deterministic helpers shared by continuity regression tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "continuity_cases.json"


def load_continuity_cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text())


def legacy_edit_window(duration: float, playhead: float) -> tuple[float, float]:
    """Mirror the pre-adaptive frontend behavior for regression comparison."""
    if duration <= 5.0:
        return 0.0, duration
    center = max(1.5, min(duration - 1.5, playhead))
    return center - 1.5, center + 1.5


def frame_difference(left: np.ndarray, right: np.ndarray) -> float:
    """Return normalized mean absolute pixel difference in [0, 1]."""
    if left.shape != right.shape:
        raise ValueError("frames must have matching shapes")
    left_f = left.astype(np.float32)
    right_f = right.astype(np.float32)
    return float(np.mean(np.abs(left_f - right_f)) / 255.0)


def foreground_centroid(frame: np.ndarray) -> tuple[float, float]:
    """Find a bright synthetic subject centroid for motion-fixture assertions."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    points = cv2.findNonZero((gray > 127).astype(np.uint8))
    if points is None:
        raise ValueError("frame has no foreground subject")
    x, y, width, height = cv2.boundingRect(points)
    return x + width / 2.0, y + height / 2.0


def subject_velocity(previous: np.ndarray, current: np.ndarray) -> tuple[float, float]:
    prev_x, prev_y = foreground_centroid(previous)
    curr_x, curr_y = foreground_centroid(current)
    return curr_x - prev_x, curr_y - prev_y
