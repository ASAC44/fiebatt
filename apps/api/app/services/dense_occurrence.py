"""Frame-accurate occurrence refinement from coarse identity candidates."""
from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.services import sam
from app.ai.services.clip_search import batch_embed_images, cosine_similarity
from app.models.entity import (
    EntityAppearance,
    OccurrenceCandidate,
    OccurrenceTrack,
)
from app.services import ffmpeg
from app.services import cpu_tracking
from app.services.local_range import detect_shot_span, tracked_span


DENSE_TRACK_FPS = 4.0
DENSE_TRACK_RADIUS_SECONDS = 5.0
DENSE_MAX_FRAMES = 48
MIN_LOCALIZATION_SIMILARITY = 0.60
MIN_TRACK_CONFIDENCE = 0.60


@dataclass(frozen=True, slots=True)
class DenseTrackEvidence:
    candidate_id: str
    passed: bool
    reason: str
    seed_ts: float
    start_ts: float
    end_ts: float
    confidence: float
    tracker: str
    frames: tuple[dict, ...]


def _proposal_boxes() -> tuple[dict[str, float], ...]:
    boxes: list[dict[str, float]] = []
    for width, height in (
        (0.25, 0.25),
        (0.4, 0.4),
        (0.6, 0.6),
        (0.25, 0.6),
        (0.35, 0.75),
        (0.6, 0.3),
        (0.75, 0.4),
    ):
        positions = (0.0, 0.5, 1.0)
        for x_pos in positions:
            for y_pos in positions:
                boxes.append(
                    {
                        "x": (1.0 - width) * x_pos,
                        "y": (1.0 - height) * y_pos,
                        "w": width,
                        "h": height,
                    }
                )
    return tuple(boxes)


async def locate_reference_bbox(
    frame_path: str,
    reference_crop_path: str,
    *,
    embed_images: Callable[[list[str]], Awaitable[list[list[float]]]] = batch_embed_images,
) -> tuple[dict[str, float] | None, float]:
    """Locate the reference identity using batched CLIP region proposals."""
    frame = cv2.imread(frame_path, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("failed to read occurrence seed frame")
    height, width = frame.shape[:2]
    boxes = _proposal_boxes()
    with tempfile.TemporaryDirectory(prefix="fiebatt-locate-") as temp_dir:
        crop_paths: list[str] = []
        for index, box in enumerate(boxes):
            left = round(box["x"] * width)
            top = round(box["y"] * height)
            right = max(left + 1, round((box["x"] + box["w"]) * width))
            bottom = max(top + 1, round((box["y"] + box["h"]) * height))
            crop_path = Path(temp_dir) / f"{index:03d}.jpg"
            if not cv2.imwrite(str(crop_path), frame[top:bottom, left:right]):
                raise ValueError("failed to write occurrence proposal")
            crop_paths.append(str(crop_path))
        embeddings = await embed_images([reference_crop_path, *crop_paths])

    if len(embeddings) != len(crop_paths) + 1:
        raise ValueError("CLIP locator returned an unexpected embedding count")
    reference = embeddings[0]
    scores = [cosine_similarity(reference, embedding) for embedding in embeddings[1:]]
    best_index = int(np.argmax(scores)) if scores else -1
    best_score = float(scores[best_index]) if best_index >= 0 else 0.0
    if best_index < 0 or best_score < MIN_LOCALIZATION_SIMILARITY:
        return None, best_score
    return dict(boxes[best_index]), best_score


async def refine_occurrence_candidate(
    *,
    source_path: str | Path,
    project_duration: float,
    candidate: OccurrenceCandidate,
    reference_crop_path: str,
    extract_frame: Callable[[str | Path, float, str | Path], Awaitable[Path]] = ffmpeg.extract_frame,
    locate_bbox: Callable[..., Awaitable[tuple[dict[str, float] | None, float]]] = locate_reference_bbox,
    track_frames: Callable[..., Awaitable[sam.TrackResult]] = cpu_tracking.track_frames,
) -> DenseTrackEvidence:
    start = max(0.0, candidate.keyframe_ts - DENSE_TRACK_RADIUS_SECONDS)
    end = min(project_duration, candidate.keyframe_ts + DENSE_TRACK_RADIUS_SECONDS)
    frame_count = min(
        DENSE_MAX_FRAMES,
        max(3, math.floor((end - start) * DENSE_TRACK_FPS) + 1),
    )
    timestamps = [
        min(end, start + index / DENSE_TRACK_FPS) for index in range(frame_count)
    ]
    seed_index = min(
        range(len(timestamps)),
        key=lambda index: abs(timestamps[index] - candidate.keyframe_ts),
    )

    with tempfile.TemporaryDirectory(prefix="fiebatt-occurrence-track-") as temp_dir:
        paths: list[str] = []
        for index, timestamp in enumerate(timestamps):
            path = Path(temp_dir) / f"{index:06d}.jpg"
            await extract_frame(source_path, timestamp, path)
            paths.append(str(path))
        shot_start, shot_end = detect_shot_span(paths, timestamps, seed_index)
        bbox, localization_score = await locate_bbox(
            paths[seed_index], reference_crop_path
        )
        if bbox is None:
            return DenseTrackEvidence(
                candidate.id,
                False,
                "identity localization confidence is too low",
                candidate.keyframe_ts,
                candidate.keyframe_ts,
                candidate.keyframe_ts,
                localization_score,
                "clip_locator",
                (),
            )
        track = await track_frames(
            paths,
            seed_frame_index=seed_index,
            bbox=bbox,
            max_frames=len(paths),
            max_seconds=30.0,
            include_masks=False,
        )

    if track.tracker not in {"sam2_video", "opencv_mil"} or track.cancelled:
        return DenseTrackEvidence(
            candidate.id,
            False,
            "subject tracking is unavailable or incomplete",
            candidate.keyframe_ts,
            candidate.keyframe_ts,
            candidate.keyframe_ts,
            0.0,
            track.tracker,
            tuple(track.frames),
        )
    track_start, track_end = tracked_span(track.frames, timestamps, seed_index)
    if track_start is None or track_end is None:
        return DenseTrackEvidence(
            candidate.id,
            False,
            "target was lost at the candidate frame",
            candidate.keyframe_ts,
            candidate.keyframe_ts,
            candidate.keyframe_ts,
            0.0,
            track.tracker,
            tuple(track.frames),
        )
    start_ts = max(shot_start, track_start)
    end_ts = min(shot_end, track_end + 1.0 / DENSE_TRACK_FPS)
    relevant = [
        frame
        for frame in track.frames
        if frame.get("state") == "tracked"
        and start_ts - 1e-6
        <= timestamps[int(frame["frame_index"])]
        <= end_ts + 1e-6
    ]
    tracking_confidence = float(
        np.mean([float(frame.get("confidence") or 0.0) for frame in relevant])
    ) if relevant else 0.0
    confidence = min(localization_score, tracking_confidence)
    if end_ts <= start_ts or confidence < MIN_TRACK_CONFIDENCE:
        return DenseTrackEvidence(
            candidate.id,
            False,
            "dense track confidence is too low",
            candidate.keyframe_ts,
            start_ts,
            end_ts,
            confidence,
            track.tracker,
            tuple(track.frames),
        )
    timestamped_frames = tuple(
        {
            "timestamp": timestamps[int(frame["frame_index"])],
            "bbox": frame.get("bbox"),
            "confidence": frame.get("confidence"),
            "state": frame.get("state"),
        }
        for frame in track.frames
        if 0 <= int(frame.get("frame_index", -1)) < len(timestamps)
    )
    return DenseTrackEvidence(
        candidate.id,
        True,
        "dense identity track confirmed",
        candidate.keyframe_ts,
        start_ts,
        end_ts,
        confidence,
        track.tracker,
        timestamped_frames,
    )


def merge_confirmed_ranges(
    tracks: list[DenseTrackEvidence],
) -> list[tuple[float, float, float]]:
    confirmed = sorted(
        (track for track in tracks if track.passed), key=lambda track: track.start_ts
    )
    merged: list[tuple[float, float, float]] = []
    for track in confirmed:
        if not merged or track.start_ts > merged[-1][1] + 1.0 / DENSE_TRACK_FPS:
            merged.append((track.start_ts, track.end_ts, track.confidence))
            continue
        start, end, confidence = merged[-1]
        merged[-1] = (
            start,
            max(end, track.end_ts),
            max(confidence, track.confidence),
        )
    return merged


async def persist_dense_tracks(
    db: AsyncSession,
    *,
    entity_id: str,
    source_revision: str,
    tracks: list[DenseTrackEvidence],
) -> tuple[int, int]:
    candidate_ids = [track.candidate_id for track in tracks]
    candidates = (
        await db.execute(
            select(OccurrenceCandidate).where(OccurrenceCandidate.id.in_(candidate_ids))
        )
    ).scalars().all() if candidate_ids else []
    by_id = {candidate.id: candidate for candidate in candidates}
    existing = (
        await db.execute(
            select(OccurrenceTrack).where(OccurrenceTrack.candidate_id.in_(candidate_ids))
        )
    ).scalars().all() if candidate_ids else []
    existing_by_candidate = {track.candidate_id: track for track in existing}
    for evidence in tracks:
        candidate = by_id.get(evidence.candidate_id)
        if candidate is not None:
            candidate.status = "confirmed" if evidence.passed else "rejected"
            candidate.evidence_json = {
                **(candidate.evidence_json or {}),
                "dense_reason": evidence.reason,
                "dense_confidence": evidence.confidence,
            }
        row = existing_by_candidate.get(evidence.candidate_id)
        values = {
            "source_revision": source_revision,
            "seed_ts": evidence.seed_ts,
            "start_ts": evidence.start_ts,
            "end_ts": evidence.end_ts,
            "confidence": evidence.confidence,
            "tracker": evidence.tracker,
            "frames_json": list(evidence.frames),
            "status": "confirmed" if evidence.passed else "rejected",
            "reason": evidence.reason,
        }
        if row is None:
            row = OccurrenceTrack(
                entity_id=entity_id,
                candidate_id=evidence.candidate_id,
                **values,
            )
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)

    existing_appearances = list((
        await db.execute(
            select(EntityAppearance).where(EntityAppearance.entity_id == entity_id)
        )
    ).scalars().all())
    ranges = merge_confirmed_ranges(tracks)
    for start_ts, end_ts, confidence in ranges:
        matching = next(
            (
                appearance
                for appearance in existing_appearances
                if appearance.start_ts < end_ts and appearance.end_ts > start_ts
            ),
            None,
        )
        if matching is not None:
            matching.start_ts = min(matching.start_ts, start_ts)
            matching.end_ts = max(matching.end_ts, end_ts)
            matching.confidence = max(matching.confidence, confidence)
        else:
            appearance = EntityAppearance(
                entity_id=entity_id,
                segment_id=None,
                start_ts=start_ts,
                end_ts=end_ts,
                confidence=confidence,
            )
            db.add(appearance)
            existing_appearances.append(appearance)
    await db.flush()
    return len(ranges), sum(1 for track in tracks if not track.passed)
