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
from app.services import cpu_tracking


ANALYSIS_FPS = 4.0
CONTINUOUS_ANALYSIS_FPS = 2.0
HANDLE_SECONDS = 0.75
LOCAL_MARGIN_SECONDS = 1.0
INITIAL_CONTINUOUS_RADIUS_SECONDS = 3.0
MAX_CONTINUOUS_EDIT_SECONDS = 30.0
CONTINUOUS_EDGE_PROBE_SECONDS = 1.0
SHOT_CHANGE_THRESHOLD = 0.32

_RANGE_CACHE: dict[str, LocalRangeResolution] = {}


class EditWindowLimitError(ValueError):
    def __init__(self, duration: float, limit: float = MAX_CONTINUOUS_EDIT_SECONDS):
        self.duration = duration
        self.limit = limit
        super().__init__(
            f"this edit covers more than {limit:g} seconds; choose a shorter range"
        )


def _persistent_change(intent: EditIntent) -> bool:
    return intent.duration_policy == "continuous_occurrence"


def analysis_window(
    intent: EditIntent,
    seed_ts: float,
    duration: float,
    *,
    source_start: float = 0.0,
    source_end: float | None = None,
) -> tuple[float, float]:
    upper = duration if source_end is None else min(duration, source_end)
    if source_start < 0.0 or upper <= source_start:
        raise ValueError("active source clip must have positive duration")
    if seed_ts < source_start - 1e-3 or seed_ts > upper + 1e-3:
        raise ValueError("selection is outside the active source clip")
    if _persistent_change(intent):
        radius = INITIAL_CONTINUOUS_RADIUS_SECONDS
    else:
        radius = intent.estimated_action_seconds / 2 + HANDLE_SECONDS + LOCAL_MARGIN_SECONDS
    return max(source_start, seed_ts - radius), min(upper, seed_ts + radius)


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
    source_start: float = 0.0,
    source_end: float | None = None,
) -> LocalRangeResolution:
    upper = duration if source_end is None else min(duration, source_end)
    tracked_lower = tracked_start if tracked_start is not None else source_start
    tracked_upper = tracked_end if tracked_end is not None else upper
    occurrence_start = max(source_start, shot_start, tracked_lower)
    occurrence_end = min(upper, shot_end, tracked_upper)
    warnings: list[str] = []
    if occurrence_end <= occurrence_start:
        occurrence_start, occurrence_end = analysis_start, analysis_end
        warnings.append("target track was inconclusive; using analyzed shot window")

    if explicit_core is not None:
        if (
            explicit_core.start_ts < source_start - 1e-3
            or explicit_core.end_ts > upper + 1e-3
        ):
            raise ValueError("explicit range exceeds active source clip")
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

    # Context belongs to the surrounding shot, not only frames where target
    # is visible. This preserves real entrance/exit motion for state changes.
    context_start = max(source_start, shot_start, core_start - HANDLE_SECONDS)
    context_end = min(upper, shot_end, core_end + HANDLE_SECONDS)
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
    selection: SelectionArtifact,
    intent: EditIntent,
    explicit_core: EditCore | None,
    source_start: float,
    source_end: float,
) -> str:
    value = json.dumps(
        {
            "selection_id": selection.id,
            "source_revision": selection.source_revision,
            "intent": intent.model_dump(mode="json"),
            "explicit_core": explicit_core.model_dump() if explicit_core else None,
            "source_start": source_start,
            "source_end": source_end,
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
    track_frames: Callable[..., Awaitable[sam.TrackResult]] = cpu_tracking.track_frames,
    source_start: float = 0.0,
    source_end: float | None = None,
) -> LocalRangeResolution:
    bounded_end = project.duration if source_end is None else min(project.duration, source_end)
    key = _cache_key(selection, intent, explicit_core, source_start, bounded_end)
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
    start, end = analysis_window(
        intent,
        selection.frame_ts,
        project.duration,
        source_start=source_start,
        source_end=bounded_end,
    )
    analysis_fps = CONTINUOUS_ANALYSIS_FPS if _persistent_change(intent) else ANALYSIS_FPS
    total_frames_inspected = 0
    track: sam.TrackResult | None = None
    shot_start = start
    shot_end = end
    track_start: float | None = None
    track_end: float | None = None
    observed_track_start: float | None = None
    observed_track_end: float | None = None
    observed_shot_start = start
    observed_shot_end = end
    observed_frames: dict[float, dict] = {}
    with tempfile.TemporaryDirectory(prefix="fiebatt-local-range-") as temp_dir:
        iteration = 0
        while True:
            frame_dir = Path(temp_dir) / f"window-{iteration}"
            if extract_frame is ffmpeg.extract_frame:
                frame_paths, timestamps = await ffmpeg.extract_sampled_frames(
                    source,
                    start_ts=start,
                    end_ts=end,
                    fps=analysis_fps,
                    output_dir=frame_dir,
                )
            else:
                frame_count = max(2, math.floor((end - start) * analysis_fps) + 1)
                timestamps = [
                    min(end, start + index / analysis_fps)
                    for index in range(frame_count)
                ]
                frame_paths = []
                frame_dir.mkdir(parents=True, exist_ok=True)
                for index, timestamp in enumerate(timestamps):
                    path = frame_dir / f"{index:06d}.jpg"
                    await extract_frame(source, timestamp, path)
                    frame_paths.append(str(path))
            total_frames_inspected += len(timestamps)
            seed_index = min(
                range(len(timestamps)),
                key=lambda index: abs(timestamps[index] - selection.frame_ts),
            )
            shot_start, shot_end = detect_shot_span(
                frame_paths, timestamps, seed_index
            )
            try:
                track = await track_frames(
                    frame_paths,
                    seed_frame_index=seed_index,
                    bbox=selection.bbox_json,
                    seed_mask_path=seed_mask_path,
                    max_frames=len(frame_paths),
                    max_seconds=30.0,
                    include_masks=False,
                )
            except Exception as exc:
                if _persistent_change(intent):
                    raise ValueError(
                        "could not reliably track the selected subject; please choose a tighter box"
                    ) from exc
                track = sam.TrackResult(
                    tracker="bbox_fallback",
                    frames=[
                        {
                            "frame_index": index,
                            "state": "tracked",
                            "confidence": 0.0,
                        }
                        for index in range(len(frame_paths))
                    ],
                    processed_start_index=0,
                    processed_end_index=len(frame_paths) - 1,
                    warning=(
                        "video tracking unavailable; bbox fallback used with shot boundaries "
                        f"({type(exc).__name__})"
                    ),
                )
            track_start, track_end = tracked_span(
                track.frames, timestamps, seed_index
            )
            if track_start is not None:
                observed_track_start = (
                    track_start
                    if observed_track_start is None
                    else min(observed_track_start, track_start)
                )
            if track_end is not None:
                observed_track_end = (
                    track_end
                    if observed_track_end is None
                    else max(observed_track_end, track_end)
                )
            observed_shot_start = min(observed_shot_start, shot_start)
            observed_shot_end = max(observed_shot_end, shot_end)
            for frame in track.frames:
                index = int(frame.get("frame_index", -1))
                if 0 <= index < len(timestamps):
                    timestamp = timestamps[index]
                    observed_frames[timestamp] = {
                        "timestamp": timestamp,
                        "state": frame.get("state"),
                        "confidence": frame.get("confidence"),
                        "bbox": frame.get("bbox") or selection.bbox_json,
                    }
            if not _persistent_change(intent):
                break

            tolerance = 1.1 / analysis_fps
            first_ts = timestamps[0]
            last_ts = timestamps[-1]
            expand_left = (
                start > source_start + 1e-3
                and track_start is not None
                and abs(track_start - first_ts) <= tolerance
                and abs(shot_start - first_ts) <= tolerance
            )
            expand_right = (
                end < bounded_end - 1e-3
                and track_end is not None
                and abs(track_end - last_ts) <= tolerance
                and abs(shot_end - last_ts) <= tolerance
            )
            if not expand_left and not expand_right:
                break
            span = end - start
            max_window = min(
                bounded_end - source_start,
                MAX_CONTINUOUS_EDIT_SECONDS + CONTINUOUS_EDGE_PROBE_SECONDS,
            )
            if span >= max_window - 1e-3:
                if expand_left and expand_right:
                    break
                if expand_right:
                    shift = min(span, selection.frame_ts - start)
                    next_end = min(bounded_end, end + shift)
                    next_start = max(source_start, next_end - max_window)
                else:
                    shift = min(span, end - selection.frame_ts)
                    next_start = max(source_start, start - shift)
                    next_end = min(bounded_end, next_start + max_window)
            else:
                growth = min(span, max_window - span)
                sides = int(expand_left) + int(expand_right)
                per_side = growth / sides
                next_start = (
                    max(source_start, start - per_side) if expand_left else start
                )
                next_end = min(bounded_end, end + per_side) if expand_right else end
            if abs(next_start - start) < 1e-6 and abs(next_end - end) < 1e-6:
                break
            start, end = next_start, next_end
            iteration += 1

    assert track is not None
    resolution = resolve_window_from_evidence(
        intent=intent,
        seed_ts=selection.frame_ts,
        duration=project.duration,
        analysis_start=start,
        analysis_end=end,
        shot_start=observed_shot_start,
        shot_end=observed_shot_end,
        tracked_start=observed_track_start,
        tracked_end=observed_track_end,
        frames_inspected=total_frames_inspected,
        explicit_core=explicit_core,
        tracking_reached_budget=False,
        source_start=source_start,
        source_end=bounded_end,
    )
    if (
        _persistent_change(intent)
        and resolution.edit_core.duration > MAX_CONTINUOUS_EDIT_SECONDS + 0.05
    ):
        raise EditWindowLimitError(resolution.edit_core.duration)
    resolution = resolution.model_copy(
        update={
            "tracked_frames": [
                observed_frames[timestamp] for timestamp in sorted(observed_frames)
            ]
        }
    )
    if track.warning:
        resolution.warnings.append(track.warning)
    _RANGE_CACHE[key] = resolution.model_copy(deep=True)
    return resolution


def clear_local_range_cache() -> None:
    _RANGE_CACHE.clear()
