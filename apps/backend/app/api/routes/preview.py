from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.schemas.preview import (
    PreviewFrame,
    PreviewFrameResponse,
    PreviewRangeResponse,
    PreviewStripResponse,
)
from app.services import ffmpeg, storage
from app.services.timeline_builder import TimelineItem, build_timeline

router = APIRouter(tags=["preview"])


PREVIEW_SCALE_VF = "scale=640:-2"


def _validate_bounds(start: float, end: float, duration: float) -> None:
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be greater than start")
    if start < 0:
        raise HTTPException(status_code=422, detail="start must be >= 0")
    if end > duration + 1e-3:
        raise HTTPException(status_code=422, detail="end past project duration")


def _validate_timestamp(ts: float, duration: float) -> None:
    if ts < 0:
        raise HTTPException(status_code=422, detail="ts must be >= 0")
    if ts > duration + 1e-3:
        raise HTTPException(status_code=422, detail="ts past project duration")


def _find_timeline_item(items: list[TimelineItem], ts: float) -> TimelineItem:
    for item in items:
        if item.start_ts - 1e-3 <= ts < item.end_ts - 1e-3:
            return item
    if items and abs(ts - items[-1].end_ts) <= 1e-3:
        return items[-1]
    raise HTTPException(status_code=404, detail="timeline item not found")


async def _resolve_source_path(
    proj: Project,
    item: TimelineItem,
) -> Path:
    if item.source == "generated":
        return await storage.path_from_url(item.url)

    src = Path(proj.video_path)
    if src.exists():
        return src
    return await storage.path_from_url(proj.video_url)


async def _extract_preview_frame(
    proj: Project,
    items: list[TimelineItem],
    ts: float,
) -> PreviewFrame:
    item = _find_timeline_item(items, ts)
    src = await _resolve_source_path(proj, item)
    if item.source == "generated":
        frame_ts = max(0.0, ts - item.start_ts)
        frame_ts = min(frame_ts, max(0.0, item.duration - 1e-3))
    else:
        frame_ts = min(ts, max(0.0, proj.duration - 1e-3))
    frame_path, _ = storage.new_path("previews", "jpg")
    await ffmpeg.extract_frame(src, frame_ts, frame_path)
    frame_url = await storage.publish(frame_path, content_type="image/jpeg")
    return PreviewFrame(ts=ts, url=frame_url)


async def _extract_preview_range(
    proj: Project,
    items: list[TimelineItem],
    start: float,
    end: float,
) -> str:
    part_paths: list[Path] = []

    for item in items:
        overlap_start = max(start, item.start_ts)
        overlap_end = min(end, item.end_ts)
        if overlap_end <= overlap_start + 1e-3:
            continue

        src = await _resolve_source_path(proj, item)
        clip_start = max(0.0, overlap_start - item.start_ts) if item.source == "generated" else overlap_start
        clip_end = clip_start + (overlap_end - overlap_start)
        part_path, _ = storage.new_path("previews", "mp4")

        await ffmpeg.extract_clip(
            src,
            clip_start,
            clip_end,
            part_path,
            vf=PREVIEW_SCALE_VF,
            with_audio=False,
        )
        part_paths.append(part_path)

    if not part_paths:
        raise HTTPException(status_code=404, detail="preview range not found")

    out_path, _ = storage.new_path("previews", "mp4")
    if len(part_paths) == 1:
        part_paths[0].replace(out_path)
    else:
        await ffmpeg.concat_mp4s(part_paths, out_path)
        for part_path in part_paths:
            part_path.unlink(missing_ok=True)

    return await storage.publish(out_path, content_type="video/mp4")


@router.get("/preview/{project_id}/frame", response_model=PreviewFrameResponse)
async def preview_frame(
    project_id: str,
    ts: float = Query(..., ge=0.0),
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    _validate_timestamp(ts, proj.duration)
    items = await build_timeline(db, proj)
    frame = await _extract_preview_frame(proj, items, ts)
    return PreviewFrameResponse(ts=frame.ts, url=frame.url)


@router.get("/preview/{project_id}/strip", response_model=PreviewStripResponse)
async def preview_strip(
    project_id: str,
    start: float = Query(..., ge=0.0),
    end: float = Query(..., gt=0.0),
    fps: float = Query(1.0, gt=0.0),
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    _validate_bounds(start, end, proj.duration)
    items = await build_timeline(db, proj)

    step = 1.0 / fps
    frame_timestamps: list[float] = []
    ts = start
    while ts < end - 1e-6:
        frame_timestamps.append(round(ts, 6))
        ts += step
    if not frame_timestamps or frame_timestamps[-1] < end - 1e-6:
        frame_timestamps.append(round(end, 6))

    frames = [
        await _extract_preview_frame(proj, items, frame_ts)
        for frame_ts in frame_timestamps
    ]
    return PreviewStripResponse(frames=frames)


@router.get("/preview/{project_id}/range", response_model=PreviewRangeResponse)
async def preview_range(
    project_id: str,
    start: float = Query(..., ge=0.0),
    end: float = Query(..., gt=0.0),
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    _validate_bounds(start, end, proj.duration)
    items = await build_timeline(db, proj)
    preview_url = await _extract_preview_range(proj, items, start, end)
    return PreviewRangeResponse(preview_url=preview_url, duration=end - start)
