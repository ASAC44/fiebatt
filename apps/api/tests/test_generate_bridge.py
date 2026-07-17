import os

os.environ["USE_AI_STUBS"] = "true"

from app.ai import services as ai  # noqa: E402
from app.ai.services.provider_capabilities import (  # noqa: E402
    select_source_edit_mode,
    select_video_provider,
    validate_provider_duration,
)
from app.workers import generate_job  # noqa: E402
from app.workers.generate_job import (  # noqa: E402
    _planned_edit_prompt,
    _provider_model,
    _public_url_or_none,
)


def test_public_url_gate_accepts_remote_https():
    assert _public_url_or_none("https://cdn.example.test/clip.mp4") == "https://cdn.example.test/clip.mp4"


def test_public_url_gate_rejects_local_urls():
    assert _public_url_or_none("/media/clips/clip.mp4") is None
    assert _public_url_or_none("http://localhost:8000/media/clips/clip.mp4") is None
    assert _public_url_or_none("http://127.0.0.1:8000/media/clips/clip.mp4") is None


def test_jump_then_walk_prompt_routes_to_single_source_edit():
    prompt = (
        "The man jumps up and down a few times, then lands and smoothly "
        "continues into a normal walk, without stopping."
    )
    motion, sequenced, rewritten = ai._rewrite_motion_prompt(prompt)  # type: ignore[attr-defined]

    assert motion is True
    assert sequenced is True
    assert prompt in rewritten
    assert "exactly three distinct repetitions" in rewritten
    assert select_video_provider("auto", source_video=True) == "wan"
    assert select_video_provider("auto", source_video=True, duration=10.0) == "wan"
    assert select_video_provider("auto", source_video=True, duration=12.0) == "happyhorse"
    assert not hasattr(generate_job, "_run_happyhorse_motion_bridge")


def test_explicit_happyhorse_remains_available():
    assert select_video_provider("happyhorse", source_video=True) == "happyhorse"


def test_veo_duration_validation_is_strict():
    assert validate_provider_duration("veo", 4.0) is None
    assert validate_provider_duration("veo", 5.0) is not None


def test_wan_duration_validation_matches_video_edit_api():
    assert validate_provider_duration("wan", 10.0) is None
    assert validate_provider_duration("wan", 12.0) is not None


def test_wan_prefers_current_video_edit_model_for_local_edits():
    assert select_source_edit_mode(
        "wan", duration=4.9, source_video=True, mask_available=True
    ) == "source_video"
    assert select_source_edit_mode(
        "wan", duration=6.0, source_video=True, mask_available=True
    ) == "source_video"
    assert _provider_model("wan", "source_video") == "wan2.7-videoedit"
    assert _provider_model("wan", "tracked_mask") == "wan2.1-vace-plus"


def test_source_edit_mode_falls_back_by_provider_capability():
    assert select_source_edit_mode(
        "happyhorse", duration=12.0, source_video=True, mask_available=True
    ) == "source_video"
    assert select_source_edit_mode(
        "veo", duration=8.0, source_video=True, mask_available=True
    ) == "image_conditioned"


def test_generation_uses_grounded_plan_without_losing_user_requirement():
    prompt = _planned_edit_prompt(
        "Make the car green",
        {
            "prompt_for_video_edit": (
                "Change only the painted body panels to vivid green. Preserve "
                "windows, tires, reflections, road, and surrounding vehicles."
            )
        },
    )

    assert "Make the car green" in prompt
    assert "painted body panels to vivid green" in prompt
    assert "Preserve windows, tires" in prompt
