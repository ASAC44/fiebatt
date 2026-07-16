from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.settings import get_settings
from app.db.session import get_db
from app.deps import get_session
from app.models.entity import Entity, EntityAppearance
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.propagation import GlobalEditPlan
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.propagate import (
    GlobalEditPlanOut,
    GlobalEditPlanRequest,
    PlannedOccurrenceOut,
)


router = APIRouter(prefix="/global-edit-plans", tags=["global-edit-plans"])


async def _plan_response(
    db: AsyncSession,
    plan: GlobalEditPlan,
) -> GlobalEditPlanOut:
    appearances = (
        await db.execute(
            select(EntityAppearance)
            .where(EntityAppearance.id.in_(plan.occurrence_ids_json))
            .order_by(EntityAppearance.start_ts)
        )
    ).scalars().all()
    return GlobalEditPlanOut(
        plan_id=plan.id,
        project_id=plan.project_id,
        entity_id=plan.entity_id,
        reference_segment_id=plan.reference_segment_id,
        scope=plan.scope,
        prompt=plan.prompt,
        occurrences=[
            PlannedOccurrenceOut(
                appearance_id=appearance.id,
                start_ts=appearance.start_ts,
                end_ts=appearance.end_ts,
                confidence=appearance.confidence,
            )
            for appearance in appearances
        ],
        estimate=plan.estimate_json,
        status=plan.status,
    )


@router.post("", response_model=GlobalEditPlanOut, status_code=201)
async def create_global_edit_plan(
    body: GlobalEditPlanRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    if not get_settings().global_edit_planning:
        raise HTTPException(status_code=409, detail="global edit planning is disabled")
    entity = (
        await db.execute(
            select(Entity)
            .where(Entity.id == body.entity_id)
            .options(
                selectinload(Entity.appearances),
                selectinload(Entity.occurrence_tracks),
            )
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")
    project = await db.get(Project, entity.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="entity not found")
    segment = await db.get(Segment, body.reference_segment_id)
    if (
        segment is None
        or segment.project_id != project.id
        or segment.source != "generated"
        or not segment.active
        or not segment.variant_id
        or entity.source_segment_id != segment.id
    ):
        raise HTTPException(status_code=422, detail="accepted reference segment is invalid")
    variant = await db.get(Variant, segment.variant_id)
    source_job = await db.get(Job, variant.job_id) if variant is not None else None
    if variant is None or not variant.url or source_job is None or not source_job.prompt:
        raise HTTPException(status_code=422, detail="accepted reference variant is unavailable")
    if not entity.occurrence_tracks or not any(
        track.status == "confirmed" for track in entity.occurrence_tracks
    ):
        raise HTTPException(status_code=422, detail="entity has no confirmed occurrence tracks")

    appearance_by_id = {appearance.id: appearance for appearance in entity.appearances}
    if body.scope == "selected_occurrences":
        requested_ids = list(dict.fromkeys(body.occurrence_ids))
        if not requested_ids:
            raise HTTPException(status_code=422, detail="select at least one occurrence")
        if any(appearance_id not in appearance_by_id for appearance_id in requested_ids):
            raise HTTPException(status_code=422, detail="selected occurrence does not belong to entity")
        selected = [appearance_by_id[appearance_id] for appearance_id in requested_ids]
    else:
        selected = list(entity.appearances)
    if not selected:
        raise HTTPException(status_code=422, detail="entity has no confirmed occurrences")

    total_seconds = sum(
        max(0.0, appearance.end_ts - appearance.start_ts) for appearance in selected
    )
    estimate = {
        "occurrence_count": len(selected),
        "expected_generation_calls": len(selected),
        "expected_generated_seconds": round(total_seconds, 3),
        "mean_track_confidence": round(
            sum(appearance.confidence for appearance in selected) / len(selected), 3
        ),
        "reference_accepted": True,
    }
    plan = GlobalEditPlan(
        project_id=project.id,
        entity_id=entity.id,
        reference_segment_id=segment.id,
        reference_variant_id=variant.id,
        scope=body.scope,
        occurrence_ids_json=[appearance.id for appearance in selected],
        estimate_json=estimate,
        prompt=source_job.prompt,
        source_revision=project.video_url,
        status="ready",
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return await _plan_response(db, plan)


@router.get("/{plan_id}", response_model=GlobalEditPlanOut)
async def get_global_edit_plan(
    plan_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(GlobalEditPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="global edit plan not found")
    project = await db.get(Project, plan.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="global edit plan not found")
    return await _plan_response(db, plan)
