from app.services.continuity_validator import ContinuityIssue, ContinuityReport
from app.services.generation_quality import (
    GenerationQualityAction,
    acceptance_block_reason,
    acceptance_allowed,
    attempt_quality_rank,
    corrective_prompt,
    decide_generation_quality,
    final_semantic_quality,
    final_candidate_quality,
    normalized_quality_state,
    quality_payload_for_candidate,
    cancel_waiting_retry,
    semantic_quality_evidence,
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
        duration=6.0,
        attempts=1,
        generated_seconds=6.0,
    )
    assert decision.action == GenerationQualityAction.PASS


def test_unavailable_continuity_is_warning_not_generation_failure():
    decision = decide_generation_quality(
        score={"visual_coherence": 8, "prompt_adherence": 8},
        continuity=None,
        duration=6.0,
        attempts=1,
        generated_seconds=6.0,
    )
    assert decision.action == GenerationQualityAction.PASS


def test_old_validator_only_hard_fail_remains_applicable():
    payload = {
        "generation_quality_state": "hard_fail",
        "generation_quality_evidence": ["continuity validation unavailable"],
    }
    assert acceptance_block_reason(payload) is None
    assert acceptance_allowed(payload, override_requested=False, override_enabled=False)


def test_frame_matcher_outage_is_warning_and_remains_applicable():
    payload = {
        "generation_quality_state": "hard_fail",
        "generation_quality_evidence": ["frame matching unavailable"],
    }
    assert normalized_quality_state(
        payload["generation_quality_state"],
        payload["generation_quality_evidence"],
    ) == "review_warning"
    assert acceptance_block_reason(payload) is None
    assert acceptance_allowed(payload, override_requested=False, override_enabled=False)


def test_real_continuity_failure_still_blocks_acceptance():
    payload = {
        "generation_quality_state": "hard_fail",
        "generation_quality_evidence": [
            "exit_subject_motion_jump at exit: measured 0.900, limit 0.780"
        ],
    }
    assert "exit_subject_motion_jump" in (acceptance_block_reason(payload) or "")
    assert not acceptance_allowed(payload, override_requested=False, override_enabled=False)


def test_first_source_edit_failure_gets_evidence_driven_retry():
    decision = decide_generation_quality(
        score={"visual_coherence": 8, "prompt_adherence": 8},
        continuity=_failed_continuity(),
        duration=6.0,
        attempts=1,
        generated_seconds=6.0,
    )
    assert decision.action == GenerationQualityAction.CORRECTIVE_RETRY
    assert "exit_subject_motion_jump" in corrective_prompt(decision.evidence)


def test_failed_source_edit_never_switches_provider_automatically():
    decision = decide_generation_quality(
        score={"visual_coherence": 8, "prompt_adherence": 8},
        continuity=_failed_continuity(),
        duration=8.0,
        attempts=1,
        generated_seconds=8.0,
    )
    assert decision.action == GenerationQualityAction.CORRECTIVE_RETRY
    assert decision.next_provider is None


def test_corrective_retry_is_capped_by_generated_seconds():
    decision = decide_generation_quality(
        score={"visual_coherence": 4, "prompt_adherence": 8},
        continuity=_failed_continuity(),
        duration=12.0,
        attempts=2,
        generated_seconds=24.0,
    )
    assert decision.action == GenerationQualityAction.HARD_FAIL


def test_hard_fail_acceptance_requires_both_request_and_operator_flag():
    payload = {"generation_quality_state": "hard_fail"}
    assert not acceptance_allowed(payload, override_requested=False, override_enabled=True)
    assert not acceptance_allowed(payload, override_requested=True, override_enabled=False)
    assert acceptance_allowed(payload, override_requested=True, override_enabled=True)
    assert acceptance_allowed({}, override_requested=False, override_enabled=False)


def test_production_bad_outputs_cannot_be_marked_clean():
    assert semantic_quality_evidence(
        {"visual_coherence": 8, "prompt_adherence": 1}
    ) == ("prompt adherence 1/10 is below 6/10",)
    assert semantic_quality_evidence({
        "visual_coherence": 8,
        "prompt_adherence": 1,
        "evidence": ["requested green paint is absent; car remains white"],
    }) == (
        "prompt adherence 1/10 is below 6/10",
        "requested green paint is absent; car remains white",
    )
    assert semantic_quality_evidence(
        {"visual_coherence": 4, "prompt_adherence": 2}
    ) == (
        "visual coherence 4/10 is below 5/10",
        "prompt adherence 2/10 is below 6/10",
    )
    assert semantic_quality_evidence(None) == ("semantic quality scoring unavailable",)
    assert final_semantic_quality(
        {"visual_coherence": 8, "prompt_adherence": 1}
    ).action == GenerationQualityAction.REVIEW_WARNING
    assert final_semantic_quality(
        {"visual_coherence": 4, "prompt_adherence": 2}
    ).action == GenerationQualityAction.REVIEW_WARNING
    assert final_semantic_quality(
        {"visual_coherence": 8, "prompt_adherence": 8}
    ).action == GenerationQualityAction.PASS


def test_semantic_miss_warns_measured_unsafe_seam_blocks_apply():
    clean = ContinuityReport(True, {})
    assert final_candidate_quality(
        {"visual_coherence": 8, "prompt_adherence": 2}, clean
    ).action == GenerationQualityAction.REVIEW_WARNING
    assert final_candidate_quality(
        {"visual_coherence": 8, "prompt_adherence": 8}, _failed_continuity()
    ).action == GenerationQualityAction.HARD_FAIL
    assert final_candidate_quality(
        {"visual_coherence": 8, "prompt_adherence": 8}, None
    ).action == GenerationQualityAction.REVIEW_WARNING


def test_candidate_review_overrides_job_level_acceptance_state():
    payload = {
        "generation_quality_state": "pass",
        "candidate_reviews": {
            "variant-unsafe": {
                "quality_state": "hard_fail",
                "evidence": ["exit_frame_match_score at exit"],
                "selected_seams": {"passed": False},
            }
        },
    }

    candidate = quality_payload_for_candidate(payload, "variant-unsafe")

    assert candidate["generation_quality_state"] == "hard_fail"
    assert not acceptance_allowed(
        candidate,
        override_requested=False,
        override_enabled=False,
    )


def test_legacy_candidate_validator_outage_is_normalized_for_ui_and_apply():
    payload = {
        "generation_quality_state": "pass",
        "candidate_reviews": {
            "variant-unchecked": {
                "quality_state": "hard_fail",
                "evidence": ["frame matching unavailable"],
                "selected_seams": None,
            }
        },
    }

    candidate = quality_payload_for_candidate(payload, "variant-unchecked")

    assert candidate["generation_quality_state"] == "review_warning"
    assert acceptance_allowed(
        candidate,
        override_requested=False,
        override_enabled=False,
    )


def test_applying_first_pass_cancels_a_waiting_retry():
    payload = cancel_waiting_retry(
        {"retry_state": {"status": "waiting", "evidence": ["wrong colour"]}},
        reason="candidate applied",
    )

    assert payload["retry_state"]["status"] == "cancelled"
    assert payload["retry_state"]["cancel_reason"] == "candidate applied"


def test_retry_replaces_previous_result_only_when_quality_improves():
    continuity = ContinuityReport(True, {})
    car_failure = {"visual_coherence": 8, "prompt_adherence": 1}
    worse_retry = {"visual_coherence": 4, "prompt_adherence": 2}
    good_retry = {"visual_coherence": 7, "prompt_adherence": 8}

    assert attempt_quality_rank(worse_retry, continuity) > attempt_quality_rank(
        car_failure,
        continuity,
    )
    assert attempt_quality_rank(good_retry, continuity) > attempt_quality_rank(
        worse_retry,
        continuity,
    )
    assert not semantic_quality_evidence(good_retry)

    correction = corrective_prompt(semantic_quality_evidence(car_failure))
    assert "prompt adherence 1/10" in correction
    assert "named color" in correction
    assert "bleed outside the target" in correction
