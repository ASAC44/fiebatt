"""Accept a generated variant.

Lazy-render model: accepting a variant writes a Segment row. Entity search
is separately opt-in. No ffmpeg, no stitching, no
full-project re-encode, no proj.video_url mutation. The timeline is
reconstructed on read by walking Segment rows, and the final MP4 is
rendered exactly once when the user hits Export.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.config.settings import get_settings
from app.deps import get_runner, get_session
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.accept import AcceptRequest, AcceptResponse
from app.services.entity_discovery import enqueue_entity_discovery
from app.services.accepted_generation import (
    accepted_generation_range,
    record_accepted_range,
    update_project_edl_for_acceptance,
)
from app.services.generation_quality import (
    acceptance_allowed,
    acceptance_block_reason,
    cancel_waiting_retry,
    quality_payload_for_candidate,
)
from app.services.timeline_response import build_timeline_response
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
    job.payload = cancel_waiting_retry(job.payload, reason="candidate applied")
    quality_payload = quality_payload_for_candidate(job.payload, variant.id)
    if not acceptance_allowed(
        quality_payload,
        override_requested=body.continuity_override,
        override_enabled=get_settings().allow_hard_failed_acceptance,
    ):
        raise HTTPException(status_code=409, detail=acceptance_block_reason(quality_payload))

    if job.start_ts is None or job.end_ts is None:
        raise HTTPException(status_code=422, detail="job has no segment range")
    accepted_range = accepted_generation_range(job, variant=variant)

    # deactivate any existing generated segments that overlap this range.
    # the newest accept wins on overlap. we don't delete rows so we keep
    # a history for potential future "revert" UX.
    overlapping = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == proj.id,
                Segment.active == True,  # noqa: E712
                Segment.source == "generated",
                Segment.start_ts < accepted_range.committed_end,
                Segment.end_ts > accepted_range.committed_start,
            )
        )
    ).scalars().all()
    for s in overlapping:
        s.active = False

    seg = Segment(
        project_id=proj.id,
        start_ts=accepted_range.committed_start,
        end_ts=accepted_range.committed_end,
        source="generated",
        url=variant.url,
        variant_id=variant.id,
        order_index=int(accepted_range.committed_start * 1000),
        active=True,
    )
    db.add(seg)
    await db.flush()
    record_accepted_range(job, segment_id=seg.id, accepted_range=accepted_range)
    update_project_edl_for_acceptance(
        proj,
        segment_id=seg.id,
        variant=variant,
        accepted_range=accepted_range,
    )
    await db.commit()
    await db.refresh(seg)

    entity_job_id: str | None = None
    if body.discover_occurrences:
        ent_job, reused = await enqueue_entity_discovery(
            db,
            project=proj,
            segment=seg,
            source_job=job,
            reference_variant_url=variant.url,
        )
        if ent_job is not None:
            await db.commit()
            if not reused:
                runner.submit(ent_job.id, lambda: entity_job.run(ent_job.id))
            entity_job_id = ent_job.id

    await db.refresh(proj)
    return AcceptResponse(
        segment_id=seg.id,
        entity_job_id=entity_job_id,
        timeline=await build_timeline_response(db, proj),
    )
