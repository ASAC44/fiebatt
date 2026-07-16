import pytest
from pydantic import ValidationError

from app.api.routes.agent import SYSTEM_PROMPT
from app.config.settings import Settings
from app.schemas.generate import GenerateRequest


def test_generate_request_accepts_authoritative_plan_id():
    request = GenerateRequest(project_id="project-1", plan_id="plan-1")
    assert request.plan_id == "plan-1"


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


def test_agent_defaults_to_local_plan_not_full_timeline():
    assert "call create_edit_plan once" in SYSTEM_PROMPT
    assert "Never default to the full timeline" in SYSTEM_PROMPT
    assert "Default start_ts to 0.0" not in SYSTEM_PROMPT
