from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.deps import get_runner, get_session
from app.models.entity import Entity
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.entity import (
    AppearanceOut,
    DiscoveryJobOut,
    EntityOut,
    OccurrenceCandidateOut,
)
from app.services.entity_discovery import enqueue_entity_discovery
from app.workers import entity_job

router = APIRouter(tags=["entities"])


@router.post(
    "/segments/{segment_id}/discover-occurrences",
    response_model=DiscoveryJobOut,
)
async def discover_occurrences(
    segment_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
    runner=Depends(get_runner),
):
    segment = await db.get(Segment, segment_id)
    if segment is None or segment.source != "generated" or not segment.variant_id:
        raise HTTPException(status_code=404, detail="accepted generated segment not found")
    project = await db.get(Project, segment.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="accepted generated segment not found")
    variant = await db.get(Variant, segment.variant_id)
    if variant is None or not variant.url:
        raise HTTPException(status_code=422, detail="accepted variant is unavailable")
    source_job = await db.get(Job, variant.job_id)
    if source_job is None:
        raise HTTPException(status_code=422, detail="source generation job is unavailable")

    job, reused = await enqueue_entity_discovery(
        db,
        project=project,
        segment=segment,
        source_job=source_job,
        reference_variant_url=variant.url,
    )
    if job is None:
        raise HTTPException(status_code=422, detail="accepted edit has no trackable region")
    await db.commit()
    if not reused:
        runner.submit(job.id, lambda: entity_job.run(job.id))
    return DiscoveryJobOut(job_id=job.id, reused=reused)


@router.get("/entities/{entity_id}", response_model=EntityOut)
async def get_entity(
    entity_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    entity = (
        await db.execute(
            select(Entity)
            .where(Entity.id == entity_id)
            .options(
                selectinload(Entity.appearances),
                selectinload(Entity.occurrence_candidates),
            )
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")

    proj = await db.get(Project, entity.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="entity not found")

    return EntityOut(
        entity_id=entity.id,
        description=entity.description,
        category=entity.category,
        reference_crop_url=entity.reference_crop_url,
        appearances=[
            AppearanceOut(
                id=a.id,
                segment_id=a.segment_id,
                start_ts=a.start_ts,
                end_ts=a.end_ts,
                keyframe_url=a.keyframe_url,
                confidence=a.confidence,
            )
            for a in entity.appearances
        ],
        occurrence_candidates=[
            OccurrenceCandidateOut(
                id=candidate.id,
                keyframe_ts=candidate.keyframe_ts,
                start_ts=candidate.start_ts,
                end_ts=candidate.end_ts,
                keyframe_url=candidate.keyframe_url,
                confidence=candidate.confidence,
                evidence=candidate.evidence_json or {},
                status=candidate.status,
            )
            for candidate in entity.occurrence_candidates
        ],
    )
