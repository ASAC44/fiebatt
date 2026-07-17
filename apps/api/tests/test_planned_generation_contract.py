import pytest
from pydantic import ValidationError

from app.api.routes.agent import SYSTEM_PROMPT, TOOL_DECLARATIONS
from app.config.settings import Settings
from app.schemas.generate import GenerateRequest
from app.schemas.job import JobOut
from app.services.agent_tools import TOOL_HANDLERS


def test_generate_request_accepts_authoritative_plan_id():
    request = GenerateRequest(
        project_id="project-1",
        plan_id="plan-1",
        target_clip_id="clip-1",
    )
    assert request.plan_id == "plan-1"
    assert request.target_clip_id == "clip-1"


def test_generate_request_keeps_complete_legacy_shape():
    request = GenerateRequest(
        project_id="project-1",
        start_ts=1.0,
        end_ts=4.0,
        bbox={"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.7},
        prompt="make this person jump",
        reference_frame_ts=2.5,
    )
    assert request.plan_id is None


def test_generate_request_rejects_partial_legacy_shape():
    with pytest.raises(ValidationError, match="plan_id or complete legacy"):
        GenerateRequest(project_id="project-1", start_ts=1.0, end_ts=4.0)


def test_adaptive_generation_is_rollout_gated_by_default():
    assert Settings().adaptive_edit_planning is False
    assert Settings().global_edit_planning is False


def test_generation_job_exposes_continuity_and_core_preview_metadata():
    response = JobOut(
        job_id="job-1",
        kind="generate",
        status="done",
        execution_window={
            "adaptive": True,
            "core_start": 4.0,
            "core_end": 6.0,
            "context_start": 3.0,
            "context_end": 7.0,
            "edit_start_offset": 1.0,
            "edit_end_offset": 3.0,
            "pre_handle": 1.0,
            "post_handle": 1.0,
        },
        continuity_validation={"passed": True, "issues": [], "sampled_frames": 12},
        generation_quality_state="pass",
        generation_attempts=2,
        generated_seconds=8.0,
        provider_attempts=["wan", "veo"],
        model="wan2.7-videoedit",
        edit_mode="source_video",
    )

    assert response.execution_window["edit_start_offset"] == 1.0
    assert response.continuity_validation["passed"] is True
    assert response.provider_attempts == ["wan", "veo"]
    assert response.model == "wan2.7-videoedit"
    assert response.edit_mode == "source_video"


def test_agent_defaults_to_local_plan_not_full_timeline():
    assert "call create_edit_plan once" in SYSTEM_PROMPT
    assert "Never default to the full timeline" in SYSTEM_PROMPT
    assert "Default start_ts to 0.0" not in SYSTEM_PROMPT


def test_agent_exposes_only_explicit_occurrence_discovery():
    declaration_names = {declaration.name for declaration in TOOL_DECLARATIONS}
    assert "discover_occurrences" in declaration_names
    assert "discover_occurrences" in TOOL_HANDLERS
    assert "Never start this full-reel scan for a local-only request" in SYSTEM_PROMPT
