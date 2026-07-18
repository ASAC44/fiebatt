"""Bounded retry/fallback policy for adaptive local generation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.ai.services.provider_capabilities import (
    VIDEO_PROVIDER_CAPABILITIES,
    validate_provider_duration,
)
from app.services.continuity_validator import ContinuityReport


MAX_GENERATION_ATTEMPTS = 2
MAX_GENERATED_SECONDS = 30.0
CONTINUITY_UNAVAILABLE = "continuity validation unavailable"
FRAME_MATCHING_UNAVAILABLE = "frame matching unavailable"
TECHNICAL_VALIDATION_EVIDENCE = frozenset(
    {CONTINUITY_UNAVAILABLE, FRAME_MATCHING_UNAVAILABLE}
)


class GenerationQualityAction(StrEnum):
    PASS = "pass"
    REVIEW_WARNING = "review_warning"
    CORRECTIVE_RETRY = "corrective_retry"
    PROVIDER_FALLBACK = "provider_fallback"
    HARD_FAIL = "hard_fail"


@dataclass(frozen=True, slots=True)
class GenerationQualityDecision:
    action: GenerationQualityAction
    evidence: tuple[str, ...] = ()
    next_provider: str | None = None


def quality_evidence(
    score: dict | None,
    continuity: ContinuityReport | None,
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
    return tuple(evidence)


def semantic_quality_evidence(score: dict | None) -> tuple[str, ...]:
    """Fail closed when target correctness cannot be proven."""
    if score is None:
        return ("semantic quality scoring unavailable",)
    evidence: list[str] = []
    coherence = int(score.get("visual_coherence") or 0)
    adherence = int(score.get("prompt_adherence") or 0)
    if coherence < 5:
        evidence.append(f"visual coherence {coherence}/10 is below 5/10")
    if adherence < 6:
        evidence.append(f"prompt adherence {adherence}/10 is below 6/10")
    if evidence:
        evidence.extend(
            str(item)[:240]
            for item in score.get("evidence", [])[:4]
            if str(item).strip()
        )
    return tuple(evidence)


def attempt_quality_rank(
    score: dict | None,
    continuity: ContinuityReport | None,
) -> tuple[int, int, int, int, int]:
    """Order attempts by correctness first, then continuity and raw scores."""
    coherence = int((score or {}).get("visual_coherence") or 0)
    adherence = int((score or {}).get("prompt_adherence") or 0)
    semantic_pass = not semantic_quality_evidence(score)
    continuity_pass = continuity is not None and continuity.passed
    return (
        int(semantic_pass),
        int(continuity_pass),
        min(coherence, adherence),
        adherence,
        coherence,
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
) -> GenerationQualityDecision:
    """Block measured bad seams; warn when technical validation was unavailable."""
    if continuity is None:
        evidence = (*semantic_quality_evidence(score), FRAME_MATCHING_UNAVAILABLE)
        return GenerationQualityDecision(
            GenerationQualityAction.REVIEW_WARNING,
            evidence,
        )
    if not continuity.passed:
        evidence = quality_evidence(score, continuity)
        return GenerationQualityDecision(GenerationQualityAction.HARD_FAIL, evidence)
    return final_semantic_quality(score)


def select_fallback_provider(current_provider: str, duration: float) -> str | None:
    """Prefer a different source-video editor that fits the full context."""
    order = ("wan", "happyhorse") if current_provider != "wan" else ("happyhorse",)
    for provider in order:
        if provider == current_provider:
            continue
        capabilities = VIDEO_PROVIDER_CAPABILITIES[provider]
        if capabilities.source_video_edit and validate_provider_duration(provider, duration) is None:
            return provider
    return None


def decide_generation_quality(
    *,
    score: dict | None,
    continuity: ContinuityReport | None,
    current_provider: str,
    duration: float,
    attempts: int,
    generated_seconds: float,
    fallback_used: bool,
    source_video_available: bool,
    generation_error: str | None = None,
) -> GenerationQualityDecision:
    evidence = quality_evidence(
        score,
        continuity,
        generation_error=generation_error,
    )
    if not evidence:
        return GenerationQualityDecision(GenerationQualityAction.PASS)

    can_generate_again = (
        attempts < MAX_GENERATION_ATTEMPTS
        and generated_seconds + duration <= MAX_GENERATED_SECONDS + 0.05
    )
    if not can_generate_again:
        return final_candidate_quality(score, continuity)

    # Missing semantic scoring cannot describe a useful correction. Missing
    # structural validation cannot be repaired by asking the model to guess.
    if score is None or continuity is None:
        return final_candidate_quality(score, continuity)

    # A provider switch is not a seam repair. It historically spent another
    # paid render and replaced Wan with a weaker result. Retry this provider
    # once with concrete evidence, then fail closed.
    return GenerationQualityDecision(GenerationQualityAction.CORRECTIVE_RETRY, evidence)


def corrective_prompt(evidence: tuple[str, ...]) -> str:
    details = "\n".join(f"- {item}" for item in evidence)
    return (
        "\n\nQUALITY CORRECTION REQUIRED. The previous result failed these exact checks:\n"
        f"{details}\n"
        "Satisfy the non-negotiable user requirement exactly, including every named color, "
        "shape, anatomical feature, count, and selected boundary. Correct only the requested "
        "target. Preserve protected handles, background, camera motion, lighting, and all "
        "unselected pixels. Do not freeze, fade, cut, bleed outside the target, or regenerate "
        "the scene."
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
