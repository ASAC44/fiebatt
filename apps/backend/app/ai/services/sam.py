"""SAM (Segment Anything) service.

Converts rough bounding boxes into precise segmentation masks.
This is what makes the bbox a real technical input, not UX theater.

Supports two backends:
  1. Modal (serverless GPU, SAM2 on T4) — set VISION_WORKER_URL to the endpoint
  2. Self-hosted vision worker (any box with a supported accelerator)

Both use the same HTTP interface: POST {url} with {image_b64, bbox} → {mask_b64}
"""

import httpx
import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from app.ai.services.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaskResult:
    path: str
    score: float | None = None
    candidate_count: int | None = None


async def bbox_to_mask(
    frame_path: str,
    bbox: dict[str, float],
) -> str:
    """Send a frame + bbox to the GPU worker and get back a precise segmentation mask.

    Works with both Modal endpoints and self-hosted GPU workers — same API.

    Args:
        frame_path: Path to the full video frame
        bbox: Normalized bounding box {x, y, w, h} (0-1, top-left origin)

    Returns:
        Path to the generated mask image (PNG, white = foreground)
    """
    return (await bbox_to_mask_result(frame_path, bbox)).path


async def bbox_to_mask_result(
    frame_path: str,
    bbox: dict[str, float],
) -> MaskResult:
    """Return reusable mask metadata without breaking the legacy path contract."""
    settings = get_settings()
    worker_url = settings.vision_worker_url

    frame_bytes = Path(frame_path).read_bytes()
    frame_b64 = base64.b64encode(frame_bytes).decode()

    # modal web endpoints use the class method URL directly
    # self-hosted workers use {gpu_url}/sam/segment
    segment_url = (
        worker_url if "modal.run" in worker_url
        else f"{worker_url}/sam/segment"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            segment_url,
            json={
                "image_b64": frame_b64,
                "bbox": bbox,
            },
        )
        response.raise_for_status()
        data = response.json()

    # Save mask
    mask_bytes = base64.b64decode(data["mask_b64"])
    mask_path = str(Path(frame_path).with_suffix(".mask.png"))
    Path(mask_path).write_bytes(mask_bytes)

    return MaskResult(
        path=mask_path,
        score=float(data["score"]) if data.get("score") is not None else None,
        candidate_count=(
            int(data["candidate_count"])
            if data.get("candidate_count") is not None
            else None
        ),
    )


async def is_available() -> bool:
    """Check if the vision worker is reachable."""
    try:
        worker_url = get_settings().vision_worker_url

        if "modal.run" in worker_url:
            health_url = worker_url.replace("segment", "health")
        else:
            health_url = f"{worker_url}/health"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(health_url)
            return resp.status_code == 200
    except Exception:
        return False


def create_subject_reference(
    frame_path: str,
    mask_path: str,
    output_path: str | None = None,
) -> str:
    """Create an opaque, tightly cropped subject reference from a SAM mask.

    Video-edit providers do not consistently accept alpha channels.  A neutral
    background makes the selected entity unambiguous while keeping the output a
    regular RGB PNG that every configured provider accepts.
    """
    import cv2  # type: ignore[import-untyped]
    import numpy as np  # type: ignore[import-untyped]

    frame = cv2.imread(frame_path, cv2.IMREAD_COLOR)
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if frame is None:
        raise ValueError("failed to read SAM reference frame")
    if mask is None:
        raise ValueError("failed to read SAM mask")
    frame_h, frame_w = frame.shape[:2]
    if mask.shape[:2] != (frame_h, frame_w):
        mask = cv2.resize(mask, (frame_w, frame_h), interpolation=cv2.INTER_NEAREST)

    # SAM masks are binary in production, but threshold again so compressed or
    # hand-authored fixtures cannot introduce a translucent halo.
    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
    points = cv2.findNonZero(mask)
    if points is None:
        raise ValueError("SAM returned an empty subject mask")

    left, top, subject_w, subject_h = cv2.boundingRect(points)
    right = left + subject_w
    bottom = top + subject_h
    pad = max(8, round(max(subject_w, subject_h) * 0.08))
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(frame_w, right + pad)
    bottom = min(frame_h, bottom + pad)

    frame_crop = frame[top:bottom, left:right]
    mask_crop = mask[top:bottom, left:right]
    background = np.full_like(frame_crop, 238)
    reference = np.where(mask_crop[..., None] > 0, frame_crop, background)

    # Provider image inputs require useful spatial resolution. Upscale only;
    # never blur a large reference by resizing it down.
    ref_h, ref_w = reference.shape[:2]
    min_side = min(ref_w, ref_h)
    if min_side < 360:
        scale = 360 / max(1, min_side)
        reference = cv2.resize(
            reference,
            (round(ref_w * scale), round(ref_h * scale)),
            interpolation=cv2.INTER_LANCZOS4,
        )

    destination = Path(output_path) if output_path else Path(frame_path).with_suffix(".subject.png")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), reference):
        raise ValueError("failed to write isolated SAM subject reference")
    return str(destination)
