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


class EditIntent(BaseModel):
    raw_prompt: str = Field(min_length=1, max_length=2000)
    scope: EditScope = "local"
    change_type: ChangeType
    target_description: str | None = None
    action_phases: list[str] = Field(default_factory=list)
    estimated_action_seconds: float = Field(default=3.0, gt=0.0)
    requires_recovery_motion: bool = False
    preservation_requirements: list[str] = Field(default_factory=list)


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


class LegacyRange(BaseModel):
    """Compatibility boundary for current start_ts/end_ts generation requests."""

    start_ts: float = Field(ge=0.0)
    end_ts: float = Field(gt=0.0)
    reference_frame_ts: float = Field(ge=0.0)
    bbox: BBox

    def as_generation_context(self) -> GenerationContext:
        core = EditCore(start_ts=self.start_ts, end_ts=self.end_ts)
        return GenerationContext(start_ts=self.start_ts, end_ts=self.end_ts, edit_core=core)
