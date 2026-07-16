from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import BBox


class GenerateRequest(BaseModel):
    project_id: str
    target_clip_id: str | None = None
    plan_id: str | None = None
    start_ts: float | None = Field(default=None, ge=0.0)
    end_ts: float | None = Field(default=None, gt=0.0)
    bbox: BBox | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    reference_frame_ts: float | None = Field(default=None, ge=0.0)
    video_gen_provider: Literal["auto", "wan", "happyhorse", "veo", "meshapi_veo"] | None = None

    @model_validator(mode="after")
    def validate_legacy_or_plan(self) -> "GenerateRequest":
        legacy = (
            self.start_ts,
            self.end_ts,
            self.bbox,
            self.prompt,
            self.reference_frame_ts,
        )
        if self.plan_id is None and any(value is None for value in legacy):
            raise ValueError("plan_id or complete legacy generation fields are required")
        return self


class GenerateResponse(BaseModel):
    job_id: str
