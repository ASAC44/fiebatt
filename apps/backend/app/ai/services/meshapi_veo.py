"""Veo video generation through Mesh API's asynchronous video endpoint."""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin

import httpx

from app.ai.services.config import get_settings
from app.ai.services.logger import tracked

TickCallback = Callable[[dict], Awaitable[None] | None]

DEFAULT_DURATION = 5
POLL_INTERVAL = 8


def _base_url() -> str:
    return get_settings().mesh_api_base_url.rstrip("/") + "/"


def _endpoint(path: str) -> str:
    return urljoin(_base_url(), path.lstrip("/"))


def _generation_endpoint(job_id: str | None = None) -> str:
    """Return the configured create endpoint or its task-status child URL."""
    endpoint = _endpoint(get_settings().mesh_video_endpoint).rstrip("/")
    return f"{endpoint}/{job_id}" if job_id else endpoint


def _headers() -> dict[str, str]:
    api_key = get_settings().mesh_api_key.strip()
    if not api_key:
        raise RuntimeError("MESH_API_KEY not configured — required for Mesh API video generation")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _output_path(stem: str | None = None) -> Path:
    if not stem:
        stem = f"meshapi_veo_{int(time.time() * 1000)}"
    out = Path(get_settings().storage_path) / "generated" / f"{stem}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _image_data_url(path: str) -> str:
    p = Path(path)
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(p.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def _maybe_await(result) -> None:
    if asyncio.iscoroutine(result):
        try:
            asyncio.get_event_loop().create_task(result)
        except RuntimeError:
            pass


def _extract_job_id(payload: dict) -> str | None:
    for key in ("id", "job_id", "task_id", "operation_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_video_url(payload: dict) -> str | None:
    for key in ("url", "video_url", "output_url"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value

    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return _extract_video_url(first)

    # Mesh's succeeded task shape is {"content": {"video_url": "..."}}.
    content = payload.get("content")
    if isinstance(content, dict):
        return _extract_video_url(content)

    output = payload.get("output")
    if isinstance(output, dict):
        return _extract_video_url(output)
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, dict):
            return _extract_video_url(first)
        if isinstance(first, str):
            return first

    return None


def _build_payload(
    *,
    model: str,
    prompt: str,
    reference_frame_path: str | None,
    duration: int,
    aspect_ratio: str,
    resolution: str,
) -> dict:
    """Build Mesh's multimodal video request body.

    Mesh video generation does not use the chat/image ``prompt`` convention:
    all inputs are represented as typed entries in ``content``.
    """
    content: list[dict] = []
    if reference_frame_path:
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_data_url(reference_frame_path)},
        })
    content.append({"type": "text", "text": prompt})

    return {
        "model": model,
        "content": content,
        "duration": duration,
        "ratio": aspect_ratio,
        "resolution": resolution.lower(),
        "generate_audio": False,
    }


async def _download_video(url: str, *, stem: str | None = None) -> str:
    out = _output_path(stem)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        out.write_bytes(resp.content)
    return str(out)


async def _poll_for_video(
    client: httpx.AsyncClient,
    job_id: str,
    *,
    on_tick: TickCallback | None,
) -> str:
    elapsed = 0
    timeout = get_settings().video_generation_timeout
    status_url = _generation_endpoint(job_id)

    while elapsed < timeout:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        resp = await client.get(status_url, headers=_headers())
        resp.raise_for_status()
        payload = resp.json()

        status = str(payload.get("status", "")).lower()
        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.poll",
                "elapsed": elapsed,
                "status": status or "unknown",
            }))

        video_url = _extract_video_url(payload)
        if video_url and status in {"", "done", "completed", "succeeded", "success"}:
            return video_url
        if status in {"failed", "error", "expired", "cancelled", "canceled"}:
            error = payload.get("error")
            raise RuntimeError(
                f"Mesh API video generation ended with status {status}: {error or payload}"
            )

    raise TimeoutError(f"Mesh API video generation timed out after {timeout}s")


async def _submit_generation(
    *,
    prompt: str,
    reference_frame_path: str | None,
    duration: int,
    aspect_ratio: str,
    resolution: str,
    on_tick: TickCallback | None,
) -> str:
    settings = get_settings()
    request_payload = _build_payload(
        model=settings.mesh_video_model,
        prompt=prompt,
        reference_frame_path=reference_frame_path,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            _generation_endpoint(),
            headers=_headers(),
            json=request_payload,
        )
        resp.raise_for_status()
        payload = resp.json()

        job_id = _extract_job_id(payload)
        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.submit",
                "task_id": job_id,
                "model": settings.mesh_video_model,
                "conditioned": reference_frame_path is not None,
            }))

        video_url = _extract_video_url(payload)
        if video_url:
            return video_url
        if job_id:
            return await _poll_for_video(client, job_id, on_tick=on_tick)

    raise RuntimeError(f"Mesh API video response did not include a video URL or job id: {payload}")


@tracked("meshapi_veo", "generate_variant")
async def generate_variant(
    prompt: str,
    reference_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "720P",
    on_tick: TickCallback | None = None,
) -> str:
    video_url = await _submit_generation(
        prompt=prompt,
        reference_frame_path=reference_frame_path,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        on_tick=on_tick,
    )
    return await _download_video(video_url)


@tracked("meshapi_veo", "generate_propagation_variant")
async def generate_propagation_variant(
    prompt: str,
    style_reference_path: str,
    reference_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "720P",
) -> str:
    return await generate_variant(
        prompt=prompt,
        reference_frame_path=reference_frame_path or style_reference_path,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        on_tick=None,
    )
