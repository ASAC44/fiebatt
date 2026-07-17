from pydantic import BaseModel, Field

from app.schemas.common import BBox


class IdentifyRequest(BaseModel):
    project_id: str
    target_clip_id: str | None = None
    frame_ts: float = Field(ge=0.0)
    bbox: BBox


class MaskOut(BaseModel):
    contour: list[list[float]]


class IdentifyResponse(BaseModel):
    description: str
    category: str
    attributes: dict
    mask: MaskOut | None = None
