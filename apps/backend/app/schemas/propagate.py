from pydantic import BaseModel

from app.schemas.common import JobStatus


class PropagateRequest(BaseModel):
    entity_id: str
    source_variant_url: str
    prompt: str
    auto_apply: bool = True


class PropagateResponse(BaseModel):
    propagation_job_id: str


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
