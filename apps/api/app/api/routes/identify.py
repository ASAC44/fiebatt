"""Object identification route — POST /api/identify.

Extracts a frame from the project video at a given timestamp, crops the
bounding box region, and sends it to Gemini for entity identification.
Mask refinement is served separately by ``POST /api/mask`` so a slow entity
description cannot delay or suppress the SAM result.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.services import gemini
from app.services import ffmpeg as async_ffmpeg, storage
from app.config.settings import get_settings
from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.schemas.identify import IdentifyRequest, IdentifyResponse
from app.services.edit_source import materialize_edit_source, source_for_timeline_clip

log = logging.getLogger("fiebatt.identify")
router = APIRouter(tags=["identify"])


@router.post("/identify", response_model=IdentifyResponse)
async def identify(
    body: IdentifyRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    # ---- validate project ownership ----
    proj = await db.get(Project, body.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    try:
        edit_source = source_for_timeline_clip(proj, body.target_clip_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if body.frame_ts > edit_source.duration + 1e-3:
        raise HTTPException(status_code=422, detail="frame_ts past selected clip duration")

    # bbox sanity: x+w and y+h in [0,1]
    if body.bbox.x + body.bbox.w > 1.0001 or body.bbox.y + body.bbox.h > 1.0001:
        raise HTTPException(status_code=422, detail="bbox extends outside the frame")

    # ---- extract frame from video ----
    settings = get_settings()
    frames_dir = settings.storage_path / "identify_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    frame_path = str(frames_dir / f"{proj.id}_{body.frame_ts:.3f}.png")
    try:
        source_path = await materialize_edit_source(proj, edit_source)
        await async_ffmpeg.extract_frame(source_path, body.frame_ts, frame_path)
    except Exception as exc:
        log.exception("ffmpeg frame extraction failed for project %s at ts=%.3f", proj.id, body.frame_ts)
        raise HTTPException(status_code=500, detail=f"frame extraction failed: {exc}") from exc

    # ---- crop bbox region ----
    bbox_dict = body.bbox.model_dump()
    try:
        crop_path = await async_ffmpeg.crop_bbox_from_frame(frame_path, bbox_dict)
    except Exception as exc:
        log.exception("bbox crop failed for project %s", proj.id)
        raise HTTPException(status_code=500, detail=f"bbox crop failed: {exc}") from exc

    # ---- identify entity ----
    try:
        entity = await gemini.identify_entity(crop_path)
    except Exception as exc:
        log.exception("entity identification failed for project %s", proj.id)
        raise HTTPException(status_code=500, detail=f"entity identification failed: {exc}") from exc

    # ---- cleanup temp files ----
    for path in (frame_path, crop_path):
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

    # gemini sometimes returns attributes as a flat string instead of a dict
    raw_attrs = entity.get("attributes", {})
    if isinstance(raw_attrs, str):
        raw_attrs = {"description": raw_attrs}

    return IdentifyResponse(
        description=entity.get("description", ""),
        category=entity.get("category", ""),
        attributes=raw_attrs,
        mask=None,
    )
