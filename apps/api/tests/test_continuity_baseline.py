from __future__ import annotations

import numpy as np
import pytest

from continuity_helpers import (
    frame_difference,
    legacy_edit_window,
    load_continuity_cases,
    subject_velocity,
)


@pytest.mark.parametrize("case", load_continuity_cases(), ids=lambda case: case["name"])
def test_legacy_window_fixtures_capture_current_behavior(case: dict) -> None:
    assert legacy_edit_window(case["duration"], case["playhead"]) == pytest.approx(
        (case["legacy_start"], case["legacy_end"])
    )


def test_frame_difference_detects_visible_boundary_change() -> None:
    original = np.zeros((32, 32, 3), dtype=np.uint8)
    generated = original.copy()
    generated[8:24, 8:24] = 255

    assert frame_difference(original, original) == 0.0
    assert frame_difference(original, generated) == pytest.approx(0.25)


def test_subject_velocity_distinguishes_matching_pose_from_matching_motion() -> None:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    boundary = np.zeros_like(before)
    after = np.zeros_like(before)
    before[12:16, 8:12] = 255
    boundary[12:16, 12:16] = 255
    after[12:16, 10:14] = 255

    incoming_velocity = subject_velocity(before, boundary)
    outgoing_velocity = subject_velocity(boundary, after)

    assert incoming_velocity == pytest.approx((4.0, 0.0))
    assert outgoing_velocity == pytest.approx((-2.0, 0.0))
    assert incoming_velocity != outgoing_velocity
