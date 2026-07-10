import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.deps import get_session
from app.models.job import Job
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.schemas.job import JobOut, VariantOut
from app.services import job_events

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    job = (
        await db.execute(
            select(Job).where(Job.id == job_id).options(selectinload(Job.variants))
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    # enforce session ownership through the project
    proj = await db.get(Project, job.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="job not found")

    return JobOut(
        job_id=job.id,
        kind=job.kind,
        status=job.status,  # type: ignore[arg-type]
        error=job.error,
        variants=[
            VariantOut(
                id=v.id,
                index=v.index,
                status=v.status,  # type: ignore[arg-type]
                url=v.url,
                description=v.description,
                visual_coherence=v.visual_coherence,
                prompt_adherence=v.prompt_adherence,
                error=v.error,
            )
            for v in sorted(job.variants, key=lambda v: v.index)
        ],
    )


@router.get("/jobs/{job_id}/stream")
async def stream_job_events(
    job_id: str,
    request: Request,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    """SSE feed of structured "thought process" events for a running job.

    Each event is emitted as ``data: {json}\\n\\n``. The stream closes
    automatically once a terminal event (done/error) is received, or when
    the client disconnects.

    Late subscribers replay history before blocking on new events so the
    console UI can reconstruct the full story even if the network drops.
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    proj = await db.get(Project, job.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="job not found")

    async def _iter_sse():
        # keep-alive comment every ~15s so proxies don't close idle streams.
        last_send = asyncio.get_event_loop().time()
        async for event in job_events.subscribe(job_id):
            if await request.is_disconnected():
                return
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("terminal"):
                return
            now = asyncio.get_event_loop().time()
            if now - last_send > 15:
                yield ": keepalive\n\n"
            last_send = now

    return StreamingResponse(
        _iter_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
