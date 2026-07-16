from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models as _models  # noqa: F401
from app.db.base import Base
from app.models.entity import Entity
from app.services.coarse_occurrence import (
    normalize_coarse_hits,
    persist_coarse_candidates,
    search_coarse_occurrences,
    source_revision_cache_dir,
)


IDENTITY = {
    "description": "person in a red jacket",
    "category": "person",
    "attributes": {"jacket": "red"},
}


def test_coarse_hits_reject_false_positives_and_merge_duplicates():
    candidates = normalize_coarse_hits(
        [
            {"start_ts": 1.0, "end_ts": 2.0, "confidence": 0.2},
            {"start_ts": 4.0, "end_ts": 5.0, "confidence": 0.82},
            {"start_ts": 5.0, "end_ts": 6.0, "confidence": 0.91},
            {"start_ts": 10.0, "end_ts": 11.0, "confidence": 0.88},
            {"start_ts": 14.0, "end_ts": 15.0, "confidence": 0.95},
        ],
        identity=IDENTITY,
        source_revision="source-v1",
        duration=20.0,
        exclude_start=13.5,
        exclude_end=15.5,
    )

    assert len(candidates) == 2
    assert (candidates[0].start_ts, candidates[0].end_ts) == (4.0, 6.0)
    assert candidates[0].confidence == 0.91
    assert candidates[0].evidence["coarse_hit_count"] == 2
    assert candidates[1].keyframe_ts == 10.0


@pytest.mark.asyncio
async def test_clip_recall_limits_vlm_frames_and_keeps_source_timestamps():
    keyframes = [f"frame_{index}.jpg" for index in range(6)]

    async def clip_search(reference, paths, *, threshold):
        assert reference == "subject.png"
        assert threshold > 0.0
        return [
            {"keyframe_index": index, "confidence": 0.9, "found": index in {1, 4}}
            for index in range(len(paths))
        ]

    async def vlm_search(identity, paths):
        assert identity == IDENTITY
        assert paths == ["frame_1.jpg", "frame_4.jpg"]
        return [
            {
                "start_ts": float(index),
                "end_ts": float(index + 1),
                "keyframe_url": path,
                "confidence": 0.86,
            }
            for index, path in enumerate(paths)
        ]

    result = await search_coarse_occurrences(
        identity=IDENTITY,
        reference_crop_path="subject.png",
        keyframe_paths=keyframes,
        source_revision="source-v1",
        duration=6.0,
        exclude_start=None,
        exclude_end=None,
        vlm_search=vlm_search,
        clip_search=clip_search,
    )

    assert result.analysis_mode == "clip_then_vlm"
    assert result.frames_inspected == 2
    assert [candidate.keyframe_ts for candidate in result.candidates] == [1.0, 4.0]
    assert all(candidate.evidence["clip_similarity"] == 0.9 for candidate in result.candidates)


@pytest.mark.asyncio
async def test_candidate_persistence_reports_cache_hits(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'coarse.db'}")
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    candidates = normalize_coarse_hits(
        [{"start_ts": 4.0, "end_ts": 5.0, "confidence": 0.9}],
        identity=IDENTITY,
        source_revision="source-v1",
        duration=10.0,
    )
    async with sessions() as db:
        entity = Entity(project_id="project-1", description="person")
        db.add(entity)
        await db.flush()
        first, first_hits = await persist_coarse_candidates(
            db, entity_id=entity.id, candidates=candidates
        )
        await db.commit()
        second, second_hits = await persist_coarse_candidates(
            db, entity_id=entity.id, candidates=candidates
        )

    assert first_hits == 0
    assert second_hits == 1
    assert first[0].id == second[0].id
    await engine.dispose()


def test_keyframe_cache_changes_with_source_revision(tmp_path):
    first = source_revision_cache_dir(
        Path(tmp_path), project_id="project-1", source_revision="source-v1"
    )
    second = source_revision_cache_dir(
        Path(tmp_path), project_id="project-1", source_revision="source-v2"
    )

    assert first != second
    assert first.parent == second.parent
