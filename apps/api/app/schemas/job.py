from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

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
    created_at: datetime | None = None
    accepted: bool = False
    variants: list[VariantOut] = []
    # authoritative edit window used when accepting a generated replacement.
    start_ts: float | None = None
    end_ts: float | None = None
    provider: str | None = None
    model: str | None = None
    edit_mode: str | None = None
    warnings: list[str] = Field(default_factory=list)
    execution_window: dict[str, Any] | None = None
    continuity_validation: dict[str, Any] | None = None
    generation_quality_state: str | None = None
    generation_quality_evidence: list[str] = Field(default_factory=list)
    generation_attempts: int | None = None
    generated_seconds: float | None = None
    provider_attempts: list[str] = Field(default_factory=list)
    localized_compositing: list[dict[str, Any]] = Field(default_factory=list)
    local_flow_telemetry: dict[str, Any] | None = None
