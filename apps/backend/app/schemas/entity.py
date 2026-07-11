from pydantic import BaseModel


class AppearanceOut(BaseModel):
    id: str
    segment_id: str | None = None
    start_ts: float
    end_ts: float
    keyframe_url: str | None = None
    confidence: float


class EntityOut(BaseModel):
    entity_id: str
    description: str
    category: str | None = None
    reference_crop_url: str | None = None
    appearances: list[AppearanceOut]
