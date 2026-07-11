from pydantic import BaseModel, Field

from app.schemas.common import BBox


class MaskRequest(BaseModel):
    project_id: str
    frame_ts: float = Field(ge=0.0)
    bbox: BBox


class MaskResponse(BaseModel):
    contour: list[list[float]]
