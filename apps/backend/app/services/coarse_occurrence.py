"""Cost-aware coarse retrieval for explicit global entity searches."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.services.clip_search import search_keyframes_by_similarity
from app.models.entity import OccurrenceCandidate


MIN_CANDIDATE_CONFIDENCE = 0.55
CLIP_RECALL_THRESHOLD = 0.70
DUPLICATE_GAP_SECONDS = 1.5
COARSE_SEARCH_VERSION = "coarse-v1"


@dataclass(frozen=True, slots=True)
class CoarseCandidateInput:
    source_revision: str
    keyframe_ts: float
    start_ts: float
    end_ts: float
    keyframe_url: str | None
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def cache_key(self) -> str:
        identity = str(self.evidence.get("identity_fingerprint") or "unknown")
        raw = (
            f"{COARSE_SEARCH_VERSION}|{self.source_revision}|{identity}|"
            f"{self.keyframe_ts:.3f}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CoarseSearchResult:
    candidates: tuple[CoarseCandidateInput, ...]
    analysis_mode: str
    frames_inspected: int


def source_revision_cache_dir(
    storage_path: Path,
    *,
    project_id: str,
    source_revision: str,
) -> Path:
    revision = hashlib.sha256(source_revision.encode()).hexdigest()[:12]
    return storage_path / "keyframes" / project_id / revision


def identity_fingerprint(identity: dict[str, Any]) -> str:
    stable = {
        "description": identity.get("description"),
        "category": identity.get("category"),
        "attributes": identity.get("attributes") or {},
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def normalize_coarse_hits(
    hits: list[dict[str, Any]],
    *,
    identity: dict[str, Any],
    source_revision: str,
    duration: float,
    exclude_start: float | None = None,
    exclude_end: float | None = None,
) -> tuple[CoarseCandidateInput, ...]:
    """Reject weak/invalid hits and merge adjacent keyframes into one candidate."""
    fingerprint = identity_fingerprint(identity)
    accepted: list[CoarseCandidateInput] = []
    for hit in hits:
        confidence = float(hit.get("confidence") or 0.0)
        if confidence < MIN_CANDIDATE_CONFIDENCE:
            continue
        keyframe_ts = max(
            0.0,
            min(duration, float(hit.get("keyframe_ts", hit.get("start_ts", 0.0)))),
        )
        start_ts = max(0.0, min(keyframe_ts, float(hit.get("start_ts", keyframe_ts))))
        end_ts = min(
            duration,
            max(keyframe_ts + 1e-3, float(hit.get("end_ts", keyframe_ts + 1.0))),
        )
        if end_ts <= start_ts:
            continue
        if (
            exclude_start is not None
            and exclude_end is not None
            and start_ts < exclude_end
            and end_ts > exclude_start
        ):
            continue
        accepted.append(
            CoarseCandidateInput(
                source_revision=source_revision,
                keyframe_ts=keyframe_ts,
                start_ts=start_ts,
                end_ts=end_ts,
                keyframe_url=hit.get("keyframe_url"),
                confidence=confidence,
                evidence={
                    "identity_fingerprint": fingerprint,
                    "vlm_confidence": confidence,
                    "clip_similarity": hit.get("clip_similarity"),
                    "analysis_mode": hit.get("analysis_mode", "vlm"),
                    "coarse_hit_count": 1,
                },
            )
        )

    accepted.sort(key=lambda candidate: candidate.keyframe_ts)
    merged: list[CoarseCandidateInput] = []
    for candidate in accepted:
        if not merged or candidate.keyframe_ts - merged[-1].keyframe_ts > DUPLICATE_GAP_SECONDS:
            merged.append(candidate)
            continue
        previous = merged[-1]
        strongest = candidate if candidate.confidence > previous.confidence else previous
        merged[-1] = CoarseCandidateInput(
            source_revision=source_revision,
            keyframe_ts=strongest.keyframe_ts,
            start_ts=min(previous.start_ts, candidate.start_ts),
            end_ts=max(previous.end_ts, candidate.end_ts),
            keyframe_url=strongest.keyframe_url,
            confidence=max(previous.confidence, candidate.confidence),
            evidence={
                **strongest.evidence,
                "coarse_hit_count": int(previous.evidence.get("coarse_hit_count", 1))
                + int(candidate.evidence.get("coarse_hit_count", 1)),
            },
        )
    return tuple(merged)


async def search_coarse_occurrences(
    *,
    identity: dict[str, Any],
    reference_crop_path: str | None,
    keyframe_paths: list[str],
    source_revision: str,
    duration: float,
    exclude_start: float | None,
    exclude_end: float | None,
    vlm_search: Callable[[dict[str, Any], list[str]], Awaitable[list[dict[str, Any]]]],
    clip_search: Callable[..., Awaitable[list[dict[str, Any]]]] = search_keyframes_by_similarity,
) -> CoarseSearchResult:
    """Use CLIP for recall, then spend VLM tokens only on likely keyframes."""
    selected_paths = list(keyframe_paths)
    clip_scores: dict[str, float] = {}
    analysis_mode = "vlm"
    if reference_crop_path and keyframe_paths:
        try:
            clip_hits = await clip_search(
                reference_crop_path,
                keyframe_paths,
                threshold=CLIP_RECALL_THRESHOLD,
            )
            selected_paths = []
            for hit in clip_hits:
                index = int(hit.get("keyframe_index", -1))
                confidence = float(hit.get("confidence") or 0.0)
                if hit.get("found") and 0 <= index < len(keyframe_paths):
                    path = keyframe_paths[index]
                    selected_paths.append(path)
                    clip_scores[path] = confidence
            if selected_paths:
                analysis_mode = "clip_then_vlm"
            else:
                selected_paths = list(keyframe_paths)
        except Exception:
            selected_paths = list(keyframe_paths)

    vlm_hits = await vlm_search(identity, selected_paths)
    index_by_path = {str(path): index for index, path in enumerate(keyframe_paths)}
    normalized_hits: list[dict[str, Any]] = []
    for hit in vlm_hits:
        path = str(hit.get("keyframe_url") or "")
        index = index_by_path.get(path)
        if index is None:
            fallback_index = round(float(hit.get("start_ts") or 0.0))
            index = max(0, min(len(keyframe_paths) - 1, fallback_index))
            path = keyframe_paths[index] if keyframe_paths else ""
        keyframe_ts = float(index)
        normalized_hits.append(
            {
                **hit,
                "keyframe_ts": keyframe_ts,
                "start_ts": keyframe_ts,
                "end_ts": min(duration, keyframe_ts + 1.0),
                "keyframe_url": path,
                "clip_similarity": clip_scores.get(path),
                "analysis_mode": analysis_mode,
            }
        )

    candidates = normalize_coarse_hits(
        normalized_hits,
        identity=identity,
        source_revision=source_revision,
        duration=duration,
        exclude_start=exclude_start,
        exclude_end=exclude_end,
    )
    return CoarseSearchResult(
        candidates=candidates,
        analysis_mode=analysis_mode,
        frames_inspected=len(selected_paths),
    )


async def persist_coarse_candidates(
    db: AsyncSession,
    *,
    entity_id: str,
    candidates: tuple[CoarseCandidateInput, ...],
) -> tuple[list[OccurrenceCandidate], int]:
    """Insert only cache misses and return all matching rows plus hit count."""
    keys = [candidate.cache_key for candidate in candidates]
    existing = (
        await db.execute(
            select(OccurrenceCandidate).where(
                OccurrenceCandidate.entity_id == entity_id,
                OccurrenceCandidate.cache_key.in_(keys),
            )
        )
    ).scalars().all() if keys else []
    by_key = {row.cache_key: row for row in existing}
    cache_hits = len(existing)
    rows: list[OccurrenceCandidate] = []
    for candidate in candidates:
        row = by_key.get(candidate.cache_key)
        if row is None:
            row = OccurrenceCandidate(
                entity_id=entity_id,
                source_revision=candidate.source_revision,
                cache_key=candidate.cache_key,
                keyframe_ts=candidate.keyframe_ts,
                start_ts=candidate.start_ts,
                end_ts=candidate.end_ts,
                keyframe_url=candidate.keyframe_url,
                confidence=candidate.confidence,
                evidence_json=candidate.evidence,
                status="candidate",
            )
            db.add(row)
        rows.append(row)
    await db.flush()
    return rows, cache_hits
