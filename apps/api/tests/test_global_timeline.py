from types import SimpleNamespace

import pytest

from app.schemas.timeline import PersistedClip, PersistedEDL
from app.services.global_timeline import (
    ensure_source_aligned_timeline,
    timeline_revision,
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _DB:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def execute(self, statement):
        return _Rows(self.rows)


def _project(clips, *, duration=10.0):
    edl = PersistedEDL(clips=clips, sources=[])
    return SimpleNamespace(
        id="project-1",
        video_url="/media/source.mp4",
        duration=duration,
        timeline_edl=edl.model_dump(mode="json"),
    )


def _source(clip_id: str, start: float, end: float):
    return PersistedClip(
        id=clip_id,
        kind="source",
        url="/media/source.mp4",
        source_start=start,
        source_end=end,
        media_duration=10.0,
        project_id="project-1",
    )


@pytest.mark.asyncio
async def test_duration_preserving_source_splits_remain_supported():
    project = _project([_source("a", 0.0, 4.0), _source("b", 4.0, 10.0)])

    await ensure_source_aligned_timeline(_DB(), project)


@pytest.mark.asyncio
async def test_reordered_or_trimmed_timeline_fails_closed():
    reordered = _project([_source("a", 5.0, 10.0), _source("b", 0.0, 5.0)])
    trimmed = _project([_source("a", 0.0, 8.0)])

    with pytest.raises(ValueError, match="source-time order"):
        await ensure_source_aligned_timeline(_DB(), reordered)
    with pytest.raises(ValueError, match="duration-preserving"):
        await ensure_source_aligned_timeline(_DB(), trimmed)


@pytest.mark.asyncio
async def test_aligned_generated_replacement_is_supported():
    generated = PersistedClip(
        id="segment-1",
        kind="generated",
        url="/media/edit.mp4",
        source_start=0.5,
        source_end=2.5,
        media_duration=3.0,
        project_id="project-1",
    )
    project = _project(
        [_source("before", 0.0, 3.0), generated, _source("after", 5.0, 10.0)]
    )
    segment = SimpleNamespace(id="segment-1", start_ts=3.0, end_ts=5.0)

    await ensure_source_aligned_timeline(_DB([segment]), project)


def test_timeline_revision_changes_with_saved_edits():
    project = _project([_source("source", 0.0, 10.0)])
    before = timeline_revision(project)
    project.timeline_edl["clips"][0]["volume"] = 0.5

    assert timeline_revision(project) != before
