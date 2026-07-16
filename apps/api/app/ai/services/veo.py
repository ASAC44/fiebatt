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

from app.ai.services.config import get_settings
from app.ai.services.logger import tracked

TickCallback = Callable[[dict], Awaitable[None] | None]

DEFAULT_MODEL = "veo-3.1-fast-generate-preview"
ALLOWED_DURATIONS = (4, 6, 8)
DEFAULT_DURATION = 4
POLL_INTERVAL = 10
GENERATION_TIMEOUT = get_settings().video_generation_timeout
SUPPORTED_RESOLUTIONS = {"720P": "720p", "1080P": "1080p"}


def _resolve_duration(duration: int) -> int:
    if duration not in ALLOWED_DURATIONS:
        allowed = ", ".join(str(item) for item in ALLOWED_DURATIONS)
        raise ValueError(f"Veo duration must be one of {allowed} seconds, got {duration}")
    return duration


def _resolve_resolution(resolution: str) -> str:
    res = resolution.strip().upper()
    return SUPPORTED_RESOLUTIONS.get(res, "720p")


def _model() -> str:
    return get_settings().veo_model.strip() or DEFAULT_MODEL


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
    last_frame: types.Image | None = None,
) -> types.GenerateVideosConfig:
    resolved_duration = _resolve_duration(duration)
    resolved_resolution = _resolve_resolution(resolution)
    if reference_images and resolved_duration != 8:
        raise ValueError("Veo reference-image generation requires an 8-second duration")
    if last_frame is not None and resolved_duration != 8:
        raise ValueError("Veo first/last-frame interpolation requires an 8-second duration")
    if resolved_resolution == "1080p" and resolved_duration != 8:
        raise ValueError("Veo 1080p generation requires an 8-second duration")
    kwargs: dict = {
        "aspect_ratio": aspect_ratio,
        "duration_seconds": resolved_duration,
        "number_of_videos": 1,
        "resolution": resolved_resolution,
    }
    if reference_images:
        kwargs["reference_images"] = reference_images
    if last_frame is not None:
        kwargs["last_frame"] = last_frame
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
    model = _model()
    operation = await asyncio.to_thread(
        client.models.generate_videos,
        model=model,
        prompt=prompt,
        image=image,
        config=config,
    )

    task_name = getattr(operation, "name", None)
    if on_tick is not None:
        _maybe_await(on_tick({
            "kind": "gen.submit",
            "task_id": task_name,
            "model": model,
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
    last_frame_path: str | None = None,
    duration: int = DEFAULT_DURATION,
    aspect_ratio: str = "16:9",
    resolution: str = "720P",
    on_tick: TickCallback | None = None,
) -> str:
    image = _image_from_path(reference_frame_path) if reference_frame_path else None
    if last_frame_path and image is None:
        raise ValueError("Veo last-frame conditioning requires a first reference frame")
    config = _build_config(
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        last_frame=_image_from_path(last_frame_path) if last_frame_path else None,
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
