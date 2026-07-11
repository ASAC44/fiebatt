from pydantic import BaseModel, Field

from app.schemas.common import BBox


class GenerateRequest(BaseModel):
    project_id: str
    start_ts: float = Field(ge=0.0)
    end_ts: float = Field(gt=0.0)
    bbox: BBox
    prompt: str = Field(min_length=1, max_length=2000)
    reference_frame_ts: float = Field(ge=0.0)


class GenerateResponse(BaseModel):
    job_id: str
