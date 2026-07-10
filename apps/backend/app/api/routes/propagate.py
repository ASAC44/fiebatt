from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.deps import get_runner, get_session
from app.models.entity import Entity, EntityAppearance
from app.models.project import Project
from app.models.propagation import PropagationJob, PropagationResult
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.propagate import (
    PropagateRequest,
    PropagateResponse,
    PropagationResultOut,
    PropagationStatus,
)
from app.workers import propagate_job

router = APIRouter(tags=["propagate"])


async def _deactivate_overlapping_generated_segments(
    db: AsyncSession,
    project_id: str,
    start_ts: float,
    end_ts: float,
) -> None:
    overlapping = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == project_id,
                Segment.active == True,  # noqa: E712
                Segment.source == "generated",
                Segment.start_ts < end_ts,
                Segment.end_ts > start_ts,
            )
        )
    ).scalars().all()
    for seg in overlapping:
        seg.active = False


@router.post("/propagate", response_model=PropagateResponse)
async def propagate(
    body: PropagateRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
    runner = Depends(get_runner),
):
    entity = (
        await db.execute(
            select(Entity)
            .where(Entity.id == body.entity_id)
            .options(selectinload(Entity.appearances))
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")

    proj = await db.get(Project, entity.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="entity not found")

    if not entity.appearances:
        raise HTTPException(
            status_code=422, detail="entity has no other appearances to propagate"
        )

    pjob = PropagationJob(
        project_id=proj.id,
        entity_id=entity.id,
        source_variant_url=body.source_variant_url,
        prompt=body.prompt,
        auto_apply=body.auto_apply,
        status="pending",
    )
    db.add(pjob)
    await db.flush()

    for app in entity.appearances:
        res = PropagationResult(
            propagation_job_id=pjob.id,
            appearance_id=app.id,
            status="pending",
        )
        db.add(res)

    await db.commit()
    await db.refresh(pjob)

    runner.submit(pjob.id, lambda: propagate_job.run(pjob.id))
    return PropagateResponse(propagation_job_id=pjob.id)


@router.get("/propagate/{propagation_job_id}", response_model=PropagationStatus)
async def get_propagation(
    propagation_job_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    pjob = await db.get(PropagationJob, propagation_job_id)
    if pjob is None:
        raise HTTPException(status_code=404, detail="propagation job not found")
    proj = await db.get(Project, pjob.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="propagation job not found")

    rows = (
        await db.execute(
            select(PropagationResult).where(
                PropagationResult.propagation_job_id == propagation_job_id
            )
        )
    ).scalars().all()

    return PropagationStatus(
        propagation_job_id=pjob.id,
        status=pjob.status,  # type: ignore[arg-type]
        error=pjob.error,
        results=[
            PropagationResultOut(
                id=r.id,
                appearance_id=r.appearance_id,
                segment_id=r.segment_id,
                variant_url=r.variant_url,
                status=r.status,  # type: ignore[arg-type]
                applied=r.applied,
            )
            for r in rows
        ],
    )


@router.post(
    "/propagate/{propagation_job_id}/apply/{result_id}",
    response_model=PropagationResultOut,
)
async def apply_propagation_result(
    propagation_job_id: str,
    result_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    pjob = await db.get(PropagationJob, propagation_job_id)
    if pjob is None:
        raise HTTPException(status_code=404, detail="propagation job not found")
    proj = await db.get(Project, pjob.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="propagation job not found")

    res = await db.get(PropagationResult, result_id)
    if res is None or res.propagation_job_id != propagation_job_id:
        raise HTTPException(status_code=404, detail="result not found")
    if res.status != "done" or not res.variant_url:
        raise HTTPException(status_code=422, detail="result not ready")
    if res.applied:
        return _out(res)

    appearance = await db.get(EntityAppearance, res.appearance_id)
    if appearance is None:
        raise HTTPException(status_code=404, detail="appearance missing")

    # lazy-render: just record the decision. export will do the actual
    # compositing from the segment rows when the user hits download.
    await _deactivate_overlapping_generated_segments(
        db,
        proj.id,
        appearance.start_ts,
        appearance.end_ts,
    )
    seg = Segment(
        project_id=proj.id,
        start_ts=appearance.start_ts,
        end_ts=appearance.end_ts,
        source="generated",
        url=res.variant_url,
        order_index=int(appearance.start_ts * 1000),
        active=True,
    )
    db.add(seg)
    await db.flush()
    res.segment_id = seg.id
    res.applied = True
    await db.commit()
    return _out(res)


def _out(r: PropagationResult) -> PropagationResultOut:
    return PropagationResultOut(
        id=r.id,
        appearance_id=r.appearance_id,
        segment_id=r.segment_id,
        variant_url=r.variant_url,
        status=r.status,  # type: ignore[arg-type]
        applied=r.applied,
    )
