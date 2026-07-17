"""Provider-aware chunk planning for long, confirmed entity occurrences."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.ai.services.provider_capabilities import VIDEO_PROVIDER_CAPABILITIES


HANDLE_SECONDS = 0.75
MIN_CORE_SECONDS = 2.0
MIN_TOTAL_SECONDS = {"wan": 2.0, "happyhorse": 3.0}


@dataclass(frozen=True, slots=True)
class SplitEvidence:
    timestamp: float
    kind: str
    stability: float = 0.0


@dataclass(frozen=True, slots=True)
class PlannedGlobalChunk:
    index: int
    edit_start: float
    edit_end: float
    context_start: float
    context_end: float
    provider: str
    split_reason: str

    @property
    def core_duration(self) -> float:
        return self.edit_end - self.edit_start

    @property
    def context_duration(self) -> float:
        return self.context_end - self.context_start


def split_evidence_from_track_frames(frames: Iterable[dict]) -> list[SplitEvidence]:
    ordered = sorted(frames, key=lambda frame: float(frame.get("timestamp") or 0.0))
    evidence: list[SplitEvidence] = []
    previous_center: tuple[float, float] | None = None
    for frame in ordered:
        timestamp = float(frame.get("timestamp") or 0.0)
        state = str(frame.get("state") or "lost")
        bbox = frame.get("bbox")
        if state in {"lost", "occluded"}:
            evidence.append(SplitEvidence(timestamp, "occlusion", 1.0))
            previous_center = None
            continue
        if not isinstance(bbox, dict):
            continue
        center = (
            float(bbox.get("x", 0.0)) + float(bbox.get("w", 0.0)) / 2,
            float(bbox.get("y", 0.0)) + float(bbox.get("h", 0.0)) / 2,
        )
        if previous_center is not None:
            motion = (
                (center[0] - previous_center[0]) ** 2
                + (center[1] - previous_center[1]) ** 2
            ) ** 0.5
            evidence.append(
                SplitEvidence(timestamp, "stable_motion", 1.0 / (1.0 + 40.0 * motion))
            )
        previous_center = center
    return evidence


def _provider_for_occurrence(requested: str, total_context_seconds: float) -> str:
    if requested != "auto":
        capabilities = VIDEO_PROVIDER_CAPABILITIES.get(requested)
        if capabilities is None or not capabilities.source_video_edit:
            raise ValueError(
                f"{requested} cannot preserve source-video motion for global edits"
            )
        return requested
    if total_context_seconds <= VIDEO_PROVIDER_CAPABILITIES["wan"].max_total_duration + 1e-6:
        return "wan"
    return "happyhorse"


def _split_priority(kind: str) -> float:
    return {
        "shot_cut": 4.0,
        "occlusion": 3.0,
        "stable_motion": 1.0,
    }.get(kind, 0.0)


def _choose_split(
    target: float,
    *,
    lower: float,
    upper: float,
    evidence: list[SplitEvidence],
) -> tuple[float, str]:
    candidates = [point for point in evidence if lower <= point.timestamp <= upper]
    if not candidates:
        return min(max(target, lower), upper), "provider_limit"
    span = max(upper - lower, 1e-6)
    best = max(
        candidates,
        key=lambda point: (
            _split_priority(point.kind)
            + max(0.0, min(1.0, point.stability))
            - 2.0 * abs(point.timestamp - target) / span
        ),
    )
    return best.timestamp, best.kind


def plan_occurrence_chunks(
    *,
    occurrence_start: float,
    occurrence_end: float,
    project_duration: float,
    requested_provider: str = "auto",
    split_evidence: list[SplitEvidence] | None = None,
    source_start: float = 0.0,
    source_end: float | None = None,
) -> list[PlannedGlobalChunk]:
    source_upper = (
        project_duration if source_end is None else min(project_duration, source_end)
    )
    if occurrence_end <= occurrence_start:
        raise ValueError("occurrence must have positive duration")
    if source_start < 0 or source_upper <= source_start:
        raise ValueError("source range must have positive duration")
    if occurrence_start < source_start - 0.05 or occurrence_end > source_upper + 0.05:
        raise ValueError("occurrence is outside the source video")
    full_context_start = max(source_start, occurrence_start - HANDLE_SECONDS)
    full_context_end = min(source_upper, occurrence_end + HANDLE_SECONDS)
    provider = _provider_for_occurrence(
        requested_provider, full_context_end - full_context_start
    )
    max_context = float(VIDEO_PROVIDER_CAPABILITIES[provider].max_total_duration)
    if max_context - 2 * HANDLE_SECONDS < MIN_CORE_SECONDS:
        raise ValueError(f"{provider} cannot fit core plus continuity handles")

    evidence = split_evidence or []
    core_ranges: list[tuple[float, float, str]] = []
    cursor = occurrence_start
    while (
        min(source_upper, occurrence_end + HANDLE_SECONDS)
        - max(source_start, cursor - HANDLE_SECONDS)
        > max_context + 1e-6
    ):
        context_start = max(source_start, cursor - HANDLE_SECONDS)
        target = context_start + max_context - HANDLE_SECONDS
        lower = cursor + MIN_CORE_SECONDS
        upper = min(
            target,
            occurrence_end - MIN_CORE_SECONDS,
        )
        if upper < lower:
            raise ValueError("occurrence cannot be split within provider limits")
        split, reason = _choose_split(
            target,
            lower=lower,
            upper=upper,
            evidence=evidence,
        )
        core_ranges.append((cursor, split, reason))
        cursor = split
    core_ranges.append((cursor, occurrence_end, "occurrence_end"))

    chunks: list[PlannedGlobalChunk] = []
    minimum_total = MIN_TOTAL_SECONDS.get(provider, 0.0)
    for index, (start, end, reason) in enumerate(core_ranges):
        context_start = max(source_start, start - HANDLE_SECONDS)
        context_end = min(source_upper, end + HANDLE_SECONDS)
        missing = minimum_total - (context_end - context_start)
        if missing > 0:
            grow_left = min(context_start - source_start, missing / 2)
            context_start -= grow_left
            missing -= grow_left
            grow_right = min(source_upper - context_end, missing)
            context_end += grow_right
            missing -= grow_right
            context_start = max(source_start, context_start - missing)
        chunks.append(
            PlannedGlobalChunk(
                index=index,
                edit_start=start,
                edit_end=end,
                context_start=context_start,
                context_end=context_end,
                provider=provider,
                split_reason=reason,
            )
        )
    for chunk in chunks:
        if len(chunks) > 1 and chunk.core_duration < MIN_CORE_SECONDS - 0.05:
            raise ValueError("provider split produced a core shorter than two seconds")
        if chunk.context_duration < minimum_total - 0.05:
            raise ValueError(f"source video is too short for {provider}")
        if chunk.context_duration > max_context + 1e-3:
            raise ValueError("provider split exceeds total duration limit")
    for left, right in zip(chunks, chunks[1:], strict=False):
        if abs(left.edit_end - right.edit_start) > 1e-6:
            raise ValueError("chunk cores must cover the occurrence exactly once")
        if left.context_end < right.context_start:
            raise ValueError("adjacent chunk contexts must overlap")
    return chunks
