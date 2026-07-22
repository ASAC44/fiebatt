import cv2
import numpy as np
import pytest

from app.services.seam_matching import SeamFrames, select_best_seam


BBOX = {"x": 0.25, "y": 0.2, "w": 0.3, "h": 0.6}


def _frame(value: int) -> np.ndarray:
    return np.full((32, 48, 3), value, dtype=np.uint8)


def _sample(timestamp: float, left: int, right: int) -> SeamFrames:
    return SeamFrames(
        timestamp=timestamp,
        left_before=_frame(left),
        left_at=_frame(left),
        right_at=_frame(right),
        right_after=_frame(right),
    )


def test_entry_prefers_latest_equally_safe_frame():
    choice = select_best_seam(
        [_sample(0.25, 40, 40), _sample(0.50, 40, 40)],
        bbox=BBOX,
        prefer_late=True,
    )

    assert choice.timestamp == pytest.approx(0.50)


def test_exit_prefers_earliest_equally_safe_frame():
    choice = select_best_seam(
        [_sample(3.25, 40, 40), _sample(3.50, 40, 40)],
        bbox=BBOX,
    )

    assert choice.timestamp == pytest.approx(3.25)


def test_unrelated_frames_are_rejected_without_a_fade():
    with pytest.raises(ValueError, match="failed seam validation"):
        select_best_seam([_sample(0.5, 0, 255)], bbox=BBOX)


def test_motion_matching_normalizes_provider_resolution():
    source = _frame(40)
    generated = cv2.resize(source, (96, 64))
    choice = select_best_seam(
        [
            SeamFrames(
                timestamp=0.5,
                left_before=source,
                left_at=source,
                right_at=generated,
                right_after=generated,
            )
        ],
        bbox=BBOX,
    )

    assert choice.safe
