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

from app.models.project import Project
from app.models.segment import Segment


Source = Literal["original", "generated"]


@dataclass(slots=True, frozen=True)
class TimelineItem:
    id: str | None
    start_ts: float
    end_ts: float
    source: Source
    url: str
    audio: bool

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
            )
            .order_by(Segment.start_ts)
        )
    ).scalars().all()

    items: list[TimelineItem] = []
    cursor = 0.0
    for seg in rows:
        if seg.start_ts > cursor + 1e-3:
            items.append(
                TimelineItem(None, cursor, seg.start_ts, "original", proj.video_url, True)
            )
        items.append(
            TimelineItem(seg.id, seg.start_ts, seg.end_ts, seg.source, seg.url, seg.source == "original")
        )
        cursor = seg.end_ts

    if cursor < proj.duration - 1e-3:
        items.append(
            TimelineItem(None, cursor, proj.duration, "original", proj.video_url, True)
        )

    if not items:
        items.append(
            TimelineItem(None, 0.0, proj.duration, "original", proj.video_url, True)
        )

    return items
