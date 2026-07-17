from __future__ import annotations

import pytest

from app.models.job import Job, Variant
from app.schemas.timeline import PersistedAsset, PersistedClip, PersistedEDL
from app.services.accepted_generation import (
    accepted_generation_range,
    record_accepted_range,
    resolve_committed_timeline_range,
    splice_accepted_clip_into_edl,
    splice_generated_clip_into_edl,
)
from app.workers import export_job


def _job() -> Job:
    return Job(
        id="job-1",
        project_id="project-1",
        kind="generate",
        start_ts=3.0,
        end_ts=5.0,
        payload={
            "execution_window": {
                "core_start": 3.0,
                "core_end": 5.0,
                "context_start": 2.0,
                "context_end": 8.0,
            }
        },
    )


def test_range_keeps_core_context_and_media_offsets_distinct():
    job = _job()
    accepted = accepted_generation_range(job)
    assert (accepted.committed_start, accepted.committed_end) == (3.0, 5.0)
    assert (accepted.context_start, accepted.context_end) == (2.0, 8.0)
    assert (accepted.media_start, accepted.media_end) == (1.0, 3.0)
    assert accepted.media_duration == 6.0

    record_accepted_range(job, segment_id="segment-1", accepted_range=accepted)
    assert job.payload["accepted_ranges"]["segment-1"]["media_start"] == 1.0


def test_edl_splice_preserves_manual_order_and_uses_core_inside_padded_media():
    edl = PersistedEDL(
        clips=[
            PersistedClip(
                id="reordered-a",
                kind="source",
                url="source.mp4",
                source_start=5.0,
                source_end=9.0,
                media_duration=12.0,
                project_id="project-1",
            ),
            PersistedClip(
                id="reordered-b",
                kind="source",
                url="source.mp4",
                source_start=0.0,
                source_end=6.0,
                media_duration=12.0,
                project_id="project-1",
            ),
        ],
        sources=[
            PersistedAsset(
                id="source",
                kind="source",
                url="source.mp4",
                duration=12.0,
                fps=24.0,
                project_id="project-1",
                label="source",
            )
        ],
    )
    variant = Variant(id="variant-1", job_id="job-1", index=0, url="generated.mp4")
    result = splice_accepted_clip_into_edl(
        edl,
        project_id="project-1",
        project_fps=24.0,
        segment_id="segment-1",
        variant=variant,
        accepted_range=accepted_generation_range(_job()),
    )

    assert [(clip.kind, clip.source_start, clip.source_end) for clip in result.clips] == [
        ("source", 5.0, 8.0),
        ("generated", 1.0, 3.0),
        ("source", 1.0, 6.0),
    ]
    assert sum(clip.source_end - clip.source_start for clip in result.clips) == pytest.approx(10.0)
    generated_asset = next(asset for asset in result.sources if asset.id == "variant-1")
    assert generated_asset.duration == 6.0
    assert generated_asset.fps == 24.0
    assert next(clip for clip in result.clips if clip.kind == "generated").volume == 1.0


def test_source_range_maps_through_reordered_target_clip_to_timeline_range():
    edl = PersistedEDL(
        clips=[
            PersistedClip(
                id="target",
                kind="source",
                url="source.mp4",
                source_start=5.0,
                source_end=9.0,
                media_duration=12.0,
            ),
            PersistedClip(
                id="other",
                kind="source",
                url="source.mp4",
                source_start=0.0,
                source_end=4.0,
                media_duration=12.0,
            ),
        ],
        sources=[],
    )
    assert resolve_committed_timeline_range(
        edl.model_dump(mode="json"),
        target_clip_id="target",
        source_start=6.0,
        source_end=8.0,
    ) == (1.0, 3.0)


def test_generic_generated_splice_uses_exact_occurrence_media_range():
    edl = PersistedEDL(
        clips=[
            PersistedClip(
                id="source",
                kind="source",
                url="source.mp4",
                source_start=0.0,
                source_end=10.0,
                media_duration=10.0,
                project_id="project-1",
            )
        ],
        sources=[],
    )

    result = splice_generated_clip_into_edl(
        edl,
        project_id="project-1",
        project_fps=24.0,
        segment_id="global-segment",
        asset_id="global-result",
        url="global.mp4",
        timeline_start=3.0,
        timeline_end=6.0,
        media_start=0.0,
        media_end=3.0,
        media_duration=3.0,
    )

    assert [(clip.kind, clip.source_start, clip.source_end) for clip in result.clips] == [
        ("source", 0.0, 3.0),
        ("generated", 0.0, 3.0),
        ("source", 6.0, 10.0),
    ]
    assert result.sources[0].id == "global-result"


@pytest.mark.asyncio
async def test_export_renders_only_authoritative_media_subrange(monkeypatch, tmp_path):
    captured = {}

    async def fake_render_clip(_src, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(export_job, "_render_clip", fake_render_clip)
    await export_job._render_span(
        tmp_path / "generated.mp4",
        span_start=3.0,
        span_end=5.0,
        target_w=1280,
        target_h=720,
        target_fps=24.0,
        out=tmp_path / "out.mp4",
        is_generated=True,
        media_start=1.0,
        media_end=3.0,
    )
    assert captured["source_start"] == 1.0
    assert captured["source_end"] == 3.0
    assert captured["volume"] == 1.0
