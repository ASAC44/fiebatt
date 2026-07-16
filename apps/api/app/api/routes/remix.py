from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.deps import get_session, get_runner
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.common import BBox
from app.workers import entity_job
from app.workers import generate_job as generate_worker

router = APIRouter(tags=["remix"])


MIN_SEG_LEN = 2.0
MAX_SEG_LEN = 15.0
MAX_BATCH_EDITS = 10


class RemixRequest(BaseModel):
    variant_id: str
    modifier_prompt: str
    preserve_composition: bool = True


class RemixResponse(BaseModel):
    job_id: str


class BatchGenerateEdit(BaseModel):
    project_id: str
    start_ts: float
    end_ts: float
    bbox: BBox
    prompt: str
    reference_frame_ts: float | None = None


class BatchGenerateRequest(BaseModel):
    edits: list[BatchGenerateEdit]


class BatchGenerateResponse(BaseModel):
    job_ids: list[str]


class BatchAcceptItem(BaseModel):
    job_id: str
    variant_index: int


class BatchAcceptRequest(BaseModel):
    accepts: list[BatchAcceptItem]


class BatchAcceptResponse(BaseModel):
    segment_ids: list[str]
    entity_job_ids: list[str | None]


def _validate_segment_length(start_ts: float, end_ts: float) -> None:
    length = end_ts - start_ts
    if length < MIN_SEG_LEN or length > MAX_SEG_LEN:
        raise HTTPException(
            status_code=422,
            detail=(
                f"segment length must be {MIN_SEG_LEN}-{MAX_SEG_LEN}s "
                f"(got {length:.2f}s)"
            ),
        )


def _validate_bbox_bounds(bbox: BBox) -> None:
    if bbox.x + bbox.w > 1.0001 or bbox.y + bbox.h > 1.0001:
        raise HTTPException(status_code=422, detail="bbox extends outside the frame")


def _is_tracked_bbox(bbox_json: dict[str, Any] | None) -> bool:
    bbox = bbox_json or {}
    bbox_w = float(bbox.get("w", 0.0)) if isinstance(bbox, dict) else 0.0
    bbox_h = float(bbox.get("h", 0.0)) if isinstance(bbox, dict) else 0.0
    return bool(bbox) and bbox_w < 0.98 and bbox_h < 0.98 and bbox_w * bbox_h > 0.0


async def _get_owned_project(
    db: AsyncSession,
    *,
    project_id: str,
    session_id: str,
) -> Project:
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session_id:
        raise HTTPException(status_code=404, detail="project not found")
    return proj


async def _accept_variant_for_job(
    *,
    db: AsyncSession,
    proj: Project,
    job: Job,
    variant: Variant,
) -> tuple[str, str | None]:
    if job.start_ts is None or job.end_ts is None:
        raise HTTPException(status_code=422, detail="job has no segment range")

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
    for segment in overlapping:
        segment.active = False

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
    await db.flush()

    entity_job_id: str | None = None
    if _is_tracked_bbox(job.bbox_json):
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
        await db.flush()
        entity_job_id = ent_job.id

    return seg.id, entity_job_id


@router.post("/remix", response_model=RemixResponse)
async def remix(
    body: RemixRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
    runner: Any = Depends(get_runner),
) -> RemixResponse:
    variant = (
        await db.execute(
            select(Variant)
            .where(Variant.id == body.variant_id)
            .options(selectinload(Variant.job))
        )
    ).scalar_one_or_none()
    if variant is None or variant.job is None:
        raise HTTPException(status_code=404, detail="variant not found")

    job = variant.job
    proj = await _get_owned_project(db, project_id=job.project_id, session_id=session.id)

    original_prompt = job.prompt or ""
    remix_prompt = f"{original_prompt} | REFINEMENT: {body.modifier_prompt}"
    payload = {
        "remix_source_variant_id": variant.id,
        "preserve_composition": body.preserve_composition,
    }

    remix_job = Job(
        project_id=proj.id,
        kind="generate",
        status="pending",
        start_ts=job.start_ts,
        end_ts=job.end_ts,
        bbox_json=job.bbox_json,
        prompt=remix_prompt,
        reference_frame_ts=job.reference_frame_ts,
        payload=payload,
    )
    db.add(remix_job)
    await db.commit()
    await db.refresh(remix_job)

    runner.submit(remix_job.id, lambda: generate_worker.run(remix_job.id))
    return RemixResponse(job_id=remix_job.id)


@router.post("/batch/generate", response_model=BatchGenerateResponse)
async def batch_generate(
    body: BatchGenerateRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
    runner: Any = Depends(get_runner),
) -> BatchGenerateResponse:
    if not body.edits:
        raise HTTPException(status_code=422, detail="edits must not be empty")
    if len(body.edits) > MAX_BATCH_EDITS:
        raise HTTPException(
            status_code=422,
            detail=f"at most {MAX_BATCH_EDITS} edits are allowed per batch",
        )

    jobs: list[Job] = []
    for edit in body.edits:
        proj = await _get_owned_project(
            db,
            project_id=edit.project_id,
            session_id=session.id,
        )
        _validate_segment_length(edit.start_ts, edit.end_ts)
        if edit.end_ts > proj.duration + 1e-3:
            raise HTTPException(status_code=422, detail="end_ts past project duration")
        _validate_bbox_bounds(edit.bbox)

        job = Job(
            project_id=proj.id,
            kind="generate",
            status="pending",
            start_ts=edit.start_ts,
            end_ts=edit.end_ts,
            bbox_json=edit.bbox.model_dump(),
            prompt=edit.prompt,
            reference_frame_ts=edit.reference_frame_ts,
        )
        db.add(job)
        jobs.append(job)

    await db.commit()

    job_ids: list[str] = []
    for job in jobs:
        await db.refresh(job)
        runner.submit(job.id, lambda job_id=job.id: generate_worker.run(job_id))
        job_ids.append(job.id)

    return BatchGenerateResponse(job_ids=job_ids)


@router.post("/batch/accept", response_model=BatchAcceptResponse)
async def batch_accept(
    body: BatchAcceptRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
    runner: Any = Depends(get_runner),
) -> BatchAcceptResponse:
    if not body.accepts:
        raise HTTPException(status_code=422, detail="accepts must not be empty")

    segment_ids: list[str] = []
    entity_job_ids: list[str | None] = []
    pending_entity_job_ids: list[str] = []

    for item in body.accepts:
        job = (
            await db.execute(
                select(Job)
                .where(Job.id == item.job_id)
                .options(selectinload(Job.variants))
            )
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        proj = await _get_owned_project(
            db,
            project_id=job.project_id,
            session_id=session.id,
        )

        variant = next((v for v in job.variants if v.index == item.variant_index), None)
        if variant is None or variant.status != "done" or not variant.url:
            raise HTTPException(status_code=422, detail="variant not ready")

        segment_id, entity_job_id = await _accept_variant_for_job(
            db=db,
            proj=proj,
            job=job,
            variant=variant,
        )
        segment_ids.append(segment_id)
        entity_job_ids.append(entity_job_id)
        if entity_job_id is not None:
            pending_entity_job_ids.append(entity_job_id)

    await db.commit()
    for entity_job_id in pending_entity_job_ids:
        runner.submit(entity_job_id, lambda job_id=entity_job_id: entity_job.run(job_id))
    return BatchAcceptResponse(segment_ids=segment_ids, entity_job_ids=entity_job_ids)
