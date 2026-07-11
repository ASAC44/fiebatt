"""Object identification route — POST /api/identify.

Extracts a frame from the project video at a given timestamp, crops the
bounding box region, and sends it to Gemini for entity identification.
Optionally refines the bbox into a precise SAM mask when the GPU worker
is available.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ai.services import gemini
from ai.services.sam import bbox_to_mask, is_available as sam_available
from app.services import ffmpeg as async_ffmpeg
from app.config.settings import get_settings
from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.schemas.identify import IdentifyRequest, IdentifyResponse, MaskOut

log = logging.getLogger("iris.identify")
router = APIRouter(tags=["identify"])


def _mask_png_to_contour(mask_path: str) -> list[list[float]] | None:
    """Extract the largest contour from a SAM mask PNG.

    Uses OpenCV if available; returns ``None`` when cv2 is missing or no
    contour is found so the caller can gracefully degrade.
    """
    try:
        import cv2  # type: ignore[import-untyped]
        import numpy as np  # type: ignore[import-untyped]

        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        if mask is None:
            return None

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Pick the largest contour by area
        largest = max(contours, key=cv2.contourArea)

        h, w = mask.shape[:2]
        # Normalize to 0-1 and flatten to [[x, y], ...]
        return [
            [round(float(pt[0][0]) / w, 4), round(float(pt[0][1]) / h, 4)]
            for pt in largest
        ]
    except ImportError:
        log.debug("cv2 not available — skipping mask contour extraction")
        return None
    except Exception:
        log.exception("failed to extract contour from mask")
        return None


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

    if body.frame_ts > proj.duration + 1e-3:
        raise HTTPException(status_code=422, detail="frame_ts past project duration")

    # bbox sanity: x+w and y+h in [0,1]
    if body.bbox.x + body.bbox.w > 1.0001 or body.bbox.y + body.bbox.h > 1.0001:
        raise HTTPException(status_code=422, detail="bbox extends outside the frame")

    # ---- extract frame from video ----
    settings = get_settings()
    frames_dir = settings.storage_path / "identify_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    frame_path = str(frames_dir / f"{proj.id}_{body.frame_ts:.3f}.png")
    try:
        await async_ffmpeg.extract_frame(proj.video_path, body.frame_ts, frame_path)
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

    # ---- identify entity + SAM mask concurrently ----
    import asyncio

    async def _identify() -> dict:
        return await gemini.identify_entity(crop_path)

    async def _sam_mask() -> MaskOut | None:
        try:
            if not await sam_available():
                return None
            mask_path = await bbox_to_mask(frame_path, bbox_dict)
            contour = _mask_png_to_contour(mask_path)
            return MaskOut(contour=contour) if contour else None
        except Exception:
            log.warning("SAM mask generation failed — continuing without mask", exc_info=True)
            return None

    try:
        entity, mask_out = await asyncio.gather(_identify(), _sam_mask())
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
        mask=mask_out,
    )
