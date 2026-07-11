from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.services import color, ffmpeg, storage

router = APIRouter(tags=["color"])


class GradeAdjustments(BaseModel):
    brightness: float | None = Field(default=None, ge=-100, le=100)
    contrast: float | None = Field(default=None, ge=-100, le=100)
    saturation: float | None = Field(default=None, ge=-100, le=100)
    temperature: float | None = Field(default=None, ge=2000, le=10000)
    gamma: float | None = Field(default=None, ge=0.1, le=3.0)
    hue_shift: float | None = Field(default=None, ge=-180, le=180)


class GradeResponse(BaseModel):
    segment_id: str
    graded_url: str


class GradePreviewResponse(BaseModel):
    preview_frame_url: str


class MatchGradeRequest(BaseModel):
    source_segment_id: str
    reference_segment_id: str


class MatchGradeResponse(BaseModel):
    segment_id: str
    matched_url: str


@router.post("/segments/{segment_id}/grade", response_model=GradeResponse)
async def grade_segment(
    segment_id: str,
    body: GradeAdjustments,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    segment, _project = await _load_owned_segment(segment_id, session, db)
    input_path = await storage.path_from_url(segment.url)
    output_path, _ = storage.new_path("graded", "mp4")

    await color.apply_grade(
        input_path=input_path,
        output_path=output_path,
        adjustments=body.model_dump(exclude_none=True),
    )
    graded_url = await storage.publish(output_path)

    await _deactivate_overlapping_generated_segments(db, segment)

    graded_segment = Segment(
        project_id=segment.project_id,
        start_ts=segment.start_ts,
        end_ts=segment.end_ts,
        source="generated",
        url=graded_url,
        order_index=segment.order_index,
        active=True,
    )
    db.add(graded_segment)
    await db.commit()
    await db.refresh(graded_segment)

    return GradeResponse(segment_id=graded_segment.id, graded_url=graded_url)


@router.post("/segments/{segment_id}/grade/preview", response_model=GradePreviewResponse)
async def grade_segment_preview(
    segment_id: str,
    body: GradeAdjustments,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    segment, _project = await _load_owned_segment(segment_id, session, db)
    input_path = await storage.path_from_url(segment.url)
    probe_data = await ffmpeg.probe(input_path)
    frame_ts = max(probe_data.get("duration", 0.0) / 2.0, 0.0)

    frame_path, _ = storage.new_path("previews", "jpg")
    preview_path, _ = storage.new_path("previews", "jpg")

    await ffmpeg.extract_frame(input_path, frame_ts, frame_path)
    await color.apply_grade_to_frame(
        input_path=frame_path,
        output_path=preview_path,
        adjustments=body.model_dump(exclude_none=True),
    )
    preview_frame_url = await storage.publish(preview_path)

    return GradePreviewResponse(preview_frame_url=preview_frame_url)


@router.post("/grade/match", response_model=MatchGradeResponse)
async def match_grade(
    body: MatchGradeRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    source_segment, _ = await _load_owned_segment(body.source_segment_id, session, db)
    reference_segment, _ = await _load_owned_segment(body.reference_segment_id, session, db)

    source_path = await storage.path_from_url(source_segment.url)
    reference_path = await storage.path_from_url(reference_segment.url)
    output_path, _ = storage.new_path("graded", "mp4")

    with tempfile.TemporaryDirectory(prefix="iris-match-") as tmp_dir:
        reference_frame_path = Path(tmp_dir) / "reference.jpg"
        reference_probe = await ffmpeg.probe(reference_path)
        reference_ts = max(reference_probe.get("duration", 0.0) / 2.0, 0.0)
        await ffmpeg.extract_frame(reference_path, reference_ts, reference_frame_path)

        await color.match_color_histogram(
            source_path=source_path,
            reference_path=reference_frame_path,
            output_path=output_path,
        )

    matched_url = await storage.publish(output_path)

    await _deactivate_overlapping_generated_segments(db, source_segment)

    matched_segment = Segment(
        project_id=source_segment.project_id,
        start_ts=source_segment.start_ts,
        end_ts=source_segment.end_ts,
        source="generated",
        url=matched_url,
        order_index=source_segment.order_index,
        active=True,
    )
    db.add(matched_segment)
    await db.commit()
    await db.refresh(matched_segment)

    return MatchGradeResponse(segment_id=matched_segment.id, matched_url=matched_url)


async def _load_owned_segment(
    segment_id: str,
    session: SessionModel,
    db: AsyncSession,
) -> tuple[Segment, Project]:
    row = (
        await db.execute(
            select(Segment, Project)
            .join(Project, Project.id == Segment.project_id)
            .where(
                Segment.id == segment_id,
                Project.session_id == session.id,
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="segment not found")

    segment, project = row
    return segment, project


async def _deactivate_overlapping_generated_segments(
    db: AsyncSession,
    segment: Segment,
) -> None:
    overlapping = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == segment.project_id,
                Segment.active == True,  # noqa: E712
                Segment.source == "generated",
                Segment.start_ts < segment.end_ts,
                Segment.end_ts > segment.start_ts,
            )
        )
    ).scalars().all()
    for existing in overlapping:
        existing.active = False
