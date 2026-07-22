"""Bounded retry/fallback policy for adaptive local generation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.continuity_validator import ContinuityReport


MAX_GENERATION_ATTEMPTS = 2
MAX_GENERATED_SECONDS = 30.0
CONTINUITY_UNAVAILABLE = "continuity validation unavailable"
FRAME_MATCHING_UNAVAILABLE = "frame matching unavailable"
TRANSITION_REVIEW_UNAVAILABLE = "final transition review unavailable"
TECHNICAL_VALIDATION_EVIDENCE = frozenset(
    {
        CONTINUITY_UNAVAILABLE,
        FRAME_MATCHING_UNAVAILABLE,
        TRANSITION_REVIEW_UNAVAILABLE,
    }
)


class GenerationQualityAction(StrEnum):
    PASS = "pass"
    REVIEW_WARNING = "review_warning"
    CORRECTIVE_RETRY = "corrective_retry"
    HARD_FAIL = "hard_fail"


@dataclass(frozen=True, slots=True)
class GenerationQualityDecision:
    action: GenerationQualityAction
    evidence: tuple[str, ...] = ()
    next_provider: str | None = None


def quality_evidence(
    score: dict | None,
    continuity: ContinuityReport | None,
    transition: dict | None = None,
    *,
    generation_error: str | None = None,
) -> tuple[str, ...]:
    evidence: list[str] = []
    if generation_error:
        evidence.append(f"provider generation error: {generation_error[:240]}")
    evidence.extend(semantic_quality_evidence(score))
    if continuity is not None and not continuity.passed:
        evidence.extend(
            f"{issue.code} at {issue.boundary or 'clip'}: "
            f"measured {issue.value:.3f}, limit {issue.threshold:.3f}"
            for issue in continuity.issues
        )
    evidence.extend(transition_quality_evidence(transition))
    return tuple(evidence)


def semantic_quality_evidence(score: dict | None) -> tuple[str, ...]:
    """Fail closed when target correctness cannot be proven."""
    if score is None:
        return ("semantic quality scoring unavailable",)
    evidence: list[str] = []
    coherence = int(score.get("visual_coherence") or 0)
    adherence = int(score.get("prompt_adherence") or 0)
    preservation_raw = score.get("preservation")
    preservation = int(preservation_raw or 0)
    if adherence < 6:
        evidence.append(f"prompt adherence {adherence}/10 is below 6/10")
    if coherence < 5:
        evidence.append(f"visual coherence {coherence}/10 is below 5/10")
    if preservation_raw is not None and preservation < 6:
        evidence.append(f"preservation {preservation}/10 is below 6/10")
    if evidence:
        evidence.extend(
            str(item)[:240]
            for item in score.get("evidence", [])[:4]
            if str(item).strip()
        )
    return tuple(evidence)


def transition_quality_evidence(review: dict | None) -> tuple[str, ...]:
    if review is None:
        return ()
    evidence: list[str] = []
    entry_applicable = review.get("entry_applicable") is not False
    exit_applicable = review.get("exit_applicable") is not False
    entry = int(review.get("entry_continuity") or 0)
    exit_score = int(review.get("exit_continuity") or 0)
    if entry_applicable and entry < 7:
        evidence.append(f"entry continuity {entry}/10 is below 7/10")
    if exit_applicable and exit_score < 7:
        evidence.append(f"exit continuity {exit_score}/10 is below 7/10")
    if evidence:
        evidence.extend(
            str(item)[:240]
            for item in review.get("evidence", [])[:4]
            if str(item).strip()
        )
    return tuple(evidence)


def attempt_quality_rank(
    score: dict | None,
    continuity: ContinuityReport | None,
    transition: dict | None = None,
) -> tuple[int, int, int, int, int, int, int]:
    """Order attempts by correctness first, then continuity and raw scores."""
    coherence = int((score or {}).get("visual_coherence") or 0)
    adherence = int((score or {}).get("prompt_adherence") or 0)
    preservation = int((score or {}).get("preservation") or coherence)
    entry = int((transition or {}).get("entry_continuity") or 0)
    exit_score = int((transition or {}).get("exit_continuity") or 0)
    semantic_pass = not semantic_quality_evidence(score)
    continuity_pass = continuity is not None and continuity.passed
    transition_pass = transition is not None and not transition_quality_evidence(transition)
    return (
        int(semantic_pass),
        int(continuity_pass),
        int(transition_pass),
        min(coherence, adherence),
        min(entry, exit_score),
        adherence,
        min(coherence, preservation),
    )


def final_semantic_quality(score: dict | None) -> GenerationQualityDecision:
    evidence = semantic_quality_evidence(score)
    return GenerationQualityDecision(
        GenerationQualityAction.REVIEW_WARNING if evidence else GenerationQualityAction.PASS,
        evidence,
    )


def final_candidate_quality(
    score: dict | None,
    continuity: ContinuityReport | None,
    transition: dict | None = None,
) -> GenerationQualityDecision:
    """Block measured bad seams; warn when technical validation was unavailable."""
    if continuity is None:
        evidence = (
            *semantic_quality_evidence(score),
            FRAME_MATCHING_UNAVAILABLE,
        )
        return GenerationQualityDecision(
            GenerationQualityAction.REVIEW_WARNING,
            evidence,
        )
    if not continuity.passed:
        evidence = quality_evidence(score, continuity, transition)
        return GenerationQualityDecision(GenerationQualityAction.HARD_FAIL, evidence)
    if transition is None:
        evidence = (
            *semantic_quality_evidence(score),
            TRANSITION_REVIEW_UNAVAILABLE,
        )
        return GenerationQualityDecision(
            GenerationQualityAction.REVIEW_WARNING,
            evidence,
        )
    if transition_quality_evidence(transition):
        evidence = quality_evidence(score, continuity, transition)
        return GenerationQualityDecision(GenerationQualityAction.HARD_FAIL, evidence)
    return final_semantic_quality(score)


def decide_generation_quality(
    *,
    score: dict | None,
    continuity: ContinuityReport | None,
    transition: dict | None = None,
    duration: float,
    attempts: int,
    generated_seconds: float,
    generation_error: str | None = None,
) -> GenerationQualityDecision:
    evidence = quality_evidence(
        score,
        continuity,
        transition,
        generation_error=generation_error,
    )
    if not evidence:
        if continuity is None or transition is None:
            return final_candidate_quality(score, continuity, transition)
        return GenerationQualityDecision(GenerationQualityAction.PASS)

    can_generate_again = (
        attempts < MAX_GENERATION_ATTEMPTS
        and generated_seconds + duration <= MAX_GENERATED_SECONDS + 0.05
    )
    if not can_generate_again:
        return final_candidate_quality(score, continuity, transition)

    # Missing semantic scoring cannot describe a useful correction. Missing
    # structural validation cannot be repaired by asking the model to guess.
    if score is None or continuity is None:
        return final_candidate_quality(score, continuity, transition)
    if transition is None and continuity.passed:
        return final_candidate_quality(score, continuity, transition)

    # A provider switch is not a seam repair. It historically spent another
    # paid render and replaced Wan with a weaker result. Retry this provider
    # once with concrete evidence, then fail closed.
    return GenerationQualityDecision(GenerationQualityAction.CORRECTIVE_RETRY, evidence)


def corrective_prompt(
    evidence: tuple[str, ...],
    *,
    pre_handle: float | None = None,
    post_handle: float | None = None,
) -> str:
    details = "\n".join(f"- {item}" for item in evidence)
    lowered = " ".join(evidence).lower()
    transition_contracts: list[str] = []
    if "entry" in lowered and pre_handle is not None:
        transition_contracts.append(
            "Briefly match incoming source motion, then transition gradually into the "
            "requested action. Never cut directly to its most changed or extreme pose. "
            "The action must still occur after this brief bridge, without a long delay."
        )
    if "exit" in lowered and post_handle is not None:
        transition_contracts.append(
            "After the action, recover into outgoing pose and velocity without pausing "
            "or resetting."
        )
    transition_text = " ".join(transition_contracts)
    return (
        "\n\nRETRY CORRECTION — fix these measured failures:\n"
        f"{details}\n"
        f"{transition_text}\n"
        "Keep the required target, action, count, and attributes exact. Fix only the "
        "named failure; preserve unrelated content. Do not return an unchanged target, "
        "cut, fade, freeze, or spill outside the edit."
    )


def acceptance_block_reason(payload: dict | None) -> str | None:
    data = payload or {}
    if data.get("generation_quality_state") != GenerationQualityAction.HARD_FAIL:
        return None
    raw_evidence = data.get("generation_quality_evidence")
    evidence = (
        [
            str(item)
            for item in raw_evidence
            if str(item).strip().lower() not in TECHNICAL_VALIDATION_EVIDENCE
        ]
        if isinstance(raw_evidence, list)
        else []
    )
    # Older jobs incorrectly treated a validator outage as a bad render. Keep
    # those successful renders applicable without weakening real quality checks.
    if isinstance(raw_evidence, list) and not evidence:
        return None
    if evidence:
        return "generation quality hard-failed: " + "; ".join(evidence[:3])
    return "generation quality hard-failed"


def normalized_quality_state(
    state: str | None,
    evidence: list | tuple | None,
) -> str | None:
    """Repair persisted jobs where validator outages were labeled unsafe."""
    raw = [str(item).strip().lower() for item in (evidence or []) if str(item).strip()]
    if (
        state == GenerationQualityAction.HARD_FAIL
        and raw
        and all(item in TECHNICAL_VALIDATION_EVIDENCE for item in raw)
    ):
        return GenerationQualityAction.REVIEW_WARNING.value
    return state


def quality_payload_for_candidate(
    payload: dict | None,
    variant_id: str,
) -> dict:
    """Overlay one candidate's review onto the legacy job-level fields."""
    output = dict(payload or {})
    reviews = output.get("candidate_reviews")
    review = reviews.get(variant_id) if isinstance(reviews, dict) else None
    if isinstance(review, dict):
        output["generation_quality_state"] = review.get("quality_state")
        output["generation_quality_evidence"] = review.get("evidence") or []
        output["continuity_validation"] = review.get("continuity_validation")
        output["selected_seams"] = review.get("selected_seams")
        output["preservation_score"] = review.get("preservation_score")
        output["transition_review"] = review.get("transition_review")
    output["generation_quality_state"] = normalized_quality_state(
        output.get("generation_quality_state"),
        output.get("generation_quality_evidence"),
    )
    return output


def cancel_waiting_retry(payload: dict | None, *, reason: str) -> dict:
    output = dict(payload or {})
    retry_state = dict(output.get("retry_state") or {})
    if retry_state.get("status") == "waiting":
        retry_state["status"] = "cancelled"
        retry_state["cancel_reason"] = reason
        output["retry_state"] = retry_state
    return output


def acceptance_allowed(
    payload: dict | None,
    *,
    override_requested: bool,
    override_enabled: bool,
) -> bool:
    return acceptance_block_reason(payload) is None or (
        override_requested and override_enabled
    )
