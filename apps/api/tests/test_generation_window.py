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
    assert "first 0.500s" in rendered
    assert "final 2.000s" in rendered
    assert "as continuity references, not restrictions" in rendered
    assert "No cut, fade, freeze" in rendered
    assert rendered.startswith("Make the person jump.")


def test_full_clip_edit_is_not_suppressed_by_zero_length_handles():
    core = EditCore(start_ts=0.0, end_ts=3.0)
    resolution = LocalRangeResolution(
        edit_core=core,
        generation_context=GenerationContext(
            start_ts=0.0,
            end_ts=3.0,
            edit_core=core,
        ),
        occurrence_start=0.0,
        occurrence_end=3.0,
        analysis_start=0.0,
        analysis_end=3.0,
        confidence=1.0,
    )
    window = resolve_generation_window(
        0.0,
        3.0,
        payload={
            "adaptive_context_enabled": True,
            "planned_context": resolution.model_dump(mode="json"),
        },
        project_duration=3.0,
    )

    assert protected_context_prompt("Make the person jump.", window) == "Make the person jump."


def test_persistent_context_forbids_early_reversion():
    window = resolve_generation_window(
        0.5,
        4.0,
        payload={
            "adaptive_context_enabled": True,
            "planned_context": _planned_context(),
        },
        project_duration=10.0,
    )

    rendered = protected_context_prompt(
        "Make the car green.",
        window,
        temporal_behavior="persistent_state",
        effect_extent="surface",
    )

    assert "do not revert early" in rendered
    assert "requested surface" in rendered


def test_motion_context_does_not_force_subject_back_to_old_path():
    window = resolve_generation_window(
        0.5,
        4.0,
        payload={
            "adaptive_context_enabled": True,
            "planned_context": _planned_context(),
        },
        project_duration=10.0,
    )

    rendered = protected_context_prompt(
        "Make the person run.",
        window,
        temporal_behavior="future_changing_motion",
        effect_extent="motion_path",
    )

    assert "For the opening 0.500s" in rendered
    assert "without beginning the requested change" in rendered
    assert "After that protected handle" in rendered
    assert "transition gradually into the requested new motion" in rendered
    assert "continue its new path" in rendered
    assert "outgoing source motion" not in rendered


def test_bounded_motion_includes_observable_success_before_continuity():
    window = resolve_generation_window(
        0.5,
        4.0,
        payload={
            "adaptive_context_enabled": True,
            "planned_context": _planned_context(),
        },
        project_duration=10.0,
    )

    rendered = protected_context_prompt(
        "Make the person perform one requested action.",
        window,
        temporal_behavior="temporary",
        effect_extent="motion_path",
        observable_success="the target reaches a visibly different airborne position",
        action_phases=[
            "bend both knees to prepare",
            "launch upward with both feet clear of the ground",
            "land on both feet",
        ],
    )

    assert rendered.index("REQUIRED ACTION SEQUENCE:") < rendered.index("REQUEST:")
    assert "1) bend both knees to prepare" in rendered
    assert "2) launch upward with both feet clear of the ground" in rendered
    assert "3) land on both feet" in rendered
    assert rendered.index("VISIBLE PROOF — MUST APPEAR:") < rendered.index("CONTINUITY:")
    assert "visibly different airborne position" in rendered
    assert "For the opening 0.500s" in rendered
    assert "without beginning phase 1" in rendered
    assert "recover by 4.000s" in rendered
    assert "final 2.000s" in rendered


def test_new_object_context_allows_emergence_beyond_selection():
    window = resolve_generation_window(
        0.5,
        4.0,
        payload={
            "adaptive_context_enabled": True,
            "planned_context": _planned_context(),
        },
        project_duration=10.0,
    )

    rendered = protected_context_prompt(
        "Make a balloon come out of the window.",
        window,
        effect_extent="new_object_path",
    )

    assert "emerge from the selected anchor" in rendered
