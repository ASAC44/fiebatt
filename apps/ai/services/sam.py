"""SAM (Segment Anything) service.

Converts rough bounding boxes into precise segmentation masks.
This is what makes the bbox a real technical input, not UX theater.

Supports two backends:
  1. Modal (serverless GPU, SAM2 on T4) — set GPU_WORKER_URL to the modal endpoint
  2. Self-hosted GPU worker (any box with a GPU)

Both use the same HTTP interface: POST {url} with {image_b64, bbox} → {mask_b64}
"""

import httpx
import base64
import logging
from pathlib import Path

from ai.services.config import get_settings

log = logging.getLogger(__name__)


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
    settings = get_settings()
    gpu_url = settings.gpu_worker_url

    frame_bytes = Path(frame_path).read_bytes()
    frame_b64 = base64.b64encode(frame_bytes).decode()

    # modal web endpoints use the class method URL directly
    # self-hosted workers use {gpu_url}/sam/segment
    segment_url = (
        gpu_url if "modal.run" in gpu_url
        else f"{gpu_url}/sam/segment"
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

    return mask_path


async def is_available() -> bool:
    """Check if the GPU worker is reachable."""
    try:
        import os
        gpu_url = os.environ.get("GPU_WORKER_URL", "")
        if not gpu_url:
            gpu_url = get_settings().gpu_worker_url

        if "modal.run" in gpu_url:
            health_url = gpu_url.replace("segment", "health")
        else:
            health_url = f"{gpu_url}/health"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(health_url)
            return resp.status_code == 200
    except Exception:
        return False
