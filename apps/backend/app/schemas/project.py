from pydantic import BaseModel

from app.schemas.common import SegmentSource


class SegmentOut(BaseModel):
    id: str
    start_ts: float
    end_ts: float
    source: SegmentSource
    url: str
    variant_id: str | None = None
    order_index: int


class EntitySummary(BaseModel):
    id: str
    description: str
    category: str | None = None
    appearance_count: int


class ProjectOut(BaseModel):
    project_id: str
    video_url: str
    duration: float
    fps: float
    width: int
    height: int
    segments: list[SegmentOut]
    entities: list[EntitySummary]
