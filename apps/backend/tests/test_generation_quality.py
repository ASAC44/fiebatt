from app.services.continuity_validator import ContinuityIssue, ContinuityReport
from app.services.generation_quality import (
    GenerationQualityAction,
    acceptance_allowed,
    corrective_prompt,
    decide_generation_quality,
    select_fallback_provider,
)


def _failed_continuity() -> ContinuityReport:
    return ContinuityReport(
        passed=False,
        metrics={"exit_subject_motion_jump": 0.9},
        issues=[ContinuityIssue("exit_subject_motion_jump", 0.9, 0.78, "exit")],
    )


def test_clean_result_passes_without_extra_generation():
    decision = decide_generation_quality(
        score={"visual_coherence": 8, "prompt_adherence": 8},
        continuity=ContinuityReport(True, {}),
        current_provider="wan",
        duration=6.0,
        attempts=1,
        generated_seconds=6.0,
        fallback_used=False,
        source_video_available=True,
    )
    assert decision.action == GenerationQualityAction.PASS


def test_first_source_edit_failure_gets_evidence_driven_retry():
    decision = decide_generation_quality(
        score={"visual_coherence": 8, "prompt_adherence": 8},
        continuity=_failed_continuity(),
        current_provider="wan",
        duration=6.0,
        attempts=1,
        generated_seconds=6.0,
        fallback_used=False,
        source_video_available=True,
    )
    assert decision.action == GenerationQualityAction.CORRECTIVE_RETRY
    assert "exit_subject_motion_jump" in corrective_prompt(decision.evidence)


def test_image_provider_failure_routes_to_source_video_fallback():
    decision = decide_generation_quality(
        score={"visual_coherence": 8, "prompt_adherence": 8},
        continuity=_failed_continuity(),
        current_provider="veo",
        duration=8.0,
        attempts=1,
        generated_seconds=8.0,
        fallback_used=False,
        source_video_available=True,
    )
    assert decision.action == GenerationQualityAction.PROVIDER_FALLBACK
    assert decision.next_provider == "wan"


def test_retry_then_fallback_is_capped_by_generated_seconds():
    decision = decide_generation_quality(
        score={"visual_coherence": 4, "prompt_adherence": 8},
        continuity=_failed_continuity(),
        current_provider="wan",
        duration=12.0,
        attempts=2,
        generated_seconds=24.0,
        fallback_used=False,
        source_video_available=True,
    )
    assert decision.action == GenerationQualityAction.HARD_FAIL


def test_provider_fallback_respects_full_context_limit():
    assert select_fallback_provider("veo", 8.0) == "wan"
    assert select_fallback_provider("wan", 12.0) == "happyhorse"
    assert select_fallback_provider("happyhorse", 12.0) is None


def test_hard_fail_acceptance_requires_both_request_and_operator_flag():
    payload = {"generation_quality_state": "hard_fail"}
    assert not acceptance_allowed(payload, override_requested=False, override_enabled=True)
    assert not acceptance_allowed(payload, override_requested=True, override_enabled=False)
    assert acceptance_allowed(payload, override_requested=True, override_enabled=True)
    assert acceptance_allowed({}, override_requested=False, override_enabled=False)
