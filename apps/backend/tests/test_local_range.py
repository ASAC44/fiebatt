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
