import pytest

from app.models.job import Job
from app.schemas.edit_plan import EditCore, GenerationContext, LocalRangeResolution
from app.services.accepted_generation import accepted_generation_range
from app.services.continuity_validator import ContinuityReport
from app.services.generation_quality import GenerationQualityAction, decide_generation_quality
from app.services.generation_telemetry import build_local_flow_telemetry
from app.services.generation_window import resolve_generation_window


def test_planned_local_flow_preserves_handles_and_commits_only_core():
    core = EditCore(start_ts=8.25, end_ts=11.75)
    resolution = LocalRangeResolution(
        edit_core=core,
        generation_context=GenerationContext(
            start_ts=7.5,
            end_ts=12.5,
            edit_core=core,
        ),
        occurrence_start=5.0,
        occurrence_end=17.0,
        analysis_start=6.5,
        analysis_end=13.5,
        frames_inspected=29,
        confidence=0.91,
    )
    payload = {
        "adaptive_context_enabled": True,
        "planned_context": resolution.model_dump(mode="json"),
        "plan_scope": "local",
        "analysis_duration_ms": 38.0,
        "analysis_frames": 29,
        "fixed_window_baseline_seconds": 3.0,
        "committed_timeline_range": {"start": 8.25, "end": 11.75},
    }
    window = resolve_generation_window(
        core.start_ts,
        core.end_ts,
        payload=payload,
        project_duration=20.0,
    )
    continuity = ContinuityReport(
        passed=True,
        metrics={
            "pre_handle_pixel_delta": 0.02,
            "post_handle_pixel_delta": 0.03,
            "entry_subject_motion_jump": 0.11,
            "exit_subject_motion_jump": 0.14,
        },
        sampled_frames=20,
    )
    quality = decide_generation_quality(
        score={"visual_coherence": 9, "prompt_adherence": 9},
        continuity=continuity,
        current_provider="wan",
        duration=window.context_duration,
        attempts=1,
        generated_seconds=window.context_duration,
        fallback_used=False,
        source_video_available=True,
    )
    job = Job(
        start_ts=core.start_ts,
        end_ts=core.end_ts,
        payload={**payload, "execution_window": window.metadata()},
    )
    accepted = accepted_generation_range(job)
    telemetry = build_local_flow_telemetry(
        payload=payload,
        window=window,
        continuity=continuity,
        quality_state=quality.action.value,
        attempts=1,
        generated_seconds=window.context_duration,
        provider_attempts=["wan"],
        selected_provider="wan",
    )

    assert window.adaptive is True
    assert (window.pre_handle, window.post_handle) == pytest.approx((0.75, 0.75))
    assert quality.action == GenerationQualityAction.PASS
    assert (accepted.committed_start, accepted.committed_end) == pytest.approx(
        (8.25, 11.75)
    )
    assert (accepted.media_start, accepted.media_end) == pytest.approx((0.75, 4.25))
    assert telemetry["scope"] == "local"
    assert telemetry["analysis_frames"] == 29
    assert telemetry["continuity_passed"] is True

    legacy = resolve_generation_window(
        8.5,
        11.5,
        payload={"adaptive_context_enabled": False},
        project_duration=20.0,
    )
    assert legacy.adaptive is False
    assert legacy.context_duration == legacy.core_duration == 3.0
