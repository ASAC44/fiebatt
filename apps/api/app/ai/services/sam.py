"""SAM (Segment Anything) service.

Converts rough bounding boxes into precise segmentation masks.
This is what makes the bbox a real technical input, not UX theater.

Supports two backends:
  1. Modal (serverless GPU, SAM2 on T4) — set VISION_WORKER_URL to the endpoint
  2. Self-hosted vision worker (any box with a supported accelerator)

Both use the same HTTP interface: POST {url} with {image_b64, bbox} → {mask_b64}
"""

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ai.services.config import get_settings

log = logging.getLogger(__name__)


def _segmentation_worker_url() -> str:
    settings = get_settings()
    return settings.sam_segmentation_url.strip() or settings.vision_worker_url


def _is_huggingface_space(worker_url: str) -> bool:
    """Return whether a worker URL should be called through Gradio."""
    parsed = urlparse(worker_url)
    hostname = (parsed.hostname or "").lower()
    return hostname.endswith(".hf.space") or (
        hostname in {"huggingface.co", "www.huggingface.co"}
        and parsed.path.startswith("/spaces/")
    )


def _call_huggingface_space(worker_url: str, payload: dict) -> dict:
    """Call the named ZeroGPU Gradio endpoint from a worker thread."""
    from gradio_client import Client

    token = os.getenv("HF_TOKEN") or None
    client = Client(
        worker_url.rstrip("/"),
        token=token,
        analytics_enabled=False,
    )
    result = client.predict(payload, api_name="/segment")
    if not isinstance(result, dict):
        raise TypeError("Hugging Face SAM endpoint returned a non-object response")
    return result


@dataclass(frozen=True, slots=True)
class MaskResult:
    path: str
    score: float | None = None
    candidate_count: int | None = None


class UnusableMaskError(ValueError):
    """SAM returned pixels, but they do not plausibly match the prompt box."""


@dataclass(frozen=True, slots=True)
class TrackResult:
    tracker: str
    frames: list[dict[str, Any]]
    processed_start_index: int
    processed_end_index: int
    cancelled: bool = False
    warning: str | None = None


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
    return (await bbox_to_validated_mask_result(frame_path, bbox)).path


def _clean_mask_for_bbox(
    frame_path: str,
    mask_path: str,
    bbox: dict[str, float],
    *,
    confidence: float | None = None,
) -> dict[str, float | int | bool]:
    """Keep the prompted component and reject obviously unrelated SAM output.

    SAM's predicted IoU score measures mask shape quality, not whether the mask
    represents the object the user meant. A successful worker response can
    therefore contain a tiny prop, background patch, or disconnected debris.
    This conservative geometry gate never invents a semantic choice: it keeps
    the component with the strongest support inside the user's box, or makes
    the caller fall back to the honest rectangle/crop.
    """
    import cv2  # type: ignore[import-untyped]
    import numpy as np  # type: ignore[import-untyped]

    frame = cv2.imread(frame_path, cv2.IMREAD_COLOR)
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if frame is None:
        raise UnusableMaskError("selection frame could not be read")
    if mask is None:
        raise UnusableMaskError("SAM mask could not be read")

    height, width = frame.shape[:2]
    if mask.shape != (height, width):
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    binary = (mask > 127).astype(np.uint8)

    left = max(0, min(width - 1, round(float(bbox["x"]) * width)))
    top = max(0, min(height - 1, round(float(bbox["y"]) * height)))
    right = max(
        left + 1,
        min(width, round((float(bbox["x"]) + float(bbox["w"])) * width)),
    )
    bottom = max(
        top + 1,
        min(height, round((float(bbox["y"]) + float(bbox["h"])) * height)),
    )
    bbox_area = max(1, (right - left) * (bottom - top))

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary,
        connectivity=8,
    )
    candidates: list[tuple[int, float, int, int]] = []
    for label in range(1, component_count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area <= 0:
            continue
        intersection = int(np.count_nonzero(labels[top:bottom, left:right] == label))
        if intersection <= 0:
            continue
        inside_fraction = intersection / area
        candidates.append((intersection, inside_fraction, area, label))

    if not candidates:
        raise UnusableMaskError("SAM mask does not overlap the selected box")
    intersection, inside_fraction, area, selected_label = max(candidates)
    bbox_coverage = intersection / bbox_area
    area_ratio = area / bbox_area
    if confidence is not None and confidence < 0.35:
        raise UnusableMaskError(f"SAM confidence too low ({confidence:.3f})")
    # A loose box is safer than pretending a tiny speck is the selected
    # subject. Production SAM failures have returned dozens of fragments with
    # the largest covering only ~3% of the prompt box.
    if bbox_coverage < 0.08:
        raise UnusableMaskError(
            f"SAM mask covers too little of selection ({bbox_coverage:.3f})"
        )
    center_x = min(width - 1, max(0, (left + right) // 2))
    center_y = min(height - 1, max(0, (top + bottom) // 2))
    contains_box_center = bool(labels[center_y, center_x] == selected_label)
    if not contains_box_center and bbox_coverage < 0.15:
        raise UnusableMaskError(
            "SAM mask is both off-center and too small for the selected box"
        )
    if inside_fraction < 0.08:
        raise UnusableMaskError(
            f"SAM mask mostly escapes selection ({inside_fraction:.3f} inside)"
        )
    if area_ratio > 8.0:
        raise UnusableMaskError(
            f"SAM mask is implausibly larger than selection ({area_ratio:.2f}x)"
        )

    cleaned = np.where(labels == selected_label, 255, 0).astype(np.uint8)
    if not cv2.imwrite(mask_path, cleaned):
        raise UnusableMaskError("cleaned SAM mask could not be written")

    metrics: dict[str, float | int | bool] = {
        "components_returned": max(0, component_count - 1),
        "components_removed": max(0, component_count - 2),
        "bbox_coverage": round(bbox_coverage, 4),
        "inside_fraction": round(inside_fraction, 4),
        "mask_to_bbox_area": round(area_ratio, 4),
        "contains_box_center": contains_box_center,
    }
    log.info("SAM mask accepted after geometry gate: %s", metrics)
    return metrics


async def bbox_to_validated_mask_result(
    frame_path: str,
    bbox: dict[str, float],
) -> MaskResult:
    """Return a cleaned, plausible SAM mask or raise for caller fallback."""
    result = await bbox_to_mask_result(frame_path, bbox)
    _clean_mask_for_bbox(
        frame_path,
        result.path,
        bbox,
        confidence=result.score,
    )
    return result


async def bbox_to_mask_result(
    frame_path: str,
    bbox: dict[str, float],
) -> MaskResult:
    """Return reusable mask metadata without breaking the legacy path contract."""
    worker_url = _segmentation_worker_url()

    frame_bytes = Path(frame_path).read_bytes()
    frame_b64 = base64.b64encode(frame_bytes).decode()

    payload = {"image_b64": frame_b64, "bbox": bbox}
    if _is_huggingface_space(worker_url):
        # gradio_client is blocking; keep it off FastAPI's event loop. The
        # longer timeout includes a possible ZeroGPU queue/cold start.
        data = await asyncio.wait_for(
            asyncio.to_thread(_call_huggingface_space, worker_url, payload),
            timeout=180.0,
        )
    else:
        # Modal web endpoints use the class method URL directly. Self-hosted
        # workers use {worker_url}/sam/segment.
        segment_url = (
            worker_url if "modal.run" in worker_url
            else f"{worker_url.rstrip('/')}/sam/segment"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(segment_url, json=payload)
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
        worker_url = _segmentation_worker_url()

        if _is_huggingface_space(worker_url):
            health_url = worker_url.rstrip("/")
        elif "modal.run" in worker_url:
            health_url = worker_url.replace("segment", "health")
        else:
            health_url = f"{worker_url.rstrip('/')}/health"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(health_url)
            return resp.status_code == 200
    except Exception:
        return False


async def video_tracking_available() -> bool:
    """Check for the full worker used by SAM2 video tracking.

    The lightweight Hugging Face Space only exposes still-image segmentation.
    Treating it as a video worker adds a slow, guaranteed-failing request to
    every edit.
    """
    try:
        worker_url = get_settings().vision_worker_url.strip()
        if not worker_url or _is_huggingface_space(worker_url):
            return False
        health_url = (
            worker_url.replace("segment", "health")
            if "modal.run" in worker_url
            else f"{worker_url.rstrip('/')}/health"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(health_url)
            return response.status_code == 200
    except Exception:
        return False


async def track_frames(
    frame_paths: list[str],
    *,
    seed_frame_index: int,
    bbox: dict[str, float] | None = None,
    seed_mask_path: str | None = None,
    max_frames: int = 120,
    max_seconds: float = 30.0,
    include_masks: bool = False,
) -> TrackResult:
    """Call the bounded SAM2 video tracker with cached local frames."""
    settings = get_settings()
    payload: dict[str, Any] = {
        "frames_b64": [base64.b64encode(Path(path).read_bytes()).decode() for path in frame_paths],
        "seed_frame_index": seed_frame_index,
        "bbox": bbox,
        "max_frames": max_frames,
        "max_seconds": max_seconds,
        "include_masks": include_masks,
    }
    if seed_mask_path:
        payload["seed_mask_b64"] = base64.b64encode(Path(seed_mask_path).read_bytes()).decode()

    async with httpx.AsyncClient(timeout=max_seconds + 10.0) as client:
        response = await client.post(f"{settings.vision_worker_url}/sam/track", json=payload)
        response.raise_for_status()
    data = response.json()
    return TrackResult(
        tracker=str(data["tracker"]),
        frames=list(data.get("frames") or []),
        processed_start_index=int(data["processed_start_index"]),
        processed_end_index=int(data["processed_end_index"]),
        cancelled=bool(data.get("cancelled", False)),
        warning=data.get("warning"),
    )


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
