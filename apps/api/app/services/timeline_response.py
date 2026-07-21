"""Build the one authoritative timeline payload returned after mutations."""
from __future__ import annotations

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.schemas.timeline import PersistedEDL, TimelineOut, TimelineSegment
from app.services import storage
from app.services.timeline_builder import build_timeline


def rehydrate_edl(raw: dict | None) -> PersistedEDL | None:
    if not raw:
        return None
    try:
        edl = PersistedEDL.model_validate(raw)
    except Exception:
        return None
    for clip in edl.clips:
        clip.url = storage.normalize_url_like(clip.url, fallback=clip.url)
    for asset in edl.sources:
        asset.url = storage.normalize_url_like(asset.url, fallback=asset.url)
    return edl


async def build_timeline_response(
    db: AsyncSession,
    project: Project,
) -> TimelineOut:
    items = await build_timeline(db, project)
    return TimelineOut(
        project_id=project.id,
        duration=project.duration,
        revision=project.timeline_revision,
        segments=[
            TimelineSegment(
                start_ts=item.start_ts,
                end_ts=item.end_ts,
                source=item.source,
                url=storage.normalize_url_like(item.url, fallback=item.url),
                audio=item.audio,
                segment_id=item.id,
                media_start_ts=item.media_start_ts,
                media_end_ts=item.media_end_ts,
                media_duration=item.media_duration,
            )
            for item in items
        ],
        edl=rehydrate_edl(cast(dict | None, project.timeline_edl)),
    )
