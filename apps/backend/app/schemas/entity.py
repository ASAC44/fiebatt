from pydantic import BaseModel, Field


class AppearanceOut(BaseModel):
    id: str
    segment_id: str | None = None
    start_ts: float
    end_ts: float
    keyframe_url: str | None = None
    confidence: float


class OccurrenceCandidateOut(BaseModel):
    id: str
    keyframe_ts: float
    start_ts: float
    end_ts: float
    keyframe_url: str | None = None
    confidence: float
    evidence: dict
    status: str


class OccurrenceTrackOut(BaseModel):
    id: str
    candidate_id: str
    seed_ts: float
    start_ts: float
    end_ts: float
    confidence: float
    tracker: str
    status: str
    reason: str | None = None


class EntityOut(BaseModel):
    entity_id: str
    description: str
    category: str | None = None
    reference_crop_url: str | None = None
    appearances: list[AppearanceOut]
    occurrence_candidates: list[OccurrenceCandidateOut] = Field(default_factory=list)
    occurrence_tracks: list[OccurrenceTrackOut] = Field(default_factory=list)


class DiscoveryJobOut(BaseModel):
    job_id: str
    reused: bool = False
