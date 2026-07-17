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
)
from app.services.timeline_response import build_timeline_response

router = APIRouter(tags=["timeline"])


@router.get("/timeline/{project_id}", response_model=TimelineOut)
async def get_timeline(
    project_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    return await build_timeline_response(db, proj)


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
