from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import JobStatus


class PropagateRequest(BaseModel):
    global_plan_id: str | None = None
    entity_id: str | None = None
    source_variant_url: str | None = None
    prompt: str | None = None
    auto_apply: bool = True


class PropagateResponse(BaseModel):
    propagation_job_id: str
    global_plan_id: str | None = None


class PropagationResultOut(BaseModel):
    id: str
    appearance_id: str
    segment_id: str | None = None
    variant_url: str | None = None
    status: JobStatus
    applied: bool


class PropagationStatus(BaseModel):
    propagation_job_id: str
    status: JobStatus
    error: str | None = None
    results: list[PropagationResultOut]


class GlobalEditPlanRequest(BaseModel):
    entity_id: str
    reference_segment_id: str
    scope: Literal["selected_occurrences", "all_occurrences"] = "all_occurrences"
    occurrence_ids: list[str] = Field(default_factory=list)
    video_gen_provider: Literal[
        "auto", "wan", "happyhorse", "veo", "meshapi_veo"
    ] = "auto"


class PlannedChunkOut(BaseModel):
    chunk_id: str
    index: int
    edit_start: float
    edit_end: float
    context_start: float
    context_end: float
    provider: str
    split_reason: str
    status: str
    attempts: int
    output_url: str | None = None
    error: str | None = None


class PlannedOccurrenceOut(BaseModel):
    appearance_id: str
    start_ts: float
    end_ts: float
    confidence: float
    status: str
    output_url: str | None = None
    error: str | None = None
    chunks: list[PlannedChunkOut] = Field(default_factory=list)


class GlobalEditPlanOut(BaseModel):
    plan_id: str
    project_id: str
    entity_id: str
    reference_segment_id: str
    scope: str
    requested_provider: str
    prompt: str
    occurrences: list[PlannedOccurrenceOut]
    estimate: dict
    status: str
