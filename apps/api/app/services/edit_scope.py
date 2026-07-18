from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.edit_plan import ChangeType, EditIntent, EditScope


_GLOBAL_RE = re.compile(
    r"\b(everywhere|throughout|every time|each time|all occurrences?|whole (video|reel))\b",
    re.IGNORECASE,
)
_PERSISTENT_RE = re.compile(
    r"\b(while (they|he|she|it|this|the .+) (is |are )?visible|"
    r"while visible|for (the )?(whole|entire) (shot|appearance)|"
    r"as long as .+ visible)\b",
    re.IGNORECASE,
)
_MOTION_RE = re.compile(
    r"\b(jump|bounce|run|walk|dance|wave|turn|spin|sit|stand|kick|throw|catch|"
    r"clap|nod|bow|fall|leap)(s|ed|ing)?\b",
    re.IGNORECASE,
)
_TRAJECTORY_RE = re.compile(
    r"\b(run|walk|fly|drive|move|leave|approach|follow|chase|crawl|swim)"
    r"(s|ed|ing)?\b",
    re.IGNORECASE,
)
_CREATED_EVENT_RE = re.compile(
    r"\b(appear|appears|appeared|appearing|emerge|emerges|emerged|emerging|"
    r"come out|comes out|coming out|came out|pop out|pops out|popping out|"
    r"reveal|reveals|revealed|revealing|release|releases|released|releasing|"
    r"spawn|spawns|spawned|spawning|materialize|materializes|materialized|"
    r"materializing)\b",
    re.IGNORECASE,
)
_EXPLICIT_CONTINUATION_RE = re.compile(
    r"\b(keep|keeps|continue|continues|remain|remains|stay|stays|"
    r"rest of (the )?(video|clip|shot)|from then on|throughout|forever)\b",
    re.IGNORECASE,
)
_REMOVAL_RE = re.compile(r"\b(remove|erase|delete|make .+ disappear)\b", re.IGNORECASE)
_REPLACEMENT_RE = re.compile(r"\b(replace|swap|turn .+ into)\b", re.IGNORECASE)
_APPEARANCE_RE = re.compile(
    r"\b(color|shirt|clothes?|hair|style|look|wear|red|blue|green|black|white)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PlanningEstimate:
    analysis_mode: str
    expected_tracking_seconds: float | None
    expected_generation_calls: int
    expected_generated_seconds: float
    requires_global_discovery: bool


@dataclass(frozen=True, slots=True)
class ScopeGateResult:
    intent: EditIntent
    estimate: PlanningEstimate
    reason: str


def _change_type(prompt: str) -> ChangeType:
    if _REMOVAL_RE.search(prompt):
        return "removal"
    if _REPLACEMENT_RE.search(prompt):
        return "replacement"
    if _MOTION_RE.search(prompt):
        return "motion"
    if _APPEARANCE_RE.search(prompt):
        return "appearance"
    return "scene"


def _motion_contract(prompt: str) -> tuple[list[str], float, bool]:
    lowered = prompt.lower()
    if _bounded_created_event(prompt):
        return (
            [
                "prepare source motion",
                "introduce requested object",
                "complete requested event",
                "recover source continuity",
            ],
            4.0,
            True,
        )
    if not _MOTION_RE.search(prompt):
        return [], 2.0, False
    phases = ["prepare", "perform action"]
    recovery = bool(re.search(r"\b(land|resume|continue|recover|then)\b", lowered))
    if "jump" in lowered or "leap" in lowered:
        phases.extend(["land", "stabilize"])
        recovery = True
        return phases, 3.5, recovery
    if recovery:
        phases.append("recover original motion")
    return phases, 3.0, recovery


def _bounded_created_event(prompt: str) -> bool:
    """Return whether a created/revealed effect should use a local story beat."""
    return bool(
        _CREATED_EVENT_RE.search(prompt)
        and not _EXPLICIT_CONTINUATION_RE.search(prompt)
        and not _GLOBAL_RE.search(prompt)
        and not _PERSISTENT_RE.search(prompt)
    )


def _repair_created_event_intent(prompt: str, intent: EditIntent) -> EditIntent:
    """Keep a semantic planner from turning a bounded reveal into a full shot.

    The language model remains authoritative for ordinary appearance, action,
    and trajectory requests. This only repairs the narrow unsafe case where a
    locally created/revealed object was classified as a continuing trajectory
    despite no request for it to remain or continue.
    """
    if intent.scope != "local" or not _bounded_created_event(prompt):
        return intent
    phases = list(intent.action_phases) or [
        "prepare source motion",
        "introduce requested object",
        "complete requested event",
        "recover source continuity",
    ]
    return intent.model_copy(
        update={
            "duration_policy": "bounded_action",
            "temporal_behavior": "temporary",
            "action_phases": phases,
            "estimated_action_seconds": max(3.0, intent.estimated_action_seconds),
            "requires_recovery_motion": True,
        }
    )


def plan_prompt_intent(
    prompt: str,
    *,
    explicit_range: bool = False,
    selected_occurrences: bool = False,
    requested_scope: EditScope | None = None,
    structured_intent: EditIntent | None = None,
) -> ScopeGateResult:
    """Cheap scope gate; a supplied structured interpretation is never recomputed."""
    if structured_intent is not None:
        intent = _repair_created_event_intent(prompt, structured_intent)
        reason = (
            "bounded created-event safety fallback"
            if intent is not structured_intent
            else "reused structured intent"
        )
    else:
        if requested_scope is not None:
            scope = requested_scope
            reason = "explicit scope"
        elif explicit_range:
            scope = "explicit_range"
            reason = "explicit timeline range"
        elif selected_occurrences:
            scope = "selected_occurrences"
            reason = "selected occurrences"
        elif _GLOBAL_RE.search(prompt):
            scope = "all_occurrences"
            reason = "explicit global language"
        else:
            scope = "local"
            reason = "ambiguous requests default to local"

        change_type = _change_type(prompt)
        phases, action_seconds, recovery = _motion_contract(prompt)
        persistent = bool(_PERSISTENT_RE.search(prompt))
        if _bounded_created_event(prompt):
            temporal_behavior = "temporary"
        elif change_type == "motion" and _TRAJECTORY_RE.search(prompt):
            temporal_behavior = "future_changing_motion"
        elif change_type == "motion":
            temporal_behavior = "temporary"
        else:
            temporal_behavior = "persistent_state"
        preservation = ["preserve unedited subjects", "preserve camera and background"]
        if persistent:
            if "apply change for complete visible occurrence" not in preservation:
                preservation.append("apply change for complete visible occurrence")
        if scope == "all_occurrences":
            duration_policy = "all_occurrences"
        elif scope == "explicit_range":
            duration_policy = "explicit_range"
        elif temporal_behavior == "temporary":
            duration_policy = "bounded_action"
        elif temporal_behavior == "future_changing_motion":
            duration_policy = "trajectory_continuation"
        else:
            # State changes should not visibly revert while the selected target
            # remains in the same continuous appearance.
            duration_policy = "continuous_occurrence"
            if "apply change for complete visible occurrence" not in preservation:
                preservation.append("apply change for complete visible occurrence")
        intent = EditIntent(
            raw_prompt=prompt,
            scope=scope,
            change_type=change_type,
            duration_policy=duration_policy,
            temporal_behavior=temporal_behavior,
            action_phases=phases,
            estimated_action_seconds=action_seconds,
            requires_recovery_motion=recovery,
            preservation_requirements=preservation,
        )

    global_search = intent.scope == "all_occurrences"
    continuous_local = intent.scope == "local" and intent.temporal_behavior in {
        "persistent_state",
        "future_changing_motion",
    }
    if global_search:
        mode = "coarse_global_then_dense_candidates"
        tracking_seconds = None
    elif intent.scope == "selected_occurrences":
        mode = "selected_occurrences_only"
        tracking_seconds = None
    elif continuous_local:
        mode = "complete_local_occurrence"
        tracking_seconds = None
    elif intent.scope == "explicit_range":
        mode = "explicit_range_with_handles"
        tracking_seconds = intent.estimated_action_seconds + 2.0
    else:
        mode = "lazy_local"
        tracking_seconds = intent.estimated_action_seconds + 2.0

    estimate = PlanningEstimate(
        analysis_mode=mode,
        expected_tracking_seconds=tracking_seconds,
        expected_generation_calls=1,
        expected_generated_seconds=intent.estimated_action_seconds + 2.0,
        requires_global_discovery=global_search,
    )
    return ScopeGateResult(intent=intent, estimate=estimate, reason=reason)


def should_discover_occurrences(*, scope: EditScope, explicitly_requested: bool) -> bool:
    """Full-reel work requires explicit global intent or explicit user action."""
    return explicitly_requested or scope == "all_occurrences"
