import pytest

from app.schemas.edit_plan import EditCore, GenerationContext, LocalRangeResolution
from app.services.generation_window import (
    protected_context_prompt,
    resolve_generation_window,
)


def _planned_context(start: float = 0.0, end: float = 6.0) -> dict:
    core = EditCore(start_ts=0.5, end_ts=4.0)
    return LocalRangeResolution(
        edit_core=core,
        generation_context=GenerationContext(
            start_ts=start,
            end_ts=end,
            edit_core=core,
        ),
        occurrence_start=0.0,
        occurrence_end=7.0,
        analysis_start=0.0,
        analysis_end=7.0,
        confidence=0.9,
    ).model_dump(mode="json")


def test_legacy_window_ignores_dormant_planned_context():
    window = resolve_generation_window(
        0.5,
        4.0,
        payload={
            "adaptive_context_enabled": False,
            "planned_context": _planned_context(),
        },
        project_duration=10.0,
    )
    assert window.adaptive is False
    assert (window.context_start, window.context_end) == (0.5, 4.0)


def test_adaptive_window_uses_padded_context_and_relative_offsets():
    window = resolve_generation_window(
        0.5,
        4.0,
        payload={
            "adaptive_context_enabled": True,
            "planned_context": _planned_context(),
        },
        project_duration=10.0,
    )
    assert window.adaptive is True
    assert (window.context_start, window.context_end) == (0.0, 6.0)
    assert window.edit_start_offset == 0.5
    assert window.edit_end_offset == 4.0
    assert window.pre_handle == 0.5
    assert window.post_handle == 2.0


def test_adaptive_window_clamps_context_at_video_end():
    raw = _planned_context(start=0.0, end=6.0)
    raw["edit_core"] = {"start_ts": 4.0, "end_ts": 5.5}
    raw["generation_context"]["edit_core"] = raw["edit_core"]
    window = resolve_generation_window(
        4.0,
        5.5,
        payload={"adaptive_context_enabled": True, "planned_context": raw},
        project_duration=5.5,
    )
    assert window.context_end == 5.5
    assert window.post_handle == 0.0


def test_adaptive_window_rejects_mismatched_core():
    with pytest.raises(ValueError, match="does not match"):
        resolve_generation_window(
            1.0,
            4.5,
            payload={
                "adaptive_context_enabled": True,
                "planned_context": _planned_context(),
            },
            project_duration=10.0,
        )


def test_protected_context_prompt_names_edit_offsets_and_handles():
    window = resolve_generation_window(
        0.5,
        4.0,
        payload={
            "adaptive_context_enabled": True,
            "planned_context": _planned_context(),
        },
        project_duration=10.0,
    )
    rendered = protected_context_prompt("Make the person jump.", window)
    assert "0.500 through 4.000" in rendered
    assert "first 0.500 seconds" in rendered
    assert "final 2.000 seconds" in rendered
    assert "locked source handles" in rendered
    assert "identical to the source" in rendered
    assert "Do not use a cut, fade, dissolve" in rendered
    assert rendered.endswith("Make the person jump.")
