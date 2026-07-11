"""Google Veo video generation through the Gemini API.

Supports:
- text-to-video
- image-conditioned video generation from a reference frame
- reference-image guided propagation for continuity

Unlike Wan, this path does not perform source-video editing. We rely on the
playhead frame plus prompt grounding rather than sending the full source clip.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable

from google import genai
from google.genai import types

from ai.services.config import get_settings
from ai.services.logger import tracked

TickCallback = Callable[[dict], Awaitable[None] | None]

DEFAULT_MODEL = "veo-3.1-generate-preview"
MIN_DURATION = 3
MAX_DURATION = 8
DEFAULT_DURATION = 5
POLL_INTERVAL = 10
GENERATION_TIMEOUT = 600
SUPPORTED_RESOLUTIONS = {"720P", "1080P"}


def _resolve_duration(duration: int) -> int:
    return max(MIN_DURATION, min(duration, MAX_DURATION))


def _resolve_resolution(resolution: str) -> str:
    res = resolution.strip().upper()
    if res in SUPPORTED_RESOLUTIONS:
        return res
    return "720P"


def _client() -> genai.Client:
    settings = get_settings()
    api_key = settings.gemini_api_key.strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured — required for Google Veo generation")
    return genai.Client(api_key=api_key)


def _image_from_path(path: str) -> types.Image:
    return types.Image.from_file(location=path)


def _output_path(stem: str | None = None) -> Path:
    if not stem:
        stem = f"veo_{int(time.time() * 1000)}"
    out = Path(get_settings().storage_path) / "generated" / f"{stem}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _maybe_await(result) -> None:
    if asyncio.iscoroutine(result):
        try:
            asyncio.get_event_loop().create_task(result)
        except RuntimeError:
            pass


def _build_config(
    *,
    duration: int,
    aspect_ratio: str,
    resolution: str,
    reference_images: list[types.VideoGenerationReferenceImage] | None = None,
) -> types.GenerateVideosConfig:
    kwargs: dict = {
        "aspect_ratio": aspect_ratio,
        "duration_seconds": _resolve_duration(duration),
        "number_of_videos": 1,
        "resolution": _resolve_resolution(resolution),
        "generate_audio": False,
    }
    if reference_images:
        kwargs["reference_images"] = reference_images
    return types.GenerateVideosConfig(**kwargs)


def _build_reference_images(
    *,
    style_reference_path: str | None = None,
    reference_frame_path: str | None = None,
) -> list[types.VideoGenerationReferenceImage]:
    refs: list[types.VideoGenerationReferenceImage] = []
    if style_reference_path:
        refs.append(
            types.VideoGenerationReferenceImage(
                image=_image_from_path(style_reference_path),
                reference_type=types.VideoGenerationReferenceType.STYLE,
            )
        )
    if reference_frame_path:
        refs.append(
            types.VideoGenerationReferenceImage(
                image=_image_from_path(reference_frame_path),
                reference_type=types.VideoGenerationReferenceType.ASSET,
            )
        )
    return refs


async def _generate_and_download(
    *,
    prompt: str,
    image: types.Image | None,
    config: types.GenerateVideosConfig,
    on_tick: TickCallback | None = None,
) -> str:
    client = _client()
    operation = await asyncio.to_thread(
        client.models.generate_videos,
        model=DEFAULT_MODEL,
        prompt=prompt,
        image=image,
        config=config,
    )

    task_name = getattr(operation, "name", None)
    if on_tick is not None:
        _maybe_await(on_tick({
            "kind": "gen.submit",
            "task_id": task_name,
            "model": DEFAULT_MODEL,
            "conditioned": image is not None,
        }))

    elapsed = 0
    while not operation.done:
        if elapsed >= GENERATION_TIMEOUT:
            raise TimeoutError(f"Veo task timed out after {GENERATION_TIMEOUT}s")
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        operation = await asyncio.to_thread(client.operations.get, operation)
        if on_tick is not None:
            _maybe_await(on_tick({
                "kind": "gen.poll",
                "elapsed": elapsed,
                "done": False,
            }))

    response = getattr(operation, "response", None)
    generated = getattr(response, "generated_videos", None) or []
    if not generated:
        raise RuntimeError("Veo generation completed without any generated videos")

    generated_video = generated[0]
    video_file = getattr(generated_video, "video", None)
    if video_file is None:
        raise RuntimeError("Veo generation completed without a downloadable video artifact")

    video_bytes = await asyncio.to_thread(client.files.download, file=video_file)
    output_path = _output_path(task_name or None)
    output_path.write_bytes(video_bytes)
    return str(output_path)


@tracked("veo", "generate_variant")
async def generate_variant(
    prompt: str,
    reference_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "720P",
    on_tick: TickCallback | None = None,
) -> str:
    image = _image_from_path(reference_frame_path) if reference_frame_path else None
    config = _build_config(
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    return await _generate_and_download(
        prompt=prompt,
        image=image,
        config=config,
        on_tick=on_tick,
    )


@tracked("veo", "generate_propagation_variant")
async def generate_propagation_variant(
    prompt: str,
    style_reference_path: str,
    reference_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "720P",
) -> str:
    image_path = reference_frame_path or style_reference_path
    reference_images = _build_reference_images(
        style_reference_path=style_reference_path,
        reference_frame_path=reference_frame_path,
    )
    config = _build_config(
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        reference_images=reference_images,
    )
    return await _generate_and_download(
        prompt=prompt,
        image=_image_from_path(image_path),
        config=config,
        on_tick=None,
    )
