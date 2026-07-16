"""HappyHorse service — video generation through Qwen Cloud (DashScope).

Uses HappyHorse models via the DashScope OpenAI-compatible async API:
- Text-to-video:     happyhorse-1.1-t2v
- Image-to-video:    happyhorse-1.1-i2v  (first_frame conditioning)
- Reference-to-video:happyhorse-1.1-r2v  (reference_image(s) for subject consistency)

Replaces the old Veo 3.1 service.
"""

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from app.ai.services.config import get_settings
from app.ai.services.logger import tracked

TickCallback = Callable[[dict], Awaitable[None] | None]

HAPPYHORSE_BASE_URL = "https://dashscope-intl.aliyuncs.com"
SUBMIT_URL = f"{HAPPYHORSE_BASE_URL}/api/v1/services/aigc/video-generation/video-synthesis"
TASKS_URL = f"{HAPPYHORSE_BASE_URL}/api/v1/tasks"

# HappyHorse constraints
MIN_DURATION = 3
MAX_DURATION = 15
DEFAULT_DURATION = 5
POLL_INTERVAL = 2  # seconds between poll ticks
GENERATION_TIMEOUT = get_settings().video_generation_timeout
DOWNLOAD_ATTEMPTS = 3

DEFAULT_T2V_MODEL = "happyhorse-1.1-t2v"
DEFAULT_I2V_MODEL = "happyhorse-1.1-i2v"
DEFAULT_R2V_MODEL = "happyhorse-1.1-r2v"
DEFAULT_VIDEO_EDIT_MODEL = "happyhorse-1.0-video-edit"

# Supported resolutions
SUPPORTED_RESOLUTIONS = ["720P", "480P"]


def _resolve_duration(duration: int) -> int:
    return max(MIN_DURATION, min(duration, MAX_DURATION))


def _resolve_resolution(resolution: str) -> str:
    res = resolution.strip().upper()
    if res in SUPPORTED_RESOLUTIONS:
        return res
    return "720P"


async def _submit_task(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    payload: dict,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-Async": "enable",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "input": payload.get("input", {}),
        "parameters": payload.get("parameters", {}),
    }
    try:
        resp = await client.post(SUBMIT_URL, headers=headers, json=body)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = ""
        try:
            body = exc.response.text
        except Exception:
            pass
        raise RuntimeError(
            f"HappyHorse submit failed (HTTP {exc.response.status_code}): {body or exc}"
        ) from exc
    data = resp.json()
    task_id = data.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"HappyHorse submit failed: no task_id in response: {data}")
    return task_id


async def _poll_task(
    client: httpx.AsyncClient,
    api_key: str,
    task_id: str,
    on_tick: TickCallback | None = None,
) -> str:
    if on_tick is not None:
        _maybe_await(on_tick({
            "kind": "gen.submit",
            "task_id": task_id,
        }))

    elapsed = 0
    while elapsed < GENERATION_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        try:
            poll_url = f"{TASKS_URL}/{task_id}"
            resp = await client.get(poll_url, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = exc.response.text
            except Exception:
                pass
            raise RuntimeError(
                f"HappyHorse poll failed (HTTP {exc.response.status_code}): {body or exc}"
            ) from exc

        output = data.get("output", {})
        status = output.get("task_status", "")

        if status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                raise RuntimeError(f"HappyHorse task SUCCEEDED but no video_url: {output}")
            return video_url

        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            err = output.get("message", output.get("code", "unknown error"))
            raise RuntimeError(f"HappyHorse task {status}: {err}")

        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.poll",
                "elapsed": elapsed,
                "done": False,
            }))

    raise TimeoutError(f"HappyHorse task timed out after {GENERATION_TIMEOUT}s")


def _maybe_await(result):
    if asyncio.iscoroutine(result):
        try:
            asyncio.get_event_loop().create_task(result)
        except RuntimeError:
            pass


def _image_to_base64(path: str) -> str:
    ext = Path(path).suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    mime = mime_map.get(ext)
    if mime is None:
        raise ValueError(f"reference image must be png/jpg/webp, got: {path}")
    data = Path(path).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _output_path(stem: str | None = None) -> Path:
    if not stem:
        stem = f"happyhorse_{int(time.time() * 1000)}"
    out = Path(get_settings().storage_path) / "generated" / f"{stem}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


async def _download_video(url: str, output_path: Path) -> Path:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            timeout = httpx.Timeout(180.0, connect=30.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                if not resp.content:
                    raise RuntimeError("provider returned an empty video file")
                output_path.write_bytes(resp.content)
                return output_path
        except (httpx.TransportError, httpx.HTTPStatusError, RuntimeError) as exc:
            last_error = exc
            retryable_status = (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code in {408, 425, 429, 500, 502, 503, 504}
            )
            retryable = isinstance(exc, (httpx.TransportError, RuntimeError)) or retryable_status
            if attempt >= DOWNLOAD_ATTEMPTS or not retryable:
                break
            await asyncio.sleep(2 ** (attempt - 1))

    detail = str(last_error).strip() if last_error is not None else "unknown error"
    if not detail and last_error is not None:
        detail = type(last_error).__name__
    raise RuntimeError(
        f"HappyHorse video download failed after {DOWNLOAD_ATTEMPTS} attempts: {detail}"
    ) from last_error


def _build_generation_payload(
    prompt: str,
    reference_frame_path: str | None = None,
    source_video_url: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "720P",
) -> tuple[str, dict, int]:
    """Build a request with semantic reference or first-frame media."""
    duration_sec = _resolve_duration(duration)
    res = _resolve_resolution(resolution)
    params = {"resolution": res, "watermark": False, "audio_setting": "origin"}
    if source_video_url:
        model = DEFAULT_VIDEO_EDIT_MODEL
        media = [{"type": "video", "url": source_video_url}]
        if reference_frame_path:
            media.append(
                {
                    "type": "reference_image",
                    "url": _image_to_base64(reference_frame_path),
                }
            )
        payload = {
            "input": {"prompt": prompt, "media": media},
            "parameters": params,
        }
    else:
        params.update({"ratio": aspect_ratio, "duration": duration_sec})
        if reference_frame_path:
            model = DEFAULT_I2V_MODEL
            payload = {
                "input": {
                    "prompt": prompt,
                    "media": [
                        {
                            "type": "first_frame",
                            "url": _image_to_base64(reference_frame_path),
                        },
                    ],
                },
                "parameters": params,
            }
        else:
            model = DEFAULT_T2V_MODEL
            payload = {"input": {"prompt": prompt}, "parameters": params}
    return model, payload, duration_sec


@tracked("happyhorse", "generate_variant")
async def generate_variant(
    prompt: str,
    reference_frame_path: str | None = None,
    source_video_url: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "720P",
    on_tick: TickCallback | None = None,
) -> str:
    """Generate a single video variant using HappyHorse.

    Args:
        prompt: Structured prompt from the edit plan
        reference_frame_path: Isolated subject reference for source-video edits,
            or full-frame start boundary for image-to-video generation.
        duration: Duration in seconds (3-15)
        aspect_ratio: "16:9", "9:16", "1:1", etc.
        resolution: "720P" or "480P" — 480P is ~2x faster for previews

    Returns:
        Path to the generated video file
    """
    settings = get_settings()
    api_key = settings.dashscope_api_key
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not configured — required for HappyHorse video generation")

    model, payload, duration_sec = _build_generation_payload(
        prompt=prompt,
        reference_frame_path=reference_frame_path,
        source_video_url=source_video_url,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        task_id = await _submit_task(client, api_key, model, payload)

        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.submit",
                "task_id": task_id,
                "prompt": prompt,
                "duration": duration_sec,
                "aspect_ratio": aspect_ratio,
                "conditioned": reference_frame_path is not None,
                "model": model,
            }))

        video_url = await _poll_task(client, api_key, task_id, on_tick=on_tick)

    output_path = _output_path(task_id)
    await _download_video(video_url, output_path)

    return str(output_path)


async def generate_variants_parallel(
    variant_prompts: list[str],
    reference_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "720P",
) -> list[dict[str, str | None]]:
    """Fan-out: generate multiple variants in parallel.

    Args:
        variant_prompts: List of prompts for each variant
        reference_frame_path: Optional reference frame for image conditioning
        duration: Duration in seconds
        aspect_ratio: Aspect ratio
        resolution: "720P" or "480P"

    Returns:
        List of {"path": str | None, "error": str | None} for each variant
    """
    tasks = [
        generate_variant(prompt, reference_frame_path, duration, aspect_ratio, resolution)
        for prompt in variant_prompts
    ]

    results: list[dict[str, str | None]] = []
    for coro in asyncio.as_completed(tasks):
        try:
            path = await coro
            results.append({"path": path, "error": None})
        except Exception as e:
            results.append({"path": None, "error": str(e)})

    return results


async def generate_propagation_variant(
    prompt: str,
    style_reference_path: str,
    reference_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "480P",
    on_tick: TickCallback | None = None,
) -> str:
    """Generate a propagation variant with style reference for consistency.

    Uses the accepted variant's frame as the first_frame conditioning to maintain
    visual continuity across segments.

    Args:
        prompt: Structured prompt for this segment
        style_reference_path: Frame from the accepted variant (for consistency)
        reference_frame_path: Optional reference frame from the target segment
        duration: Duration in seconds
        aspect_ratio: Aspect ratio
        resolution: "720P" or "480P" — defaults to 480P since propagation only
        previews before the final export. The export worker re-renders at full
        resolution from the source + variant clips.

    Returns:
        Path to the generated video file
    """
    # Use the R2V (reference-to-video) model for subject-consistent propagation.
    # This model is specifically designed to preserve the subject's appearance
    # from the reference across new video, which produces much better continuity
    # than I2V (which only uses first-frame conditioning).
    settings = get_settings()
    api_key = settings.dashscope_api_key
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not configured")

    duration_sec = _resolve_duration(duration)
    res = _resolve_resolution(resolution)

    params = {
        "resolution": res,
        "ratio": aspect_ratio,
        "duration": duration_sec,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        model = DEFAULT_R2V_MODEL
        media = [{"type": "reference_image", "url": _image_to_base64(style_reference_path)}]
        if reference_frame_path:
            media.append({"type": "reference_image", "url": _image_to_base64(reference_frame_path)})
        payload = {
            "input": {
                "prompt": prompt,
                "media": media,
            },
            "parameters": params,
        }

        task_id = await _submit_task(client, api_key, model, payload)
        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.submit",
                "task_id": task_id,
                "prompt": prompt,
                "duration": duration_sec,
                "aspect_ratio": aspect_ratio,
                "conditioned": True,
                "model": model,
            }))
        video_url = await _poll_task(client, api_key, task_id, on_tick=on_tick)

    output_path = _output_path(task_id)
    await _download_video(video_url, output_path)

    return str(output_path)
