from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import SegmentSource


class TimelineSegment(BaseModel):
    """Flat "playable span" view — what `build_timeline` emits and what
    existing callers (legacy hydration path, early versions of the export
    worker) consume. Preserved on the wire for backwards compatibility
    with reels created before we started saving the full EDL."""

    start_ts: float
    end_ts: float
    source: SegmentSource
    url: str
    audio: bool
    segment_id: str | None = None
    media_start_ts: float = Field(ge=0)
    media_end_ts: float = Field(ge=0)
    media_duration: float = Field(ge=0)


# ─── persisted EDL (edit decision list) ─────────────────────────────────
#
# Mirrors the frontend's `Clip` / `MediaAsset` shapes one-to-one so the
# studio can round-trip state without lossy transformation. Persisted as
# a JSON blob on Project.timeline_edl and re-emitted on read (with all
# URLs passed through storage.normalize_url_like so stale presigned links
# get fresh signatures transparently).

ClipKind = Literal["source", "generated"]


class PersistedClip(BaseModel):
    id: str
    kind: ClipKind
    url: str
    source_start: float = Field(ge=0)
    source_end: float = Field(ge=0)
    media_duration: float = Field(ge=0)
    volume: float = Field(ge=0, le=1, default=1.0)
    label: str | None = None
    project_id: str | None = None
    source_asset_id: str | None = None
    generated_from_clip_id: str | None = None


class PersistedAsset(BaseModel):
    id: str
    kind: ClipKind
    url: str
    duration: float = Field(ge=0)
    fps: float = Field(ge=0)
    project_id: str
    label: str


class PersistedEDL(BaseModel):
    clips: list[PersistedClip]
    sources: list[PersistedAsset]
    # seconds since epoch. written by the server on save so clients can
    # show a "saved · 3s ago" indicator without trusting their own clock.
    updated_at: float | None = None


class TimelineSaveReq(BaseModel):
    """Body for `PUT /api/timeline/{project_id}` — full EDL snapshot."""

    clips: list[PersistedClip]
    sources: list[PersistedAsset]


class TimelineSaveResp(BaseModel):
    project_id: str
    updated_at: float


class TimelineOut(BaseModel):
    project_id: str
    duration: float
    # Always populated. For EDL-aware clients this is just a fallback view;
    # for legacy clients it's still the whole timeline.
    segments: list[TimelineSegment]
    # Present only when the user has saved at least one manual edit on
    # this reel. When present, clients should prefer `edl` — it encodes
    # splits/trims/reorders that the flat `segments` view can't express.
    edl: PersistedEDL | None = None
