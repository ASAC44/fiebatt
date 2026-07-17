"""Bounded retry/fallback policy for adaptive local generation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.ai.services.provider_capabilities import (
    VIDEO_PROVIDER_CAPABILITIES,
    validate_provider_duration,
)
from app.services.continuity_validator import ContinuityReport


MAX_GENERATION_ATTEMPTS = 3
MAX_GENERATED_SECONDS = 30.0


class GenerationQualityAction(StrEnum):
    PASS = "pass"
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
    if continuity is None:
        evidence.append("continuity validation unavailable")
    elif not continuity.passed:
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
        GenerationQualityAction.HARD_FAIL if evidence else GenerationQualityAction.PASS,
        evidence,
    )


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
        return GenerationQualityDecision(GenerationQualityAction.HARD_FAIL, evidence)

    current_capabilities = VIDEO_PROVIDER_CAPABILITIES.get(current_provider)
    fallback = (
        select_fallback_provider(current_provider, duration)
        if source_video_available
        else None
    )
    provider_limited = (
        generation_error is not None
        or current_capabilities is None
        or not current_capabilities.source_video_edit
    )
    if provider_limited and not fallback_used and fallback:
        return GenerationQualityDecision(
            GenerationQualityAction.PROVIDER_FALLBACK,
            evidence,
            fallback,
        )
    if attempts == 1:
        return GenerationQualityDecision(
            GenerationQualityAction.CORRECTIVE_RETRY,
            evidence,
        )
    if not fallback_used and fallback:
        return GenerationQualityDecision(
            GenerationQualityAction.PROVIDER_FALLBACK,
            evidence,
            fallback,
        )
    return GenerationQualityDecision(GenerationQualityAction.HARD_FAIL, evidence)


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
    evidence = data.get("generation_quality_evidence")
    if isinstance(evidence, list) and evidence:
        return "continuity validation hard-failed: " + "; ".join(str(item) for item in evidence[:3])
    return "continuity validation hard-failed"


def acceptance_allowed(
    payload: dict | None,
    *,
    override_requested: bool,
    override_enabled: bool,
) -> bool:
    return acceptance_block_reason(payload) is None or (
        override_requested and override_enabled
    )
