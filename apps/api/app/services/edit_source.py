"""Resolve the exact media revision a selection was made from."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.models.project import Project
from app.models.selection import SelectionArtifact
from app.schemas.timeline import PersistedEDL
from app.services import storage


@dataclass(frozen=True)
class EditSource:
    url: str
    duration: float
    target_clip_id: str | None = None
    source_start: float = 0.0
    source_end: float | None = None

    @property
    def active_end(self) -> float:
        return self.duration if self.source_end is None else self.source_end


def source_for_selection(project: Project, selection: SelectionArtifact) -> EditSource:
    """Return a live source revision; removed/replaced clips are stale."""
    if selection.source_revision == project.video_url:
        return EditSource(url=project.video_url, duration=float(project.duration))
    if not project.timeline_edl:
        raise ValueError("selection is stale for the current timeline")
    try:
        edl = PersistedEDL.model_validate(project.timeline_edl)
    except Exception as exc:
        raise ValueError("saved timeline is invalid") from exc
    for clip in edl.clips:
        if clip.kind == "generated" and clip.url == selection.source_revision:
            return EditSource(
                url=clip.url,
                duration=float(clip.media_duration),
                target_clip_id=clip.id,
                source_start=float(clip.source_start),
                source_end=float(clip.source_end),
            )
    raise ValueError("selection is stale for the current timeline")


def source_for_timeline_clip(project: Project, target_clip_id: str | None) -> EditSource:
    """Resolve a UI clip. No id means the immutable uploaded source."""
    if not target_clip_id:
        return EditSource(url=project.video_url, duration=float(project.duration))
    # Fresh projects have no persisted EDL yet. Their visible clip is the
    # original upload, so the client clip id is only advisory at this point.
    if not project.timeline_edl:
        return EditSource(url=project.video_url, duration=float(project.duration))
    try:
        edl = PersistedEDL.model_validate(project.timeline_edl)
    except Exception as exc:
        raise ValueError("saved timeline is invalid") from exc
    for clip in edl.clips:
        if clip.id != target_clip_id:
            continue
        if clip.kind == "source":
            return EditSource(
                url=project.video_url,
                duration=float(project.duration),
                target_clip_id=clip.id,
                source_start=float(clip.source_start),
                source_end=float(clip.source_end),
            )
        return EditSource(
            url=clip.url,
            duration=float(clip.media_duration),
            target_clip_id=clip.id,
            source_start=float(clip.source_start),
            source_end=float(clip.source_end),
        )
    raise ValueError("selected timeline clip is no longer available")


async def materialize_edit_source(project: Project, source: EditSource) -> Path:
    if source.url == project.video_url:
        return await storage.materialize_source(project.video_path, project.video_url)
    return await storage.path_from_url(source.url)
