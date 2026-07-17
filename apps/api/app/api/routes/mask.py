import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import services as ai
from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.selection import SelectionArtifact
from app.models.session import Session as SessionModel
from app.schemas.mask import MaskRequest, MaskResponse
from app.services import ffmpeg, storage
from app.services.edit_source import materialize_edit_source, source_for_timeline_clip

log = logging.getLogger("fiebatt.mask")

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

    try:
        edit_source = source_for_timeline_clip(proj, body.target_clip_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if body.frame_ts > edit_source.duration + 1e-3:
        raise HTTPException(status_code=422, detail="frame_ts past selected clip duration")

    # bbox sanity: x+w and y+h in [0,1]
    if body.bbox.x + body.bbox.w > 1.0001 or body.bbox.y + body.bbox.h > 1.0001:
        raise HTTPException(status_code=422, detail="bbox extends outside the frame")

    # Railway scratch storage is ephemeral. Restore the durable upload first.
    frame_path, _ = storage.new_path("keyframes", "jpg")
    try:
        source_path = await materialize_edit_source(proj, edit_source)
        await ffmpeg.extract_frame(source_path, body.frame_ts, frame_path)
    except Exception as e:
        log.exception("frame extraction failed")
        raise HTTPException(status_code=500, detail=f"frame extraction failed: {e}")

    mask_path: Path
    mask_score: float | None
    try:
        mask_result = await ai.sam.bbox_to_mask_result(
            frame_path=str(frame_path),
            bbox=body.bbox.model_dump(),
        )
        mask_path = Path(mask_result.path)
        mask_score = mask_result.score
    except Exception as e:
        # Planning can safely use a bbox mask. Never deadlock editing because
        # an optional segmentation worker is unavailable.
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, OSError, httpx.HTTPStatusError)):
            log.warning("SAM unavailable; using bbox selection: %s", e)
        else:
            log.exception("SAM failed; using bbox selection")
        mask_path = _bbox_fallback_mask(frame_path, body.bbox.model_dump())
        mask_score = None

    # Convert mask image to contour points normalized to [0, 1]
    contours = _mask_to_contours(str(mask_path))

    mask_url = await storage.publish(mask_path, content_type="image/png")
    subject_reference_url: str | None = None
    try:
        subject_reference_path = ai.sam.create_subject_reference(
            str(frame_path), str(mask_path)
        )
        subject_reference_url = await storage.publish(
            Path(subject_reference_path), content_type="image/png"
        )
    except Exception:
        log.exception("subject reference creation failed; keeping mask artifact")

    artifact = SelectionArtifact(
        project_id=proj.id,
        frame_ts=body.frame_ts,
        bbox_json=body.bbox.model_dump(),
        contours_json=contours,
        mask_url=mask_url,
        subject_reference_url=subject_reference_url,
        sam_score=mask_score,
        source_revision=edit_source.url,
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)

    # Clean up temporary mask file
    try:
        mask_path.unlink(missing_ok=True)
    except Exception:
        pass

    return MaskResponse(
        contour=contours[0] if contours else [],
        contours=contours,
        selection_id=artifact.id,
        mask_url=mask_url,
        subject_reference_url=subject_reference_url,
        score=mask_score,
    )


def _bbox_fallback_mask(frame_path: Path, bbox: dict[str, float]) -> Path:
    """Create a deterministic rectangular mask when SAM is unavailable."""
    import cv2  # type: ignore[import-untyped]
    import numpy as np

    frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("failed to read selection frame")
    height, width = frame.shape[:2]
    left = max(0, min(width - 1, round(bbox["x"] * width)))
    top = max(0, min(height - 1, round(bbox["y"] * height)))
    right = max(left + 1, min(width, round((bbox["x"] + bbox["w"]) * width)))
    bottom = max(top + 1, min(height, round((bbox["y"] + bbox["h"]) * height)))
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[top:bottom, left:right] = 255
    path, _ = storage.new_path("masks", "png")
    if not cv2.imwrite(str(path), mask):
        raise ValueError("failed to write fallback selection mask")
    return path


def _mask_to_contours(mask_path: str) -> list[list[list[float]]]:
    """Return meaningful disconnected SAM components as normalized contours."""
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

    ordered = sorted(contours, key=cv2.contourArea, reverse=True)
    largest_area = cv2.contourArea(ordered[0])
    minimum_area = max(12.0, largest_area * 0.002)
    result: list[list[list[float]]] = []
    for component in ordered:
        if cv2.contourArea(component) < minimum_area:
            continue
        # A smaller epsilon retains hands, feet, and clothing edges while still
        # keeping the browser payload compact.
        epsilon = 0.0025 * cv2.arcLength(component, True)
        approx = cv2.approxPolyDP(component, epsilon, True)
        if len(approx) < 3:
            continue
        result.append([
            [round(float(pt[0][0]) / w, 4), round(float(pt[0][1]) / h, 4)]
            for pt in approx
        ])
    return result


def _mask_to_contour(mask_path: str) -> list[list[float]]:
    """Backward-compatible largest-contour helper."""
    contours = _mask_to_contours(mask_path)
    return contours[0] if contours else []
