"""Accept a generated variant.

Lazy-render model: accepting a variant writes a Segment row + enqueues
the entity-search background job. That's it. No ffmpeg, no stitching, no
full-project re-encode, no proj.video_url mutation. The timeline is
reconstructed on read by walking Segment rows, and the final MP4 is
rendered exactly once when the user hits Export.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.deps import get_runner, get_session
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.accept import AcceptRequest, AcceptResponse
from app.workers import entity_job

router = APIRouter(tags=["accept"])


@router.post("/accept", response_model=AcceptResponse)
async def accept(
    body: AcceptRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
    runner=Depends(get_runner),
):
    job = (
        await db.execute(
            select(Job)
            .where(Job.id == body.job_id)
            .options(selectinload(Job.variants))
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    proj = await db.get(Project, job.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="job not found")

    variant: Variant | None = next(
        (v for v in job.variants if v.index == body.variant_index), None
    )
    if variant is None or variant.status != "done" or not variant.url:
        raise HTTPException(status_code=422, detail="variant not ready")

    if job.start_ts is None or job.end_ts is None:
        raise HTTPException(status_code=422, detail="job has no segment range")

    # deactivate any existing generated segments that overlap this range.
    # the newest accept wins on overlap. we don't delete rows so we keep
    # a history for potential future "revert" UX.
    overlapping = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == proj.id,
                Segment.active == True,  # noqa: E712
                Segment.source == "generated",
                Segment.start_ts < job.end_ts,
                Segment.end_ts > job.start_ts,
            )
        )
    ).scalars().all()
    for s in overlapping:
        s.active = False

    seg = Segment(
        project_id=proj.id,
        start_ts=job.start_ts,
        end_ts=job.end_ts,
        source="generated",
        url=variant.url,
        variant_id=variant.id,
        order_index=int(job.start_ts * 1000),
        active=True,
    )
    db.add(seg)
    await db.commit()
    await db.refresh(seg)

    # fire an entity-search job — but only when the generation actually has a
    # concrete entity to track. For full-frame regenerations (no bbox drawn)
    # and for "remove" intents there's nothing meaningful to identify, and
    # kicking the job off anyway produces a red "continuity error" chip on
    # the frontend when Gemini inevitably fails to ID a subject. Skip those
    # and the UI renders the idle "X tracked" pill instead.
    bbox = job.bbox_json or {}
    bbox_w = float(bbox.get("w", 0.0)) if isinstance(bbox, dict) else 0.0
    bbox_h = float(bbox.get("h", 0.0)) if isinstance(bbox, dict) else 0.0
    bbox_is_tracked = bool(bbox) and bbox_w < 0.98 and bbox_h < 0.98 and bbox_w * bbox_h > 0.0

    entity_job_id: str | None = None
    if bbox_is_tracked:
        ent_job = Job(
            project_id=proj.id,
            kind="entity",
            status="pending",
            payload={
                "segment_id": seg.id,
                "reference_frame_ts": job.reference_frame_ts,
                "reference_variant_url": variant.url,
                "bbox": job.bbox_json,
            },
        )
        db.add(ent_job)
        await db.commit()
        await db.refresh(ent_job)

        runner.submit(ent_job.id, lambda: entity_job.run(ent_job.id))
        entity_job_id = ent_job.id

    return AcceptResponse(segment_id=seg.id, entity_job_id=entity_job_id)
