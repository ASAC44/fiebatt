"""Wan service — video generation through Qwen Cloud (DashScope).

Uses Wan 2.7 models via the DashScope async API:
- Text-to-video:     wan2.7-t2v-2026-04-25
- Video editing:     wan2.7-videoedit  (instruction-based editing & style transfer)

HappyHorse and Wan share the same DashScope API infrastructure but use different model names.
This module mirrors ai/services/happyhorse.py with Wan-specific model names and defaults. Wan
supports both ordinary generation and source-video instruction editing; the latter is the
important path for keeping motion and temporal context around a localized edit.
"""

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from ai.services.config import get_settings
from ai.services.logger import tracked

TickCallback = Callable[[dict], Awaitable[None] | None]

WAN_BASE_URL = "https://dashscope-intl.aliyuncs.com"
SUBMIT_URL = f"{WAN_BASE_URL}/api/v1/services/aigc/video-generation/video-synthesis"
TASKS_URL = f"{WAN_BASE_URL}/api/v1/tasks"

MIN_DURATION = 2
MAX_DURATION = 15
DEFAULT_DURATION = 5
POLL_INTERVAL = 2
GENERATION_TIMEOUT = get_settings().video_generation_timeout

DEFAULT_T2V_MODEL = "wan2.7-t2v-2026-04-25"
DEFAULT_VIDEOEDIT_MODEL = "wan2.7-videoedit"
DEFAULT_LOCAL_EDIT_MODEL = "wan2.1-vace-plus"

SUPPORTED_RESOLUTIONS = ["720P", "1080P"]


def _resolve_duration(duration: int) -> int:
    return max(MIN_DURATION, min(duration, MAX_DURATION))


def _resolve_resolution(resolution: str) -> str:
    res = resolution.strip().upper()
    if res in SUPPORTED_RESOLUTIONS:
        return res
    return "1080P"


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
            f"Wan submit failed (HTTP {exc.response.status_code}): {body or exc}"
        ) from exc
    data = resp.json()
    task_id = data.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"Wan submit failed: no task_id in response: {data}")
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
                f"Wan poll failed (HTTP {exc.response.status_code}): {body or exc}"
            ) from exc

        output = data.get("output", {})
        status = output.get("task_status", "")

        if status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                raise RuntimeError(f"Wan task SUCCEEDED but no video_url: {output}")
            return video_url

        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            err = output.get("message", output.get("code", "unknown error"))
            raise RuntimeError(f"Wan task {status}: {err}")

        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.poll",
                "elapsed": elapsed,
                "done": False,
            }))

    raise TimeoutError(f"Wan task timed out after {GENERATION_TIMEOUT}s")


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
        stem = f"wan_{int(time.time() * 1000)}"
    out = Path(get_settings().storage_path) / "generated" / f"{stem}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


VIDEO_EDIT_NEGATIVE_PROMPT = (
    "hard cut, dissolve, fade, rectangular boxes, transition graphics, ghosting, "
    "double exposure, duplicate person, frozen subject, frame mismatch, flicker, "
    "background drift, camera jump, changed horse, changed background, regenerated scene"
)


def _build_video_edit_payload(
    prompt: str,
    source_video_url: str,
    reference_frame_path: str | None = None,
    resolution: str = "720P",
) -> dict:
    """Build a Wan video-edit request without performing network I/O."""
    media = [{"type": "video", "url": source_video_url}]
    if reference_frame_path:
        media.append({
            "type": "reference_image",
            "url": _image_to_base64(reference_frame_path),
        })

    target_instruction = (
        "The reference image contains the exact isolated target subject. Apply the "
        "requested change only to that subject. "
        if reference_frame_path
        else "Apply the requested change only to the subject named in the request. "
    )
    preserve_prompt = (
        "Edit the supplied source video directly. "
        + target_instruction
        + "Preserve every other person and object, the exact background, camera "
        "framing, perspective, lighting, shadows, clothing, and original timing. "
        "Keep the result natural and temporally continuous with the source video. "
        "Do not regenerate the scene.\n\n"
        + prompt
    )
    return {
        "input": {
            "prompt": preserve_prompt,
            "negative_prompt": VIDEO_EDIT_NEGATIVE_PROMPT,
            "media": media,
        },
        "parameters": {
            "resolution": _resolve_resolution(resolution),
            "prompt_extend": True,
            "watermark": False,
        },
    }


def _build_local_edit_payload(
    prompt: str,
    source_video_url: str,
    mask_image_url: str,
    mask_frame_id: int = 1,
) -> dict:
    """Build Wan VACE's native tracked-mask local-edit request."""
    return {
        "input": {
            "prompt": prompt,
            "function": "video_edit",
            "video_url": source_video_url,
            "mask_image_url": mask_image_url,
            "mask_frame_id": max(1, int(mask_frame_id)),
        },
        "parameters": {
            "mask_type": "tracking",
            "expand_ratio": 0.08,
            "expand_mode": "original",
            "prompt_extend": False,
            "watermark": False,
        },
    }


async def _download_video(url: str, output_path: Path) -> Path:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
    return output_path


@tracked("wan", "generate_variant")
async def generate_variant(
    prompt: str,
    reference_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "1080P",
    on_tick: TickCallback | None = None,
) -> str:
    settings = get_settings()
    api_key = settings.dashscope_api_key
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not configured — required for Wan video generation")

    duration_sec = _resolve_duration(duration)
    res = _resolve_resolution(resolution)

    params = {
        "resolution": res,
        "ratio": aspect_ratio,
        "duration": duration_sec,
        "prompt_extend": True,
        "watermark": False,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        model = DEFAULT_T2V_MODEL
        payload = {
            "input": {"prompt": prompt},
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
                "conditioned": False,
                "model": model,
            }))

        video_url = await _poll_task(client, api_key, task_id, on_tick=on_tick)

    output_path = _output_path(task_id)
    await _download_video(video_url, output_path)

    return str(output_path)


@tracked("wan", "generate_edit_variant")
async def generate_edit_variant(
    prompt: str,
    source_video_url: str,
    reference_frame_path: str | None = None,
    resolution: str = "720P",
    on_tick: TickCallback | None = None,
) -> str:
    """Edit an existing public video while retaining its temporal context."""
    settings = get_settings()
    api_key = settings.dashscope_api_key
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not configured — required for Wan video editing")
    if not source_video_url.startswith(("http://", "https://")):
        raise ValueError("Wan video editing requires a public http(s) source_video_url")

    payload = _build_video_edit_payload(
        prompt=prompt,
        source_video_url=source_video_url,
        reference_frame_path=reference_frame_path,
        resolution=resolution,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        task_id = await _submit_task(client, api_key, DEFAULT_VIDEOEDIT_MODEL, payload)
        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.submit",
                "task_id": task_id,
                "model": DEFAULT_VIDEOEDIT_MODEL,
                "conditioned": True,
                "source_video": True,
            }))
        video_url = await _poll_task(client, api_key, task_id, on_tick=on_tick)

    output_path = _output_path(task_id)
    await _download_video(video_url, output_path)
    return str(output_path)


@tracked("wan", "generate_local_edit_variant")
async def generate_local_edit_variant(
    prompt: str,
    source_video_url: str,
    mask_image_url: str,
    mask_frame_id: int = 1,
    on_tick: TickCallback | None = None,
) -> str:
    """Run a native mask-tracked local edit for a source clip up to five seconds."""
    settings = get_settings()
    api_key = settings.dashscope_api_key
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not configured — required for Wan local editing")
    if not source_video_url.startswith(("http://", "https://")):
        raise ValueError("Wan local editing requires a public http(s) source_video_url")
    if not mask_image_url.startswith(("http://", "https://")):
        raise ValueError("Wan local editing requires a public http(s) mask_image_url")

    payload = _build_local_edit_payload(
        prompt=prompt,
        source_video_url=source_video_url,
        mask_image_url=mask_image_url,
        mask_frame_id=mask_frame_id,
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        task_id = await _submit_task(client, api_key, DEFAULT_LOCAL_EDIT_MODEL, payload)
        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.submit",
                "task_id": task_id,
                "model": DEFAULT_LOCAL_EDIT_MODEL,
                "conditioned": True,
                "source_video": True,
                "mask_tracking": True,
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
    resolution: str = "1080P",
) -> list[dict[str, str | None]]:
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


@tracked("wan", "generate_propagation_variant")
async def generate_propagation_variant(
    prompt: str,
    style_reference_path: str,
    reference_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "1080P",
) -> str:
    settings = get_settings()
    api_key = settings.dashscope_api_key
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not configured")

    res = _resolve_resolution(resolution)

    params = {
        "resolution": res,
        "prompt_extend": True,
        "watermark": False,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        model = DEFAULT_VIDEOEDIT_MODEL
        media = [{"type": "video", "url": style_reference_path}]
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
        video_url = await _poll_task(client, api_key, task_id)

    output_path = _output_path(task_id)
    await _download_video(video_url, output_path)

    return str(output_path)
