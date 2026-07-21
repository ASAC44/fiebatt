"""Authoritative ranges and EDL mutation for accepted generated media."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import time

from app.models.job import Job, Variant
from app.models.project import Project
from app.schemas.timeline import PersistedAsset, PersistedClip, PersistedEDL


@dataclass(frozen=True, slots=True)
class AcceptedGenerationRange:
    requested_start: float
    requested_end: float
    context_start: float
    context_end: float
    committed_start: float
    committed_end: float
    media_start: float
    media_end: float
    media_duration: float

    def metadata(self) -> dict[str, float]:
        return asdict(self)


def accepted_generation_range(
    job: Job,
    *,
    variant: Variant | None = None,
) -> AcceptedGenerationRange:
    if job.start_ts is None or job.end_ts is None:
        raise ValueError("generation job has no requested range")
    requested_start = float(job.start_ts)
    requested_end = float(job.end_ts)
    payload = dict(job.payload or {})
    if variant is not None:
        reviews = payload.get("candidate_reviews")
        review = reviews.get(variant.id) if isinstance(reviews, dict) else None
        if isinstance(review, dict):
            payload["selected_seams"] = review.get("selected_seams")
    raw_window = payload.get("execution_window")
    window = raw_window if isinstance(raw_window, dict) else {}
    core_start = float(window.get("core_start", requested_start))
    core_end = float(window.get("core_end", requested_end))
    context_start = min(core_start, float(window.get("context_start", core_start)))
    context_end = max(core_end, float(window.get("context_end", core_end)))
    media_start = core_start - context_start
    media_end = media_start + (core_end - core_start)
    raw_committed = payload.get("committed_timeline_range")
    committed = raw_committed if isinstance(raw_committed, dict) else {}
    committed_start = float(committed.get("start", core_start))
    committed_end = float(committed.get("end", core_end))
    raw_seams = payload.get("selected_seams")
    seams = raw_seams if isinstance(raw_seams, dict) else {}
    if seams.get("passed") is True:
        candidate_media_start = float(seams.get("media_start", media_start))
        candidate_media_end = float(seams.get("media_end", media_end))
        candidate_timeline_start = float(seams.get("timeline_start", core_start))
        candidate_timeline_end = float(seams.get("timeline_end", core_end))
        if (
            0.0 <= candidate_media_start < candidate_media_end <= context_end - context_start + 0.05
            and context_start - 0.05 <= candidate_timeline_start < candidate_timeline_end <= context_end + 0.05
        ):
            media_start = candidate_media_start
            media_end = candidate_media_end
            committed_start += candidate_timeline_start - core_start
            committed_end += candidate_timeline_end - core_end
    return AcceptedGenerationRange(
        requested_start=requested_start,
        requested_end=requested_end,
        context_start=context_start,
        context_end=context_end,
        committed_start=committed_start,
        committed_end=committed_end,
        media_start=media_start,
        media_end=media_end,
        media_duration=context_end - context_start,
    )


def resolve_committed_timeline_range(
    raw_edl: dict | None,
    *,
    target_clip_id: str | None,
    source_start: float,
    source_end: float,
) -> tuple[float, float]:
    """Map source timestamps inside one persisted clip to EDL timeline time."""
    if not raw_edl or not target_clip_id:
        return source_start, source_end
    try:
        edl = PersistedEDL.model_validate(raw_edl)
    except Exception:
        return source_start, source_end
    cursor = 0.0
    for clip in edl.clips:
        duration = max(0.0, clip.source_end - clip.source_start)
        if clip.id == target_clip_id:
            if (
                source_start < clip.source_start - 0.05
                or source_end > clip.source_end + 0.05
            ):
                raise ValueError("planned edit core is outside its target clip")
            timeline_start = cursor + max(0.0, source_start - clip.source_start)
            return timeline_start, timeline_start + (source_end - source_start)
        cursor += duration
    return source_start, source_end


def rebase_accepted_range_for_project(
    job: Job,
    project: Project,
    accepted_range: AcceptedGenerationRange,
) -> AcceptedGenerationRange:
    """Move an accepted core with its target clip or reject a stale preview."""
    payload = dict(job.payload or {})
    generated_revision = payload.get("timeline_revision")
    current_revision = int(project.timeline_revision or 0)
    if generated_revision is None or int(generated_revision) == current_revision:
        return accepted_range

    target_clip_id = payload.get("target_clip_id")
    if not target_clip_id:
        raise ValueError(
            "The timeline changed after this preview was generated. Generate a new preview from the current timeline."
        )
    old_committed = payload.get("committed_timeline_range")
    old_core = old_committed if isinstance(old_committed, dict) else {}
    old_core_start = float(old_core.get("start", job.start_ts or 0.0))
    old_core_end = float(old_core.get("end", job.end_ts or old_core_start))
    new_core_start, new_core_end = resolve_committed_timeline_range(
        project.timeline_edl,
        target_clip_id=str(target_clip_id),
        source_start=accepted_range.requested_start,
        source_end=accepted_range.requested_end,
    )
    return replace(
        accepted_range,
        committed_start=new_core_start + accepted_range.committed_start - old_core_start,
        committed_end=new_core_end + accepted_range.committed_end - old_core_end,
    )


def record_accepted_range(
    job: Job,
    *,
    segment_id: str,
    accepted_range: AcceptedGenerationRange,
) -> None:
    payload = dict(job.payload or {})
    accepted = dict(payload.get("accepted_ranges") or {})
    accepted[segment_id] = accepted_range.metadata()
    payload["accepted_ranges"] = accepted
    payload["latest_accepted_segment_id"] = segment_id
    job.payload = payload


def splice_accepted_clip_into_edl(
    edl: PersistedEDL,
    *,
    project_id: str,
    project_fps: float,
    segment_id: str,
    variant: Variant,
    accepted_range: AcceptedGenerationRange,
) -> PersistedEDL:
    """Replace one timeline interval without discarding manual EDL edits."""
    return splice_generated_clip_into_edl(
        edl,
        project_id=project_id,
        project_fps=project_fps,
        segment_id=segment_id,
        asset_id=variant.id,
        url=variant.url or "",
        timeline_start=accepted_range.committed_start,
        timeline_end=accepted_range.committed_end,
        media_start=accepted_range.media_start,
        media_end=accepted_range.media_end,
        media_duration=accepted_range.media_duration,
    )


def splice_generated_clip_into_edl(
    edl: PersistedEDL,
    *,
    project_id: str,
    project_fps: float,
    segment_id: str,
    asset_id: str,
    url: str,
    timeline_start: float,
    timeline_end: float,
    media_start: float,
    media_end: float,
    media_duration: float,
) -> PersistedEDL:
    """Replace an exact timeline interval with generated media."""
    start = timeline_start
    end = timeline_end
    if end <= start:
        raise ValueError("accepted range must have positive duration")

    generated = PersistedClip(
        id=segment_id,
        kind="generated",
        url=url,
        source_start=media_start,
        source_end=media_end,
        media_duration=media_duration,
        # Every accepted local edit is conformed with the matching original
        # audio. Keep that track audible by default; the editor's normal
        # volume control remains available for an intentional mute.
        volume=1.0,
        label="ai edit",
        project_id=project_id,
    )
    output: list[PersistedClip] = []
    cursor = 0.0
    inserted = False
    for clip in edl.clips:
        clip_duration = max(0.0, clip.source_end - clip.source_start)
        clip_start = cursor
        clip_end = cursor + clip_duration
        cursor = clip_end

        if clip_end <= start + 1e-6 or clip_start >= end - 1e-6:
            if not inserted and clip_start >= end - 1e-6:
                output.append(generated)
                inserted = True
            output.append(clip)
            continue

        before_duration = max(0.0, start - clip_start)
        after_duration = max(0.0, clip_end - end)
        if before_duration > 1e-3:
            output.append(
                clip.model_copy(
                    update={"source_end": clip.source_start + before_duration}
                )
            )
        if not inserted:
            output.append(generated)
            inserted = True
        if after_duration > 1e-3:
            output.append(
                clip.model_copy(
                    update={
                        "id": f"{clip.id}:after:{segment_id}",
                        "source_start": clip.source_end - after_duration,
                    }
                )
            )

    if not inserted:
        output.append(generated)

    assets = [asset for asset in edl.sources if asset.id != asset_id]
    assets.append(
        PersistedAsset(
            id=asset_id,
            kind="generated",
            url=url,
            duration=media_duration,
            fps=project_fps,
            project_id=project_id,
            label="ai edit",
        )
    )
    return PersistedEDL(clips=output, sources=assets, updated_at=time.time())


def update_project_edl_for_acceptance(
    project: Project,
    *,
    segment_id: str,
    variant: Variant,
    accepted_range: AcceptedGenerationRange,
) -> None:
    project.timeline_revision = int(project.timeline_revision or 0) + 1
    raw = project.timeline_edl
    if not raw:
        return
    try:
        edl = PersistedEDL.model_validate(raw)
    except Exception:
        project.timeline_edl = None
        return
    project.timeline_edl = splice_accepted_clip_into_edl(
        edl,
        project_id=project.id,
        project_fps=project.fps,
        segment_id=segment_id,
        variant=variant,
        accepted_range=accepted_range,
    ).model_dump(mode="json")
