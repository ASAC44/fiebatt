from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models as _models  # noqa: F401
from app.ai.services.sam import TrackResult
from app.db.base import Base
from app.models.entity import Entity, OccurrenceCandidate
from app.services.dense_occurrence import (
    DenseTrackEvidence,
    locate_reference_bbox,
    merge_confirmed_ranges,
    persist_dense_tracks,
    refine_occurrence_candidate,
)


@pytest.mark.asyncio
async def test_clip_locator_selects_best_region(tmp_path):
    frame_path = tmp_path / "frame.jpg"
    reference_path = tmp_path / "reference.jpg"
    cv2.imwrite(str(frame_path), np.zeros((80, 120, 3), dtype=np.uint8))
    cv2.imwrite(str(reference_path), np.zeros((20, 20, 3), dtype=np.uint8))

    async def embed(paths):
        vectors = [[1.0, 0.0]]
        vectors.extend([[0.0, 1.0] for _ in paths[1:]])
        vectors[6] = [1.0, 0.0]
        return vectors

    bbox, score = await locate_reference_bbox(
        str(frame_path), str(reference_path), embed_images=embed
    )

    assert bbox is not None
    assert score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_dense_refinement_uses_real_contiguous_track(tmp_path):
    candidate = OccurrenceCandidate(
        id="candidate-1",
        entity_id="entity-1",
        source_revision="source-v1",
        cache_key="cache-1",
        keyframe_ts=5.0,
        start_ts=5.0,
        end_ts=6.0,
        confidence=0.9,
        evidence_json={},
    )

    async def extract(source, timestamp, destination):
        cv2.imwrite(str(destination), np.zeros((48, 64, 3), dtype=np.uint8))
        return Path(destination)

    async def locate(frame, reference):
        return {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7}, 0.88

    async def track(paths, *, seed_frame_index, **kwargs):
        frames = []
        for index in range(len(paths)):
            state = "tracked" if seed_frame_index - 3 <= index <= seed_frame_index + 4 else "lost"
            frames.append(
                {
                    "frame_index": index,
                    "state": state,
                    "confidence": 0.92 if state == "tracked" else 0.1,
                    "bbox": kwargs["bbox"] if state == "tracked" else None,
                }
            )
        return TrackResult(
            tracker="sam2_video",
            frames=frames,
            processed_start_index=0,
            processed_end_index=len(paths) - 1,
        )

    result = await refine_occurrence_candidate(
        source_path=tmp_path / "source.mp4",
        project_duration=12.0,
        candidate=candidate,
        reference_crop_path="reference.jpg",
        extract_frame=extract,
        locate_bbox=locate,
        track_frames=track,
    )

    assert result.passed is True
    assert result.tracker == "sam2_video"
    assert result.start_ts < candidate.keyframe_ts < result.end_ts
    assert result.confidence == pytest.approx(0.88)


@pytest.mark.asyncio
async def test_stub_tracker_cannot_confirm_occurrence(tmp_path):
    candidate = OccurrenceCandidate(
        id="candidate-1",
        entity_id="entity-1",
        source_revision="source-v1",
        cache_key="cache-1",
        keyframe_ts=2.0,
        start_ts=2.0,
        end_ts=3.0,
        confidence=0.9,
        evidence_json={},
    )

    async def extract(source, timestamp, destination):
        cv2.imwrite(str(destination), np.zeros((48, 64, 3), dtype=np.uint8))
        return Path(destination)

    async def locate(frame, reference):
        return {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7}, 0.88

    async def track(paths, **kwargs):
        return TrackResult(
            tracker="stub",
            frames=[],
            processed_start_index=0,
            processed_end_index=len(paths) - 1,
            warning="fallback",
        )

    result = await refine_occurrence_candidate(
        source_path=tmp_path / "source.mp4",
        project_duration=6.0,
        candidate=candidate,
        reference_crop_path="reference.jpg",
        extract_frame=extract,
        locate_bbox=locate,
        track_frames=track,
    )

    assert result.passed is False
    assert "real SAM2" in result.reason


def test_dense_tracks_merge_overlap_but_keep_reentry_separate():
    def evidence(candidate_id, start, end, confidence=0.9):
        return DenseTrackEvidence(
            candidate_id,
            True,
            "confirmed",
            (start + end) / 2,
            start,
            end,
            confidence,
            "sam2_video",
            (),
        )

    merged = merge_confirmed_ranges(
        [
            evidence("one", 1.0, 3.0),
            evidence("duplicate", 2.5, 4.0, 0.95),
            evidence("reentry", 7.0, 8.0),
        ]
    )

    assert merged == [(1.0, 4.0, 0.95), (7.0, 8.0, 0.9)]


@pytest.mark.asyncio
async def test_dense_persistence_is_idempotent(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dense.db'}")
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with sessions() as db:
        entity = Entity(project_id="project-1", description="person")
        db.add(entity)
        await db.flush()
        candidate = OccurrenceCandidate(
            entity_id=entity.id,
            source_revision="source-v1",
            cache_key="cache-1",
            keyframe_ts=3.0,
            start_ts=3.0,
            end_ts=4.0,
            confidence=0.9,
            evidence_json={},
        )
        db.add(candidate)
        await db.flush()
        evidence = DenseTrackEvidence(
            candidate.id,
            True,
            "confirmed",
            3.0,
            2.0,
            5.0,
            0.9,
            "sam2_video",
            (),
        )
        assert await persist_dense_tracks(
            db,
            entity_id=entity.id,
            source_revision="source-v1",
            tracks=[evidence],
        ) == (1, 0)
        await db.commit()
        assert await persist_dense_tracks(
            db,
            entity_id=entity.id,
            source_revision="source-v1",
            tracks=[evidence],
        ) == (1, 0)
        await db.commit()
        await db.refresh(entity, attribute_names=["appearances", "occurrence_tracks"])
        assert len(entity.appearances) == 1
        assert len(entity.occurrence_tracks) == 1

    await engine.dispose()
