from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

import cv2
import numpy as np

from app.ai.services import sam
from app.models.project import Project
from app.models.selection import SelectionArtifact
from app.schemas.edit_plan import (
    EditCore,
    EditIntent,
    GenerationContext,
    LocalRangeResolution,
)
from app.services import ffmpeg, storage


ANALYSIS_FPS = 4.0
HANDLE_SECONDS = 0.75
LOCAL_MARGIN_SECONDS = 1.0
PERSISTENT_TRACK_BUDGET_SECONDS = 30.0
SHOT_CHANGE_THRESHOLD = 0.32

_RANGE_CACHE: dict[str, LocalRangeResolution] = {}


def _persistent_change(intent: EditIntent) -> bool:
    return any("complete visible occurrence" in item for item in intent.preservation_requirements)


def analysis_window(intent: EditIntent, seed_ts: float, duration: float) -> tuple[float, float]:
    if _persistent_change(intent):
        radius = PERSISTENT_TRACK_BUDGET_SECONDS / 2
    else:
        radius = intent.estimated_action_seconds / 2 + HANDLE_SECONDS + LOCAL_MARGIN_SECONDS
    return max(0.0, seed_ts - radius), min(duration, seed_ts + radius)


def _fit_interval(center: float, length: float, lower: float, upper: float) -> tuple[float, float]:
    length = min(length, max(0.0, upper - lower))
    start = center - length / 2
    end = center + length / 2
    if start < lower:
        end += lower - start
        start = lower
    if end > upper:
        start -= end - upper
        end = upper
    return max(lower, start), min(upper, end)


def resolve_window_from_evidence(
    *,
    intent: EditIntent,
    seed_ts: float,
    duration: float,
    analysis_start: float,
    analysis_end: float,
    shot_start: float,
    shot_end: float,
    tracked_start: float | None,
    tracked_end: float | None,
    frames_inspected: int,
    explicit_core: EditCore | None = None,
    tracking_reached_budget: bool = False,
) -> LocalRangeResolution:
    occurrence_start = max(0.0, shot_start, tracked_start or 0.0)
    occurrence_end = min(duration, shot_end, tracked_end or duration)
    warnings: list[str] = []
    if occurrence_end <= occurrence_start:
        occurrence_start, occurrence_end = analysis_start, analysis_end
        warnings.append("target track was inconclusive; using analyzed shot window")

    if explicit_core is not None:
        core_start = max(occurrence_start, explicit_core.start_ts)
        core_end = min(occurrence_end, explicit_core.end_ts)
        if core_end <= core_start:
            raise ValueError("explicit range does not overlap selected occurrence")
    elif _persistent_change(intent):
        core_start, core_end = occurrence_start, occurrence_end
        if tracking_reached_budget:
            warnings.append("target remains visible at local tracking budget; adjust range to continue")
    else:
        core_start, core_end = _fit_interval(
            seed_ts,
            intent.estimated_action_seconds,
            occurrence_start,
            occurrence_end,
        )

    context_start = max(occurrence_start, core_start - HANDLE_SECONDS)
    context_end = min(occurrence_end, core_end + HANDLE_SECONDS)
    pre_handle = core_start - context_start
    post_handle = context_end - core_end
    if pre_handle < HANDLE_SECONDS - 0.05:
        warnings.append("limited pre-roll before edit core")
    if post_handle < HANDLE_SECONDS - 0.05:
        warnings.append("limited post-roll after edit core")

    requested_core = explicit_core.duration if explicit_core else intent.estimated_action_seconds
    core_coverage = min(1.0, (core_end - core_start) / max(requested_core, 1e-6))
    handle_coverage = min(1.0, (pre_handle + post_handle) / (2 * HANDLE_SECONDS))
    confidence = max(0.0, min(1.0, 0.65 * core_coverage + 0.35 * handle_coverage))
    core = EditCore(start_ts=core_start, end_ts=core_end)
    context = GenerationContext(start_ts=context_start, end_ts=context_end, edit_core=core)
    return LocalRangeResolution(
        edit_core=core,
        generation_context=context,
        occurrence_start=occurrence_start,
        occurrence_end=occurrence_end,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
        frames_inspected=frames_inspected,
        confidence=confidence,
        warnings=warnings,
    )


def detect_shot_span(
    frame_paths: list[str], timestamps: list[float], seed_index: int
) -> tuple[float, float]:
    if not frame_paths:
        raise ValueError("shot detection requires frames")
    small_frames = []
    for path in frame_paths:
        frame = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise ValueError(f"failed to read analysis frame: {path}")
        small_frames.append(cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA))
    cuts = []
    for index in range(1, len(small_frames)):
        difference = np.mean(
            np.abs(small_frames[index].astype(np.float32) - small_frames[index - 1])
        ) / 255.0
        if difference >= SHOT_CHANGE_THRESHOLD:
            cuts.append(index)
    prior = max((cut for cut in cuts if cut <= seed_index), default=0)
    following = min((cut for cut in cuts if cut > seed_index), default=len(timestamps) - 1)
    return timestamps[prior], timestamps[following]


def tracked_span(
    frames: list[dict], timestamps: list[float], seed_index: int
) -> tuple[float | None, float | None]:
    states = {int(frame["frame_index"]): frame.get("state") for frame in frames}
    if states.get(seed_index) != "tracked":
        return None, None
    left = seed_index
    right = seed_index
    while left - 1 >= 0 and states.get(left - 1) == "tracked":
        left -= 1
    while right + 1 < len(timestamps) and states.get(right + 1) == "tracked":
        right += 1
    return timestamps[left], timestamps[right]


def _cache_key(
    selection: SelectionArtifact, intent: EditIntent, explicit_core: EditCore | None
) -> str:
    value = json.dumps(
        {
            "selection_id": selection.id,
            "source_revision": selection.source_revision,
            "intent": intent.model_dump(mode="json"),
            "explicit_core": explicit_core.model_dump() if explicit_core else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(value.encode()).hexdigest()


async def resolve_local_range(
    project: Project,
    selection: SelectionArtifact,
    intent: EditIntent,
    *,
    explicit_core: EditCore | None = None,
    extract_frame: Callable[[str | Path, float, str | Path], Awaitable[Path]] = ffmpeg.extract_frame,
    track_frames: Callable[..., Awaitable[sam.TrackResult]] = sam.track_frames,
) -> LocalRangeResolution:
    key = _cache_key(selection, intent, explicit_core)
    cached = _RANGE_CACHE.get(key)
    if cached is not None:
        return cached.model_copy(deep=True)

    source = Path(project.video_path)
    if not source.exists():
        source = await storage.path_from_url(project.video_url)
    seed_mask_path: str | None = None
    mask_url = getattr(selection, "mask_url", None)
    if mask_url:
        try:
            seed_mask_path = str(await storage.path_from_url(mask_url))
        except Exception:
            seed_mask_path = None
    start, end = analysis_window(intent, selection.frame_ts, project.duration)
    frame_count = max(2, math.floor((end - start) * ANALYSIS_FPS) + 1)
    timestamps = [
        min(end, start + index / ANALYSIS_FPS) for index in range(frame_count)
    ]
    seed_index = min(range(len(timestamps)), key=lambda index: abs(timestamps[index] - selection.frame_ts))

    with tempfile.TemporaryDirectory(prefix="fiebatt-local-range-") as temp_dir:
        frame_paths = []
        for index, timestamp in enumerate(timestamps):
            path = Path(temp_dir) / f"{index:06d}.jpg"
            await extract_frame(source, timestamp, path)
            frame_paths.append(str(path))
        shot_start, shot_end = detect_shot_span(frame_paths, timestamps, seed_index)
        track = await track_frames(
            frame_paths,
            seed_frame_index=seed_index,
            bbox=selection.bbox_json,
            seed_mask_path=seed_mask_path,
            max_frames=len(frame_paths),
            max_seconds=PERSISTENT_TRACK_BUDGET_SECONDS,
            include_masks=False,
        )

    track_start, track_end = tracked_span(track.frames, timestamps, seed_index)
    resolution = resolve_window_from_evidence(
        intent=intent,
        seed_ts=selection.frame_ts,
        duration=project.duration,
        analysis_start=start,
        analysis_end=end,
        shot_start=shot_start,
        shot_end=shot_end,
        tracked_start=track_start,
        tracked_end=track_end,
        frames_inspected=len(timestamps),
        explicit_core=explicit_core,
        tracking_reached_budget=(
            _persistent_change(intent)
            and track_start is not None
            and track_end is not None
            and start > 0.0
            and end < project.duration
            and abs(track_start - start) < 1 / ANALYSIS_FPS
            and abs(track_end - end) < 1 / ANALYSIS_FPS
        ),
    )
    if track.warning:
        resolution.warnings.append(track.warning)
    _RANGE_CACHE[key] = resolution.model_copy(deep=True)
    return resolution


def clear_local_range_cache() -> None:
    _RANGE_CACHE.clear()
