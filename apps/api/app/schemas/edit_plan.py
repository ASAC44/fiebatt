from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import BBox


EditScope = Literal[
    "local",
    "explicit_range",
    "selected_occurrences",
    "all_occurrences",
]
ChangeType = Literal["appearance", "removal", "replacement", "motion", "scene"]
DurationPolicy = Literal[
    "bounded_action",
    "continuous_occurrence",
    "trajectory_continuation",
    "explicit_range",
    "all_occurrences",
]
TemporalBehavior = Literal[
    "temporary",
    "persistent_state",
    "future_changing_motion",
]
EffectExtent = Literal[
    "surface",
    "subject",
    "motion_path",
    "new_object_path",
    "scene",
]


class GroundedEditInstruction(BaseModel):
    intent: str
    conditioning_strategy: Literal["first_frame", "text_only"] = "first_frame"
    description: str
    tone: str = "original visual tone unless explicitly changed"
    color_grading: str = "original grade unless explicitly changed"
    region_emphasis: str
    prompt_for_video_edit: str = Field(min_length=1, max_length=4000)


class SemanticEditDecision(BaseModel):
    scope: EditScope
    change_type: ChangeType
    duration_policy: DurationPolicy
    temporal_behavior: TemporalBehavior
    effect_extent: EffectExtent = "subject"
    expected_new_objects: list[str] = Field(default_factory=list)
    target_description: str | None = None
    selection_match: Literal["match", "mismatch", "uncertain"] = "match"
    selection_match_reason: str | None = None
    action_phases: list[str] = Field(default_factory=list)
    observable_success: str | None = Field(default=None, max_length=300)
    estimated_action_seconds: float = Field(default=3.0, ge=0.5, le=15.0)
    requires_recovery_motion: bool = False
    preservation_requirements: list[str] = Field(default_factory=list)
    reasoning: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_temporal_behavior(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            if not value.get("temporal_behavior"):
                policy = value.get("duration_policy")
                value["temporal_behavior"] = (
                    "future_changing_motion"
                    if policy == "trajectory_continuation"
                    else "persistent_state"
                    if policy == "continuous_occurrence"
                    else "temporary"
                )
            # Some compatible chat models use zero to mean "not applicable"
            # for a persistent state change. Duration is irrelevant to the
            # occurrence tracker, so repair that harmless value instead of
            # discarding the full grounded semantic plan.
            try:
                if float(value.get("estimated_action_seconds", 3.0)) <= 0.0:
                    value["estimated_action_seconds"] = 3.0
            except (TypeError, ValueError):
                pass
        return value


class SemanticEditPlan(BaseModel):
    decision: SemanticEditDecision
    variants: list[GroundedEditInstruction] = Field(min_length=1, max_length=1)

    def as_intent(self, raw_prompt: str) -> "EditIntent":
        decision = self.decision
        return EditIntent(
            raw_prompt=raw_prompt,
            scope=decision.scope,
            change_type=decision.change_type,
            duration_policy=decision.duration_policy,
            temporal_behavior=decision.temporal_behavior,
            effect_extent=decision.effect_extent,
            expected_new_objects=decision.expected_new_objects,
            target_description=decision.target_description,
            action_phases=decision.action_phases,
            observable_success=decision.observable_success,
            estimated_action_seconds=decision.estimated_action_seconds,
            requires_recovery_motion=decision.requires_recovery_motion,
            preservation_requirements=decision.preservation_requirements,
            reasoning=decision.reasoning,
            grounded_edit=self.variants[0],
        )


class EditIntent(BaseModel):
    raw_prompt: str = Field(min_length=1, max_length=2000)
    scope: EditScope = "local"
    change_type: ChangeType
    duration_policy: DurationPolicy = "bounded_action"
    temporal_behavior: TemporalBehavior = "temporary"
    effect_extent: EffectExtent = "subject"
    expected_new_objects: list[str] = Field(default_factory=list)
    target_description: str | None = None
    action_phases: list[str] = Field(default_factory=list)
    observable_success: str | None = Field(default=None, max_length=300)
    estimated_action_seconds: float = Field(default=3.0, gt=0.0)
    requires_recovery_motion: bool = False
    preservation_requirements: list[str] = Field(default_factory=list)
    reasoning: str | None = None
    grounded_edit: GroundedEditInstruction | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_temporal_behavior(cls, value):
        if isinstance(value, dict) and not value.get("temporal_behavior"):
            value = dict(value)
            policy = value.get("duration_policy")
            value["temporal_behavior"] = (
                "future_changing_motion"
                if policy == "trajectory_continuation"
                else "persistent_state"
                if policy == "continuous_occurrence"
                else "temporary"
            )
        return value


class EditCore(BaseModel):
    start_ts: float = Field(ge=0.0)
    end_ts: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_order(self) -> "EditCore":
        if self.end_ts <= self.start_ts:
            raise ValueError("edit core end must be after start")
        return self

    @property
    def duration(self) -> float:
        return self.end_ts - self.start_ts


class BoundaryAnchor(BaseModel):
    timestamp: float = Field(ge=0.0)
    frame_url: str | None = None
    subject_bbox: BBox | None = None
    subject_velocity: tuple[float, float] | None = None
    camera_velocity: tuple[float, float] | None = None


class GenerationContext(BaseModel):
    start_ts: float = Field(ge=0.0)
    end_ts: float = Field(gt=0.0)
    edit_core: EditCore
    start_anchor: BoundaryAnchor | None = None
    end_anchor: BoundaryAnchor | None = None

    @model_validator(mode="after")
    def validate_contains_core(self) -> "GenerationContext":
        if self.end_ts <= self.start_ts:
            raise ValueError("generation context end must be after start")
        if self.edit_core.start_ts < self.start_ts or self.edit_core.end_ts > self.end_ts:
            raise ValueError("generation context must contain edit core")
        return self

    @property
    def duration(self) -> float:
        return self.end_ts - self.start_ts


class LocalRangeResolution(BaseModel):
    edit_core: EditCore
    generation_context: GenerationContext
    occurrence_start: float
    occurrence_end: float
    analysis_start: float
    analysis_end: float
    frames_inspected: int = 0
    tracked_frames: list[dict] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class LegacyRange(BaseModel):
    """Compatibility boundary for current start_ts/end_ts generation requests."""

    start_ts: float = Field(ge=0.0)
    end_ts: float = Field(gt=0.0)
    reference_frame_ts: float = Field(ge=0.0)
    bbox: BBox

    def as_generation_context(self) -> GenerationContext:
        core = EditCore(start_ts=self.start_ts, end_ts=self.end_ts)
        return GenerationContext(start_ts=self.start_ts, end_ts=self.end_ts, edit_core=core)
