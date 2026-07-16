"""Timeline construction helper.

Produces the ordered list of spans that represents a project's current
timeline: generated segments from the DB + implicit "original" segments
filling the gaps between them. Shared between the /api/timeline route
(which returns these as-is to the frontend) and the export worker (which
renders them into a single MP4).

Keeping this logic in one place so the two consumers don't drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import Variant
from app.models.project import Project
from app.models.segment import Segment
from app.services.accepted_generation import accepted_generation_range


Source = Literal["original", "generated"]


@dataclass(slots=True, frozen=True)
class TimelineItem:
    id: str | None
    start_ts: float
    end_ts: float
    source: Source
    url: str
    audio: bool
    media_start_ts: float
    media_end_ts: float
    media_duration: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)


async def build_timeline(db: AsyncSession, proj: Project) -> list[TimelineItem]:
    """Walk the Segment table for a project and produce the ordered span list."""
    rows = (
        await db.execute(
            select(Segment)
            .where(
                Segment.project_id == proj.id,
                Segment.active == True,  # noqa: E712
                Segment.source == "generated",
            )
            .options(selectinload(Segment.variant).selectinload(Variant.job))
            .order_by(Segment.start_ts, Segment.source)
        )
    ).scalars().all()

    items: list[TimelineItem] = []
    cursor = 0.0
    for seg in rows:
        effective_end = min(seg.end_ts, proj.duration)
        if effective_end <= cursor + 1e-3:
            continue

        effective_start = max(seg.start_ts, cursor)

        if effective_start > cursor + 1e-3:
            items.append(
                TimelineItem(
                    None,
                    cursor,
                    effective_start,
                    "original",
                    proj.video_url,
                    True,
                    cursor,
                    effective_start,
                    proj.duration,
                )
            )

        variant = getattr(seg, "variant", None)
        source_job = getattr(variant, "job", None) if variant is not None else None
        if source_job is not None:
            accepted = accepted_generation_range(source_job)
            media_start = accepted.media_start + (effective_start - seg.start_ts)
            media_end = media_start + (effective_end - effective_start)
            media_duration = accepted.media_duration
        else:
            media_start = max(0.0, effective_start - seg.start_ts)
            media_end = media_start + (effective_end - effective_start)
            media_duration = media_end
        items.append(
            TimelineItem(
                seg.id,
                effective_start,
                effective_end,
                seg.source,
                seg.url,
                seg.source == "original",
                media_start,
                media_end,
                media_duration,
            )
        )
        cursor = effective_end

    if cursor < proj.duration - 1e-3:
        items.append(
            TimelineItem(
                None,
                cursor,
                proj.duration,
                "original",
                proj.video_url,
                True,
                cursor,
                proj.duration,
                proj.duration,
            )
        )

    if not items:
        items.append(
            TimelineItem(
                None,
                0.0,
                proj.duration,
                "original",
                proj.video_url,
                True,
                0.0,
                proj.duration,
                proj.duration,
            )
        )

    return items
