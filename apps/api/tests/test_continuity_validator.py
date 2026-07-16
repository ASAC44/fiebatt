from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from app.services.continuity_validator import (
    ContinuitySamples,
    score_continuity_samples,
)


BBOX = {"x": 0.25, "y": 0.25, "w": 0.25, "h": 0.25}


def _frame(x: int, *, background: int = 20) -> np.ndarray:
    frame = np.full((64, 64, 3), background, dtype=np.uint8)
    cv2.rectangle(frame, (x, 24), (x + 12, 40), (220, 220, 220), -1)
    return frame


def _clean_samples() -> ContinuitySamples:
    moving = tuple(_frame(x) for x in (16, 18, 20))
    return ContinuitySamples(
        source_pre=moving,
        generated_pre=moving,
        source_post=moving,
        generated_post=moving,
        generated_entry=moving,
        generated_exit=moving,
        source_tail=moving,
        generated_tail=moving,
    )


def test_matching_handles_and_motion_pass():
    report = score_continuity_samples(
        _clean_samples(),
        bbox=BBOX,
        source_duration=6.0,
        generated_duration=6.02,
        source_fps=24.0,
        generated_fps=24.0,
    )
    assert report.passed is True
    assert report.issues == []
    assert report.sampled_frames == 24


def test_changed_protected_background_fails_both_handles():
    clean = _clean_samples()
    changed = tuple(_frame(x, background=180) for x in (16, 18, 20))
    report = score_continuity_samples(
        replace(clean, generated_pre=changed, generated_post=changed),
        bbox=BBOX,
        source_duration=6.0,
        generated_duration=6.0,
        source_fps=24.0,
        generated_fps=24.0,
    )
    codes = {issue.code for issue in report.issues}
    assert "pre_handle_background_delta" in codes
    assert "post_handle_background_delta" in codes


def test_exit_motion_reversal_is_detected_over_three_frames():
    clean = _clean_samples()
    report = score_continuity_samples(
        replace(clean, generated_exit=tuple(_frame(x) for x in (16, 22, 16))),
        bbox=BBOX,
        source_duration=6.0,
        generated_duration=6.0,
        source_fps=24.0,
        generated_fps=24.0,
    )
    assert any(issue.code == "exit_subject_motion_jump" for issue in report.issues)


def test_duration_fps_and_frozen_tail_are_detected():
    clean = _clean_samples()
    frozen = (_frame(16), _frame(16), _frame(16))
    report = score_continuity_samples(
        replace(clean, generated_tail=frozen),
        bbox=BBOX,
        source_duration=6.0,
        generated_duration=5.5,
        source_fps=24.0,
        generated_fps=30.0,
    )
    codes = {issue.code for issue in report.issues}
    assert {"duration_delta_s", "fps_delta_ratio", "frozen_tail"} <= codes
    assert "duration_delta_s" in report.correction_evidence()
