"""Guard global source-time occurrences against incompatible timeline edits."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.segment import Segment
from app.schemas.timeline import PersistedEDL


def timeline_revision(project: Project) -> str:
    payload = {
        "video_url": project.video_url,
        "timeline_edl": project.timeline_edl,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def ensure_source_aligned_timeline(
    db: AsyncSession,
    project: Project,
) -> None:
    """Allow duration-preserving edits; reject trims, deletes, and reordering."""
    if not project.timeline_edl:
        return
    try:
        edl = PersistedEDL.model_validate(project.timeline_edl)
    except Exception as exc:
        raise ValueError("saved timeline is invalid") from exc
    segment_rows = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == project.id,
                Segment.active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    segments = {segment.id: segment for segment in segment_rows}
    cursor = 0.0
    for clip in edl.clips:
        duration = clip.source_end - clip.source_start
        if duration <= 0:
            raise ValueError("saved timeline contains an empty clip")
        if clip.project_id not in {None, project.id}:
            raise ValueError("global edits do not support clips from another project")
        if clip.kind == "source":
            if abs(clip.source_start - cursor) > 0.05:
                raise ValueError(
                    "global edits currently require source-time order; undo trims, deletes, or reordering"
                )
        else:
            segment = segments.get(clip.id)
            if (
                segment is None
                or abs(segment.start_ts - cursor) > 0.05
                or abs((segment.end_ts - segment.start_ts) - duration) > 0.05
            ):
                raise ValueError(
                    "global edits currently require generated clips to keep their source-time positions"
                )
        cursor += duration
    if abs(cursor - float(project.duration)) > 0.05:
        raise ValueError(
            "global edits currently require a duration-preserving timeline"
        )
