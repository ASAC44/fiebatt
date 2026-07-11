from __future__ import annotations

import pytest

from app.models.project import Project
from app.models.segment import Segment
from app.services.timeline_builder import build_timeline


class _FakeScalarResult:
    def __init__(self, rows: list[Segment]):
        self._rows = rows

    def all(self) -> list[Segment]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[Segment]):
        self._rows = rows

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Segment]):
        self._rows = rows

    async def execute(self, _stmt):
        rows = [
            row
            for row in self._rows
            if row.active and row.source == "generated"
        ]
        rows.sort(key=lambda row: (row.start_ts, row.source))
        return _FakeResult(rows)


@pytest.mark.asyncio
async def test_build_timeline_late_generated_segment_not_blocked_by_original_row():
    project = Project(
        id="project-1",
        session_id="session-1",
        video_path="/tmp/source.mp4",
        video_url="/media/source.mp4",
        duration=12.0,
        fps=24.0,
        width=1280,
        height=720,
    )
    original = Segment(
        id="original-1",
        project_id=project.id,
        start_ts=0.0,
        end_ts=12.0,
        source="original",
        url="/media/source.mp4",
        active=True,
        order_index=0,
    )
    generated = Segment(
        id="generated-1",
        project_id=project.id,
        start_ts=8.935,
        end_ts=11.935,
        source="generated",
        url="/media/generated.mp4",
        active=True,
        order_index=8935,
    )

    items = await build_timeline(_FakeSession([original, generated]), project)  # type: ignore[arg-type]

    assert [(item.start_ts, item.end_ts, item.source, item.url) for item in items] == [
        (0.0, 8.935, "original", "/media/source.mp4"),
        (8.935, 11.935, "generated", "/media/generated.mp4"),
        (11.935, 12.0, "original", "/media/source.mp4"),
    ]
