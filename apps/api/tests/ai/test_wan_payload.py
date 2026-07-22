from pathlib import Path

import cv2
import numpy as np

from app.ai.services.wan import (
    DEFAULT_I2V_MODEL,
    _build_image_to_video_payload,
    _build_video_edit_payload,
)
from app.services.edit_prompt import planned_edit_prompt
from app.services.generation_window import GenerationWindow, protected_context_prompt


def _write_frame(path: Path, color: tuple[int, int, int]) -> None:
    frame = np.full((360, 640, 3), color, dtype=np.uint8)
    assert cv2.imwrite(str(path), frame)


def test_i2v_payload_uses_full_first_and_last_frames(tmp_path: Path):
    first = tmp_path / "first.jpg"
    last = tmp_path / "last.jpg"
    _write_frame(first, (10, 20, 30))
    _write_frame(last, (30, 20, 10))

    payload = _build_image_to_video_payload(
        "the car bounces once and lands naturally",
        str(first),
        last_frame_path=str(last),
        duration=6,
        resolution="720P",
    )

    assert DEFAULT_I2V_MODEL == "wan2.7-i2v-2026-04-25"
    assert [item["type"] for item in payload["input"]["media"]] == [
        "first_frame",
        "last_frame",
    ]
    assert all(
        item["url"].startswith("data:image/jpeg;base64,")
        for item in payload["input"]["media"]
    )
    assert payload["parameters"] == {
        "resolution": "720P",
        "duration": 6,
        "prompt_extend": False,
        "watermark": False,
    }


def test_i2v_payload_can_leave_future_motion_open(tmp_path: Path):
    first = tmp_path / "first.jpg"
    _write_frame(first, (10, 20, 30))

    payload = _build_image_to_video_payload(
        "the person starts running",
        str(first),
        duration=5,
    )

    assert [item["type"] for item in payload["input"]["media"]] == ["first_frame"]


def test_motion_video_edit_allows_target_motion_but_protects_scene():
    payload = _build_video_edit_payload(
        "Make the selected car bounce once.",
        "https://cdn.example.test/source.mp4",
        motion_edit=True,
    )

    prompt = payload["input"]["prompt"]
    assert prompt.startswith("REQUIRED EDIT — HIGHEST PRIORITY")
    assert "pose, position, velocity, and timing to change" in prompt
    assert "unchanged target motion fails" in prompt
    assert prompt.index("Make the selected car bounce once") < prompt.index("PRESERVE:")
    assert payload["parameters"]["prompt_extend"] is False


def test_appearance_video_edit_keeps_provider_prompt_extension():
    payload = _build_video_edit_payload(
        "Make the selected car green.",
        "https://cdn.example.test/source.mp4",
    )

    assert "Change only the requested target attributes" in payload["input"]["prompt"]
    assert payload["parameters"]["prompt_extend"] is True


def test_complete_motion_prompt_stays_focused_and_action_first(tmp_path: Path):
    reference = tmp_path / "person.png"
    _write_frame(reference, (10, 20, 30))
    instruction = planned_edit_prompt(
        "Make this man jump once",
        {
            "prompt_for_video_edit": (
                "Make the selected man perform exactly one natural jump: bend his "
                "knees, take off, become airborne, land, and continue walking."
            )
        },
    )
    timed = protected_context_prompt(
        instruction,
        GenerationWindow(0.75, 4.25, 0.0, 5.0, True),
        temporal_behavior="temporary",
        effect_extent="motion_path",
    )
    prompt = _build_video_edit_payload(
        timed,
        "https://cdn.example.test/source.mp4",
        reference_frame_path=str(reference),
        motion_edit=True,
    )["input"]["prompt"]

    assert len(prompt.split()) <= 180
    assert prompt.index("Make this man jump once") < prompt.index("MOTION:")
    assert prompt.index("MOTION:") < prompt.index("PRESERVE:")
    assert "0.750 through 4.250" not in prompt
    assert "never delay or weaken the action" in prompt
    assert "do not begin the requested action" not in prompt.lower()
