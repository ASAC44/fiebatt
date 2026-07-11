from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.models.timeline_snapshot import TimelineSnapshot

router = APIRouter(tags=["timeline"])


class SegmentSplitRequest(BaseModel):
    segment_id: str
    split_ts: float


class SegmentTrimRequest(BaseModel):
    segment_id: str
    new_start_ts: float
    new_end_ts: float


class SegmentDeleteRequest(BaseModel):
    segment_id: str


class TimelineReorderRequest(BaseModel):
    segment_ids: list[str]
    order: list[int]


class TimelineRevertRequest(BaseModel):
    snapshot_id: str


class SegmentResponse(BaseModel):
    id: str
    project_id: str
    start_ts: float
    end_ts: float
    source: str
    url: str
    variant_id: str | None
    order_index: int
    active: bool


class SegmentDeleteResponse(BaseModel):
    deleted: bool
    segment_id: str


class TimelineSnapshotResponse(BaseModel):
    snapshot_id: str
    created_at: datetime
    segment_count: int


class TimelineRevertResponse(BaseModel):
    reverted: bool
    segment_count: int


async def _get_owned_project(
    project_id: str,
    session: SessionModel,
    db: AsyncSession,
) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")
    return project


async def _get_project_segment(
    project_id: str,
    segment_id: str,
    db: AsyncSession,
) -> Segment:
    segment = await db.get(Segment, segment_id)
    if segment is None or segment.project_id != project_id:
        raise HTTPException(status_code=404, detail="segment not found")
    return segment


def _serialize_segment(segment: Segment) -> SegmentResponse:
    return SegmentResponse(
        id=segment.id,
        project_id=segment.project_id,
        start_ts=segment.start_ts,
        end_ts=segment.end_ts,
        source=segment.source,
        url=segment.url,
        variant_id=segment.variant_id,
        order_index=segment.order_index,
        active=segment.active,
    )


def _snapshot_payload(segment: Segment) -> dict[str, Any]:
    return {
        "project_id": segment.project_id,
        "start_ts": segment.start_ts,
        "end_ts": segment.end_ts,
        "source": segment.source,
        "url": segment.url,
        "variant_id": segment.variant_id,
        "order_index": segment.order_index,
        "active": segment.active,
    }


@router.post("/timeline/{project_id}/split", response_model=list[SegmentResponse])
async def split_timeline_segment(
    project_id: str,
    body: SegmentSplitRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> list[SegmentResponse]:
    await _get_owned_project(project_id, session, db)
    segment = await _get_project_segment(project_id, body.segment_id, db)

    if not (segment.start_ts < body.split_ts < segment.end_ts):
        raise HTTPException(
            status_code=422,
            detail="split_ts must be within the segment bounds",
        )

    left = Segment(
        project_id=segment.project_id,
        start_ts=segment.start_ts,
        end_ts=body.split_ts,
        source=segment.source,
        url=segment.url,
        variant_id=segment.variant_id,
        order_index=segment.order_index,
        active=True,
    )
    right = Segment(
        project_id=segment.project_id,
        start_ts=body.split_ts,
        end_ts=segment.end_ts,
        source=segment.source,
        url=segment.url,
        variant_id=segment.variant_id,
        order_index=segment.order_index + 1,
        active=True,
    )
    segment.active = False

    db.add_all([left, right])
    await db.commit()
    await db.refresh(left)
    await db.refresh(right)

    return [_serialize_segment(left), _serialize_segment(right)]


@router.post("/timeline/{project_id}/trim", response_model=SegmentResponse)
async def trim_timeline_segment(
    project_id: str,
    body: SegmentTrimRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> SegmentResponse:
    await _get_owned_project(project_id, session, db)
    segment = await _get_project_segment(project_id, body.segment_id, db)

    if body.new_start_ts >= body.new_end_ts:
        raise HTTPException(status_code=422, detail="invalid segment range")
    if (
        body.new_start_ts < segment.start_ts
        or body.new_end_ts > segment.end_ts
    ):
        raise HTTPException(
            status_code=422,
            detail="trim range must stay within the original segment bounds",
        )

    segment.start_ts = body.new_start_ts
    segment.end_ts = body.new_end_ts
    await db.commit()
    await db.refresh(segment)

    return _serialize_segment(segment)


@router.post("/timeline/{project_id}/delete", response_model=SegmentDeleteResponse)
async def delete_timeline_segment(
    project_id: str,
    body: SegmentDeleteRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> SegmentDeleteResponse:
    await _get_owned_project(project_id, session, db)
    segment = await _get_project_segment(project_id, body.segment_id, db)

    segment.active = False
    await db.commit()

    return SegmentDeleteResponse(deleted=True, segment_id=segment.id)


@router.post("/timeline/{project_id}/reorder", response_model=list[SegmentResponse])
async def reorder_timeline_segments(
    project_id: str,
    body: TimelineReorderRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> list[SegmentResponse]:
    await _get_owned_project(project_id, session, db)

    if len(body.segment_ids) != len(body.order):
        raise HTTPException(status_code=422, detail="segment_ids and order must match")
    if len(set(body.segment_ids)) != len(body.segment_ids):
        raise HTTPException(status_code=422, detail="segment_ids must be unique")

    segments = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == project_id,
                Segment.id.in_(body.segment_ids),
            )
        )
    ).scalars().all()

    if len(segments) != len(body.segment_ids):
        raise HTTPException(status_code=404, detail="one or more segments not found")

    order_by_segment_id = dict(zip(body.segment_ids, body.order, strict=True))
    for segment in segments:
        segment.order_index = order_by_segment_id[segment.id]

    await db.commit()

    updated_segments = sorted(segments, key=lambda segment: segment.order_index)
    return [_serialize_segment(segment) for segment in updated_segments]


@router.post("/timeline/{project_id}/snapshot", response_model=TimelineSnapshotResponse)
async def snapshot_timeline(
    project_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> TimelineSnapshotResponse:
    await _get_owned_project(project_id, session, db)

    active_segments = (
        await db.execute(
            select(Segment)
            .where(Segment.project_id == project_id, Segment.active == True)  # noqa: E712
            .order_by(Segment.order_index, Segment.start_ts)
        )
    ).scalars().all()

    snapshot = TimelineSnapshot(
        project_id=project_id,
        segments_json=[_snapshot_payload(segment) for segment in active_segments],
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)

    return TimelineSnapshotResponse(
        snapshot_id=snapshot.id,
        created_at=snapshot.created_at,
        segment_count=len(active_segments),
    )


@router.post("/timeline/{project_id}/revert", response_model=TimelineRevertResponse)
async def revert_timeline(
    project_id: str,
    body: TimelineRevertRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> TimelineRevertResponse:
    await _get_owned_project(project_id, session, db)

    snapshot = await db.get(TimelineSnapshot, body.snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise HTTPException(status_code=404, detail="snapshot not found")

    active_segments = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == project_id,
                Segment.active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    for segment in active_segments:
        segment.active = False

    restored_segments = [
        Segment(
            project_id=project_id,
            start_ts=float(segment_data["start_ts"]),
            end_ts=float(segment_data["end_ts"]),
            source=str(segment_data["source"]),
            url=str(segment_data["url"]),
            variant_id=(
                str(segment_data["variant_id"])
                if segment_data.get("variant_id") is not None
                else None
            ),
            order_index=int(segment_data.get("order_index", 0)),
            active=bool(segment_data.get("active", True)),
        )
        for segment_data in snapshot.segments_json
    ]
    db.add_all(restored_segments)
    await db.commit()

    return TimelineRevertResponse(
        reverted=True,
        segment_count=len(restored_segments),
    )
