from pydantic import BaseModel


class AcceptRequest(BaseModel):
    job_id: str
    # single-edit mode only ever produces index 0. kept for backward-compat.
    variant_index: int = 0
    # Full-reel entity search is opt-in. Local acceptance stays local and cheap.
    discover_occurrences: bool = False


class AcceptResponse(BaseModel):
    segment_id: str
    entity_job_id: str | None = None
