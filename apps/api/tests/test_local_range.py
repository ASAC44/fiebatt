from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.ai.services.sam import TrackResult
from app.schemas.edit_plan import EditIntent
from app.services.local_range import (
    analysis_window,
    clear_local_range_cache,
    resolve_local_range,
    resolve_window_from_evidence,
)


def test_local_jump_window_is_bounded_around_playhead():
    intent = EditIntent(
        raw_prompt="make this person jump",
        change_type="motion",
        estimated_action_seconds=3.5,
        requires_recovery_motion=True,
    )
    assert analysis_window(intent, 50.0, 100.0) == (46.5, 53.5)


def test_range_uses_two_sided_handles_without_covering_whole_occurrence():
    intent = EditIntent(
        raw_prompt="make this person jump",
        change_type="motion",
        estimated_action_seconds=3.5,
    )
    result = resolve_window_from_evidence(
        intent=intent,
        seed_ts=10.0,
        duration=30.0,
        analysis_start=6.5,
        analysis_end=13.5,
        shot_start=4.0,
        shot_end=18.0,
        tracked_start=5.0,
        tracked_end=17.0,
        frames_inspected=29,
    )

    assert result.edit_core.start_ts == pytest.approx(8.25)
    assert result.edit_core.end_ts == pytest.approx(11.75)
    assert result.generation_context.start_ts == pytest.approx(7.5)
    assert result.generation_context.end_ts == pytest.approx(12.5)


def test_range_never_crosses_active_source_clip():
    intent = EditIntent(
        raw_prompt="make this person jump",
        change_type="motion",
        estimated_action_seconds=3.5,
    )
    result = resolve_window_from_evidence(
        intent=intent,
        seed_ts=11.5,
        duration=30.0,
        analysis_start=10.0,
        analysis_end=13.0,
        shot_start=8.0,
        shot_end=15.0,
        tracked_start=9.0,
        tracked_end=14.0,
        frames_inspected=13,
        source_start=10.0,
        source_end=13.0,
    )

    assert result.occurrence_start == pytest.approx(10.0)
    assert result.occurrence_end == pytest.approx(13.0)
    assert result.generation_context.start_ts == pytest.approx(10.0)
    assert result.generation_context.end_ts == pytest.approx(13.0)


def test_analysis_window_respects_active_source_clip():
    intent = EditIntent(
        raw_prompt="make this person jump",
        change_type="motion",
        estimated_action_seconds=3.5,
    )

    assert analysis_window(
        intent,
        11.5,
        30.0,
        source_start=10.0,
        source_end=13.0,
    ) == (10.0, 13.0)


@pytest.mark.asyncio
async def test_resolver_inspects_local_frames_and_reuses_cache(tmp_path):
    clear_local_range_cache()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unused by fake extractor")
    project = SimpleNamespace(video_path=str(source), video_url="", duration=120.0)
    selection = SimpleNamespace(
        id="selection-1",
        source_revision="revision-1",
        frame_ts=60.0,
        bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
    )
    intent = EditIntent(
        raw_prompt="make this person jump",
        change_type="motion",
        estimated_action_seconds=3.5,
    )
    extracted = []
    tracker_calls = 0

    async def fake_extract(_source, timestamp, output):
        extracted.append(timestamp)
        cv2.imwrite(str(output), np.full((36, 64, 3), 80, dtype=np.uint8))
        return output

    async def fake_track(paths, **kwargs):
        nonlocal tracker_calls
        tracker_calls += 1
        return TrackResult(
            tracker="stub",
            frames=[
                {"frame_index": index, "state": "tracked", "confidence": 1.0}
                for index in range(len(paths))
            ],
            processed_start_index=0,
            processed_end_index=len(paths) - 1,
        )

    first = await resolve_local_range(
        project, selection, intent, extract_frame=fake_extract, track_frames=fake_track
    )
    second = await resolve_local_range(
        project, selection, intent, extract_frame=fake_extract, track_frames=fake_track
    )

    assert first == second
    assert max(extracted) - min(extracted) <= 7.01
    assert len(extracted) < project.duration * 4
    assert tracker_calls == 1


@pytest.mark.asyncio
async def test_resolver_falls_back_when_video_tracker_is_unavailable(tmp_path):
    clear_local_range_cache()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unused")
    project = SimpleNamespace(video_path=str(source), video_url="", duration=20.0)
    selection = SimpleNamespace(
        id="selection-fallback",
        source_revision="revision-fallback",
        frame_ts=10.0,
        bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
        mask_url=None,
    )
    intent = EditIntent(
        raw_prompt="make this person jump",
        change_type="motion",
        estimated_action_seconds=3.5,
    )

    async def fake_extract(_source, _timestamp, output):
        cv2.imwrite(str(output), np.full((36, 64, 3), 80, dtype=np.uint8))
        return output

    async def unavailable_tracker(*args, **kwargs):
        raise ConnectionError("vision worker offline")

    result = await resolve_local_range(
        project,
        selection,
        intent,
        extract_frame=fake_extract,
        track_frames=unavailable_tracker,
    )

    assert result.edit_core.duration == pytest.approx(3.5)
    assert any("bbox fallback used" in warning for warning in result.warnings)
