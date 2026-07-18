"""Prepare and generate one tracked global-edit chunk."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from app.ai import services as ai
from app.ai.services.provider_capabilities import select_source_edit_mode
from app.config.settings import get_settings
from app.models.propagation import GlobalGenerationChunk
from app.models.project import Project
from app.services import ffmpeg, storage
from app.services.global_chunk_sequence import ChunkExecution
from app.services.generation_window import GenerationWindow


@dataclass(frozen=True, slots=True)
class PreviousChunk:
    context_start: float
    context_end: float
    output_url: str


def _is_public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname not in {None, "localhost", "127.0.0.1", "0.0.0.0"}
    )


def target_bbox(payload: dict, timestamp: float) -> dict[str, float]:
    frames = payload.get("track_frames")
    candidates = [
        frame
        for frame in frames if isinstance(frame, dict)
        and frame.get("state") == "tracked"
        and isinstance(frame.get("bbox"), dict)
    ] if isinstance(frames, list) else []
    if not candidates:
        raise ValueError("global chunk has no tracked target box")
    nearest = min(
        candidates,
        key=lambda frame: abs(float(frame.get("timestamp") or 0.0) - timestamp),
    )
    bbox = nearest["bbox"]
    normalized = {
        key: float(bbox.get(key, 0.0))
        for key in ("x", "y", "w", "h")
    }
    if (
        normalized["w"] <= 0
        or normalized["h"] <= 0
        or normalized["x"] < 0
        or normalized["y"] < 0
        or normalized["x"] + normalized["w"] > 1.001
        or normalized["y"] + normalized["h"] > 1.001
    ):
        raise ValueError("global chunk target box is invalid")
    return normalized


def global_chunk_prompt(
    prompt: str,
    window: GenerationWindow,
    boundary_contract: dict,
) -> str:
    protect_before = bool(boundary_contract.get("protect_source_before"))
    protect_after = bool(boundary_contract.get("protect_source_after"))
    handoff_before = bool(boundary_contract.get("handoff_from_previous"))
    handoff_after = bool(boundary_contract.get("handoff_to_next"))
    instructions = [
        "CONTINUOUS GLOBAL EDIT CONTRACT:",
        f"The requested edit core is seconds {window.edit_start_offset:.3f} through "
        f"{window.edit_end_offset:.3f} of this supplied source clip.",
    ]
    if protect_before:
        instructions.append(
            f"Preserve the original entrance exactly for the first {window.pre_handle:.3f} "
            "seconds, then enter the edit with continuous subject and camera motion."
        )
    elif handoff_before:
        instructions.append(
            f"The first {window.pre_handle:.3f} seconds contain the accepted ending of "
            "the previous chunk. Preserve that edited appearance, pose, velocity, camera "
            "motion, lighting, and background; continue from it without reverting."
        )
    if handoff_after:
        instructions.append(
            "Continue the requested edit through the end of this clip. The final overlap "
            "will seed the next chunk, so do not return to the original appearance, freeze, "
            "fade, or finish the action early."
        )
    elif protect_after:
        instructions.append(
            f"Use the final {window.post_handle:.3f} seconds to leave the edit and match "
            "the original continuation exactly, without a cut, fade, or frozen pose."
        )
    instructions.append("Apply the change only to the tracked subject. Preserve everything else.")
    return "\n".join(instructions) + "\n\n" + prompt


async def prepare_reference_subject(
    *,
    reference_video_url: str,
    reference_json: dict,
) -> Path:
    video_path = await storage.path_from_url(reference_video_url)
    media_start = max(0.0, float(reference_json.get("media_start") or 0.0))
    media_end = float(reference_json.get("media_end") or media_start)
    if media_end <= media_start:
        metadata = await ffmpeg.probe(video_path)
        media_end = float(metadata["duration"])
    frame_path, _ = storage.new_path("keyframes", "jpg")
    raw_timestamp = reference_json.get("media_timestamp")
    requested_timestamp = (
        float(raw_timestamp)
        if raw_timestamp is not None
        else (media_start + media_end) / 2
    )
    media_timestamp = min(
        media_end,
        max(
            media_start,
            requested_timestamp,
        ),
    )
    await ffmpeg.extract_frame(video_path, media_timestamp, frame_path)
    bbox = reference_json.get("bbox")
    if isinstance(bbox, dict) and float(bbox.get("w", 0.0)) > 0:
        return await ffmpeg.crop_bbox_from_frame(frame_path, bbox)
    return frame_path


async def _prepare_source_clip(
    *,
    project: Project,
    chunk: GlobalGenerationChunk,
    previous: PreviousChunk | None,
) -> Path:
    original_source = await storage.materialize_source(
        project.video_path,
        project.video_url,
    )
    source_path, _ = storage.new_path("clips", "mp4")
    await ffmpeg.extract_clip(
        original_source,
        chunk.context_start,
        chunk.context_end,
        source_path,
        with_audio=False,
    )
    contract = (chunk.payload_json or {}).get("boundary_contract") or {}
    if not bool(contract.get("handoff_from_previous")):
        return source_path
    if previous is None:
        raise ValueError("global chunk is missing its previous handoff")
    overlap_start = max(previous.context_start, chunk.context_start)
    overlap_end = min(previous.context_end, chunk.context_end)
    if overlap_end <= overlap_start + 0.05:
        raise ValueError("adjacent global chunks have no usable overlap")
    previous_path = await storage.path_from_url(previous.output_url)
    overlap_path, _ = storage.new_path("clips", "mp4")
    await ffmpeg.extract_clip(
        previous_path,
        overlap_start - previous.context_start,
        overlap_end - previous.context_start,
        overlap_path,
        with_audio=False,
    )
    handed_source, _ = storage.new_path("clips", "mp4")
    await ffmpeg.prepend_video_handoff(
        source_path,
        overlap_path,
        overlap_end - overlap_start,
        handed_source,
    )
    return handed_source


async def execute_global_chunk(
    *,
    project: Project,
    chunk: GlobalGenerationChunk,
    prompt: str,
    reference_subject_path: Path,
    previous: PreviousChunk | None,
    on_tick: Callable[[dict], Awaitable[None] | None] | None = None,
) -> ChunkExecution:
    source_path = await _prepare_source_clip(
        project=project,
        chunk=chunk,
        previous=previous,
    )
    source_url = await storage.publish(source_path, content_type="video/mp4")
    settings = get_settings()
    if not settings.use_ai_stubs and not _is_public_http_url(source_url):
        raise ValueError(
            "global source edits require provider-accessible media storage"
        )

    duration = chunk.context_end - chunk.context_start
    midpoint = (chunk.edit_start + chunk.edit_end) / 2
    bbox = target_bbox(chunk.payload_json or {}, midpoint)
    target_frame_path, _ = storage.new_path("keyframes", "jpg")
    seed_offset = min(max(0.0, midpoint - chunk.context_start), duration)
    await ffmpeg.extract_frame(source_path, seed_offset, target_frame_path)

    mask_frame_id = max(1, round(seed_offset * float(project.fps or 1.0)) + 1)

    edit_mode = select_source_edit_mode(
        chunk.provider,
        duration=duration,
        source_video=True,
        mask_available=False,
    )
    window = GenerationWindow(
        core_start=chunk.edit_start,
        core_end=chunk.edit_end,
        context_start=chunk.context_start,
        context_end=chunk.context_end,
        adaptive=True,
    )
    edit_prompt = global_chunk_prompt(
        prompt,
        window,
        (chunk.payload_json or {}).get("boundary_contract") or {},
    )
    result = await ai.runway.generate(
        str(source_path),
        {
            "description": f"global edit chunk {chunk.index + 1}",
            "_video_gen_provider": chunk.provider,
            "_edit_prompt": edit_prompt,
        },
        subject_reference_path=str(reference_subject_path),
        source_video_url=source_url,
        mask_image_url=None,
        mask_frame_id=mask_frame_id,
        duration=math.ceil(duration - 1e-6),
        resolution="720P",
        on_tick=on_tick,
    )
    raw_url = str(result.get("url") or "")
    generated_path = Path(str(result.get("path") or ""))
    if raw_url in {str(source_path), source_path.as_posix()}:
        output_url = source_url
    elif generated_path.is_file():
        conformed_path, _ = storage.new_path("generated", "mp4")
        await ffmpeg.conform_generated_edit(
            generated_path,
            source_path,
            duration,
            conformed_path,
        )
        output_url = await storage.publish(conformed_path, content_type="video/mp4")
    else:
        output_url = storage.normalize_url_like(raw_url, fallback=source_url)
    return ChunkExecution(
        output_url=output_url,
        metadata={
            "provider": chunk.provider,
            "edit_mode": edit_mode,
            "context_duration": round(duration, 3),
            "target_bbox": bbox,
            "handoff_used": previous is not None,
            "mask_used": False,
        },
    )
