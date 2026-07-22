import numpy as np
import pytest

from app.services.generation_window import GenerationWindow
from app.services.continuity_validator import ContinuityIssue, ContinuityReport
from app.services.local_seam import (
    _candidate_times,
    continuity_at_selected_seams,
    select_local_seams,
)
from app.services.seam_matching import SeamFrames


BBOX = {"x": 0.25, "y": 0.2, "w": 0.3, "h": 0.6}
WINDOW = GenerationWindow(3.0, 5.0, 2.0, 6.0, adaptive=True)


def _frame(value: int) -> np.ndarray:
    return np.full((24, 32, 3), value, dtype=np.uint8)


def _sample(timestamp: float, left: int, right: int) -> SeamFrames:
    return SeamFrames(timestamp, _frame(left), _frame(left), _frame(right), _frame(right))


def _regional_sample(timestamp: float, *, changed_side: str) -> SeamFrames:
    source = np.zeros((24, 32, 3), dtype=np.uint8)
    generated = source.copy()
    if changed_side == "left":
        generated[:, :16] = 255
    else:
        generated[:, 16:] = 255
    return SeamFrames(timestamp, source, source, generated, generated)


def _motion_sample(timestamp: float, *, outgoing: str) -> SeamFrames:
    def square(x: int, y: int) -> np.ndarray:
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        frame[y : y + 16, x : x + 16] = 255
        return frame

    left_before = square(16, 24)
    left_at = square(20, 24)
    right_at = square(20, 24)
    right_after = square(24, 24) if outgoing == "walking" else square(20, 12)
    return SeamFrames(timestamp, left_before, left_at, right_at, right_after)


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


def test_candidate_reads_never_seek_to_exact_media_end():
    duration = 5.25
    fps = 24.0
    step = 1.0 / fps

    candidates = _candidate_times(4.5, duration, fps)

    assert candidates
    assert max(candidates) + step < duration


def test_local_seams_weight_background_using_tracked_target_position():
    samples = [
        _regional_sample(0.25, changed_side="right"),
        _regional_sample(0.75, changed_side="left"),
    ]
    fixed = select_local_seams(
        entry_samples=samples,
        exit_samples=[],
        bbox={"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0},
        window=WINDOW,
    )
    tracked = select_local_seams(
        entry_samples=samples,
        exit_samples=[],
        bbox={"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0},
        window=WINDOW,
        tracked_frames=[
            {
                "timestamp": 2.25,
                "state": "tracked",
                "bbox": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0},
            },
            {
                "timestamp": 2.75,
                "state": "tracked",
                "bbox": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0},
            },
        ],
    )

    assert fixed.entry.timestamp == pytest.approx(0.75)
    assert tracked.entry.timestamp == pytest.approx(0.25)
    assert tracked.target_weighting == "tracked_bbox"


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


def test_frame_matching_keeps_nominal_cut_for_tiny_improvement():
    selection = select_local_seams(
        entry_samples=[_sample(0.75, 30, 29)],
        exit_samples=[],
        nominal_entry_sample=_sample(1.0, 30, 31),
        bbox=BBOX,
        window=WINDOW,
    )

    assert selection.media_start == pytest.approx(1.0)
    assert selection.entry_strategy == "nominal"


def test_frame_matching_moves_unsafe_nominal_cut_to_safe_candidate():
    selection = select_local_seams(
        entry_samples=[_sample(0.75, 30, 30)],
        exit_samples=[],
        nominal_entry_sample=_sample(1.0, 0, 255),
        bbox=BBOX,
        window=WINDOW,
    )

    assert selection.media_start == pytest.approx(0.75)
    assert selection.entry_strategy == "matched"
    assert selection.passed is True


def test_frame_matching_rejects_walk_to_airborne_motion_cut():
    selection = select_local_seams(
        entry_samples=[_motion_sample(0.1, outgoing="walking")],
        exit_samples=[],
        nominal_entry_sample=_motion_sample(0.75, outgoing="jumping"),
        bbox={"x": 0.2, "y": 0.1, "w": 0.6, "h": 0.8},
        window=WINDOW,
    )

    assert selection.media_start == pytest.approx(0.1)
    assert selection.entry_strategy == "matched"
    assert selection.passed is True


def test_selected_seams_replace_nominal_handle_failures_but_keep_media_failures():
    selection = select_local_seams(
        entry_samples=[_sample(0.5, 30, 30)],
        exit_samples=[_sample(3.5, 30, 30)],
        bbox=BBOX,
        window=WINDOW,
    )
    base = ContinuityReport(
        passed=False,
        metrics={"pre_handle_background_delta": 0.113, "duration_delta_s": 0.2},
        issues=[
            ContinuityIssue("pre_handle_background_delta", 0.113, 0.09, "pre"),
            ContinuityIssue("duration_delta_s", 0.2, 0.16),
        ],
        sampled_frames=12,
    )

    report = continuity_at_selected_seams(base, selection)

    assert report.passed is False
    assert [issue.code for issue in report.issues] == ["duration_delta_s"]
    assert report.metrics["entry_frame_match_score"] == pytest.approx(0.0)
