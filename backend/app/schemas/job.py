from pydantic import BaseModel

from app.schemas.common import JobStatus


class VariantOut(BaseModel):
    id: str
    index: int
    status: JobStatus
    url: str | None = None
    description: str | None = None
    visual_coherence: int | None = None
    prompt_adherence: int | None = None
    error: str | None = None


class JobOut(BaseModel):
    job_id: str
    kind: str
    status: JobStatus
    error: str | None = None
    variants: list[VariantOut] = []
    # authoritative edit window used when accepting a generated replacement.
    start_ts: float | None = None
    end_ts: float | None = None
