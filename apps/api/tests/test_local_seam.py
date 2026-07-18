import numpy as np
import pytest

from app.services.generation_window import GenerationWindow
from app.services.local_seam import select_local_seams
from app.services.seam_matching import SeamFrames


BBOX = {"x": 0.25, "y": 0.2, "w": 0.3, "h": 0.6}
WINDOW = GenerationWindow(3.0, 5.0, 2.0, 6.0, adaptive=True)


def _frame(value: int) -> np.ndarray:
    return np.full((24, 32, 3), value, dtype=np.uint8)


def _sample(timestamp: float, left: int, right: int) -> SeamFrames:
    return SeamFrames(timestamp, _frame(left), _frame(left), _frame(right), _frame(right))


def test_local_seams_keep_full_context_until_matching_cut_frames():
    selection = select_local_seams(
        entry_samples=[_sample(0.25, 30, 31), _sample(0.75, 30, 30)],
        exit_samples=[_sample(3.25, 30, 30), _sample(3.75, 30, 31)],
        bbox=BBOX,
        window=WINDOW,
    )

    assert selection.passed is True
    assert selection.media_start == pytest.approx(0.75)
    assert selection.media_end == pytest.approx(3.25)
    assert selection.timeline_start == pytest.approx(2.75)
    assert selection.timeline_end == pytest.approx(5.25)


def test_local_seams_report_unsafe_boundaries_without_blending():
    selection = select_local_seams(
        entry_samples=[_sample(0.5, 0, 255)],
        exit_samples=[_sample(3.5, 0, 255)],
        bbox=BBOX,
        window=WINDOW,
    )

    assert selection.passed is False
    assert len(selection.issues) == 2
    assert "entry_frame_match_score" in selection.issues[0]
