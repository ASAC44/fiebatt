from pydantic import BaseModel

from app.schemas.timeline import TimelineOut


class AcceptRequest(BaseModel):
    job_id: str
    # single-edit mode only ever produces index 0. kept for backward-compat.
    variant_index: int = 0
    # Full-reel entity search is opt-in. Local acceptance stays local and cheap.
    discover_occurrences: bool = False
    # Honored only when the backend operator enables the emergency override.
    continuity_override: bool = False


class AcceptResponse(BaseModel):
    segment_id: str
    entity_job_id: str | None = None
    timeline: TimelineOut
