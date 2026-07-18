import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
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
from app.services import job_events, storage
from app.services.generation_quality import normalized_quality_state

router = APIRouter(tags=["jobs"])


class RetryDecisionRequest(BaseModel):
    action: Literal["cancel", "retry_now"]


def _job_out(job: Job) -> JobOut:
    payload = job.payload or {}
    candidate_reviews = payload.get("candidate_reviews") or {}
    return JobOut(
        job_id=job.id,
        kind=job.kind,
        status=job.status,  # type: ignore[arg-type]
        error=job.error,
        created_at=job.created_at,
        accepted=bool(payload.get("latest_accepted_segment_id")),
        start_ts=job.start_ts,
        end_ts=job.end_ts,
        provider=payload.get("selected_provider"),
        model=payload.get("selected_model"),
        edit_mode=payload.get("selected_edit_mode"),
        warnings=payload.get("warnings") or [],
        execution_window=payload.get("execution_window"),
        continuity_validation=payload.get("continuity_validation"),
        selected_seams=payload.get("selected_seams"),
        generation_quality_state=normalized_quality_state(
            payload.get("generation_quality_state"),
            payload.get("generation_quality_evidence"),
        ),
        generation_quality_evidence=payload.get("generation_quality_evidence") or [],
        generation_attempts=payload.get("generation_attempts"),
        generated_seconds=payload.get("generated_seconds"),
        provider_attempts=payload.get("provider_attempts") or [],
        localized_compositing=payload.get("localized_compositing") or [],
        local_flow_telemetry=payload.get("local_flow_telemetry"),
        retry_state=payload.get("retry_state"),
        progress_state=payload.get("progress_state"),
        failure_state=payload.get("failure_state"),
        variants=[
            VariantOut(
                id=v.id,
                index=v.index,
                status=v.status,  # type: ignore[arg-type]
                url=storage.normalize_url_like(v.url, fallback=v.url) if v.url else None,
                description=v.description,
                visual_coherence=v.visual_coherence,
                prompt_adherence=v.prompt_adherence,
                error=v.error,
                attempt_label=(candidate_reviews.get(v.id) or {}).get("label"),
                quality_state=normalized_quality_state(
                    (candidate_reviews.get(v.id) or {}).get("quality_state"),
                    (candidate_reviews.get(v.id) or {}).get("evidence"),
                ),
                quality_evidence=(candidate_reviews.get(v.id) or {}).get("evidence") or [],
                continuity_validation=(candidate_reviews.get(v.id) or {}).get(
                    "continuity_validation"
                ),
                selected_seams=(candidate_reviews.get(v.id) or {}).get("selected_seams"),
            )
            for v in sorted(job.variants, key=lambda v: v.index)
        ],
    )


@router.get("/projects/{project_id}/generation-jobs", response_model=list[JobOut])
async def list_generation_jobs(
    project_id: str,
    limit: int = Query(default=10, ge=1, le=20),
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    """Recent generation jobs used to restore work after page navigation."""
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")
    rows = (
        await db.execute(
            select(Job)
            .where(Job.project_id == project_id, Job.kind == "generate")
            .options(selectinload(Job.variants))
            .order_by(Job.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [_job_out(job) for job in rows]


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

    return _job_out(job)


@router.post("/jobs/{job_id}/retry-decision")
async def decide_retry(
    job_id: str,
    body: RetryDecisionRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    project = await db.get(Project, job.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="job not found")
    payload = dict(job.payload or {})
    retry_state = dict(payload.get("retry_state") or {})
    if retry_state.get("status") != "waiting":
        raise HTTPException(status_code=409, detail="no retry is waiting for a decision")
    retry_state["status"] = "cancelled" if body.action == "cancel" else "retry_now"
    retry_state["decision_at"] = time.time()
    payload["retry_state"] = retry_state
    job.payload = payload
    await db.commit()
    return {"status": retry_state["status"]}


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
