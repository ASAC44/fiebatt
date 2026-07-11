import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ai import services as ai
from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.schemas.mask import MaskRequest, MaskResponse
from app.services import ffmpeg, storage

log = logging.getLogger("iris.mask")

router = APIRouter(tags=["mask"])


@router.post("/mask", response_model=MaskResponse)
async def mask(
    body: MaskRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, body.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    if body.frame_ts > proj.duration + 1e-3:
        raise HTTPException(status_code=422, detail="frame_ts past project duration")

    # bbox sanity: x+w and y+h in [0,1]
    if body.bbox.x + body.bbox.w > 1.0001 or body.bbox.y + body.bbox.h > 1.0001:
        raise HTTPException(status_code=422, detail="bbox extends outside the frame")

    # Extract the frame from the video at the requested timestamp
    frame_path, _ = storage.new_path("keyframes", "jpg")
    try:
        await ffmpeg.extract_frame(proj.video_path, body.frame_ts, frame_path)
    except Exception as e:
        log.exception("frame extraction failed")
        raise HTTPException(status_code=500, detail=f"frame extraction failed: {e}")

    # Call SAM to get a segmentation mask
    try:
        mask_path = await ai.sam.bbox_to_mask(
            frame_path=str(frame_path),
            bbox=body.bbox.model_dump(),
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
        log.warning("GPU worker unavailable: %s", e)
        raise HTTPException(status_code=503, detail="GPU worker unavailable")
    except httpx.HTTPStatusError as e:
        log.exception("SAM segmentation failed")
        raise HTTPException(status_code=502, detail=f"SAM segmentation failed: {e}")
    except Exception as e:
        log.exception("SAM segmentation failed")
        raise HTTPException(status_code=500, detail=f"SAM segmentation failed: {e}")

    # Convert mask image to contour points normalized to [0, 1]
    contour = _mask_to_contour(mask_path)

    # Clean up temporary mask file
    try:
        Path(mask_path).unlink(missing_ok=True)
    except Exception:
        pass

    return MaskResponse(contour=contour)


def _mask_to_contour(mask_path: str) -> list[list[float]]:
    """Read a binary mask PNG and return the largest contour as normalized [x, y] points."""
    import cv2  # type: ignore[import-untyped]

    img = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise HTTPException(status_code=500, detail="failed to read mask image")

    h, w = img.shape[:2]

    # Threshold to binary (mask is white-on-black)
    _, binary = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    # Take the largest contour by area
    largest = max(contours, key=cv2.contourArea)

    # Simplify the contour to reduce point count
    epsilon = 0.005 * cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, epsilon, True)

    # Normalize to [0, 1] and return as [[x, y], ...]
    return [
        [round(float(pt[0][0]) / w, 4), round(float(pt[0][1]) / h, 4)]
        for pt in approx
    ]
