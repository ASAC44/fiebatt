"""Timeline endpoints — get (read current state) + put (save EDL snapshot).

GET returns both shapes: the flat segment span list (`segments`) built from
the DB, and when the user has saved manual edits, the full EDL snapshot
(`edl`) with split/trim/reorder/volume preserved. Clients that understand
the EDL should prefer it; the segment list remains for legacy callers and
a coarse fallback.

PUT accepts the full EDL and writes it to `Project.timeline_edl`. This is
the only mutation path for manual edits — AI accepts still flow through
/api/accept (which writes a Segment row) and the next auto-save picks up
the resulting clip.
"""
import time
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.schemas.timeline import (
    PersistedEDL,
    TimelineOut,
    TimelineSaveReq,
    TimelineSaveResp,
    TimelineSegment,
)
from app.services import storage
from app.services.timeline_builder import build_timeline

router = APIRouter(tags=["timeline"])


def _rehydrate_edl(raw: dict | None) -> PersistedEDL | None:
    """Turn a JSON blob from the DB into a fully re-signed EDL, or None
    if the project has no saved snapshot yet. Every url we emit here gets
    passed through `storage.normalize_url_like` so stale presigned links
    mint fresh signatures on read — keeping the frontend's <video> nodes
    playable no matter how long ago the EDL was written."""
    if not raw:
        return None
    try:
        edl = PersistedEDL.model_validate(raw)
    except Exception:
        # A malformed blob shouldn't brick the whole reel. Fall back to
        # the segment-based view so the user at least sees something.
        return None

    for clip in edl.clips:
        clip.url = storage.normalize_url_like(clip.url, fallback=clip.url)
    for asset in edl.sources:
        asset.url = storage.normalize_url_like(asset.url, fallback=asset.url)
    return edl


@router.get("/timeline/{project_id}", response_model=TimelineOut)
async def get_timeline(
    project_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    items = await build_timeline(db, proj)

    return TimelineOut(
        project_id=proj.id,
        duration=proj.duration,
        segments=[
            TimelineSegment(
                start_ts=it.start_ts,
                end_ts=it.end_ts,
                source=it.source,
                url=storage.normalize_url_like(it.url, fallback=it.url),
                audio=it.audio,
                segment_id=it.id,
            )
            for it in items
        ],
        edl=_rehydrate_edl(cast(dict | None, proj.timeline_edl)),
    )


@router.put("/timeline/{project_id}", response_model=TimelineSaveResp)
async def save_timeline(
    project_id: str,
    body: TimelineSaveReq,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    """Persist a full EDL snapshot. Idempotent — called on debounce whenever
    the user's frontend state settles. Body must contain the entire EDL,
    not a delta; this keeps the server logic trivially correct and makes
    concurrent edits last-writer-wins (fine for single-user reels)."""
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    now = time.time()
    edl_payload = PersistedEDL(
        clips=body.clips,
        sources=body.sources,
        updated_at=now,
    ).model_dump(mode="json")
    proj.timeline_edl = edl_payload
    await db.commit()

    # nothing returned but the echo — the client keeps its own state and
    # just needs to know the save went through.
    return TimelineSaveResp(project_id=proj.id, updated_at=now)
