from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.edit_plan import (
    EditCore,
    EditIntent,
    EditScope,
    GenerationContext,
)


VideoProvider = Literal["auto", "wan", "happyhorse", "veo", "meshapi_veo"]


class EditPlanRequest(BaseModel):
    project_id: str
    selection_id: str
    prompt: str = Field(min_length=1, max_length=2000)
    requested_scope: EditScope | None = None
    explicit_start_ts: float | None = Field(default=None, ge=0.0)
    explicit_end_ts: float | None = Field(default=None, gt=0.0)
    video_gen_provider: VideoProvider = "auto"
    structured_intent: EditIntent | None = None

    @model_validator(mode="after")
    def validate_explicit_range(self) -> "EditPlanRequest":
        supplied = self.explicit_start_ts is not None or self.explicit_end_ts is not None
        if supplied and (self.explicit_start_ts is None or self.explicit_end_ts is None):
            raise ValueError("explicit_start_ts and explicit_end_ts must be supplied together")
        if supplied and self.explicit_end_ts <= self.explicit_start_ts:
            raise ValueError("explicit_end_ts must be after explicit_start_ts")
        return self


class PlanEstimateResponse(BaseModel):
    analysis_mode: str
    frames_inspected: int
    expected_generation_calls: int
    expected_generated_seconds: float
    requires_global_discovery: bool


class GenerationChunkResponse(BaseModel):
    id: str
    index: int
    edit_core: EditCore
    generation_context: GenerationContext
    provider: str
    status: str


class EditPlanResponse(BaseModel):
    plan_id: str
    project_id: str
    selection_id: str
    scope: EditScope
    intent: EditIntent
    edit_core: EditCore
    generation_context: GenerationContext
    occurrence_start: float
    occurrence_end: float
    provider: str
    provider_reason: str
    estimate: PlanEstimateResponse
    confidence: float
    warnings: list[str]
    chunks: list[GenerationChunkResponse]
    status: str
