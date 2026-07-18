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
    project_target_through_parent,
    temporal_tracking_bbox,
    tracked_span,
    tracked_span_evidence,
)


def test_local_jump_window_is_bounded_around_playhead():
    intent = EditIntent(
        raw_prompt="make this person jump",
        change_type="motion",
        estimated_action_seconds=3.5,
        requires_recovery_motion=True,
    )
    assert analysis_window(intent, 50.0, 100.0) == (46.5, 53.5)


def test_temporal_tracking_uses_parent_context_for_thin_detail():
    selected = {"x": 0.438, "y": 0.642, "w": 0.313, "h": 0.068}

    tracked = temporal_tracking_bbox(selected)

    assert tracked["w"] > selected["w"]
    assert tracked["h"] >= 0.28
    assert tracked["x"] <= selected["x"]
    assert tracked["y"] <= selected["y"]


def test_tracked_span_bridges_brief_loss_but_not_real_disappearance():
    timestamps = [float(index) for index in range(8)]
    brief_loss = [
        {"frame_index": index, "state": "lost" if index in {3, 4} else "tracked"}
        for index in range(8)
    ]
    disappearance = [
        {"frame_index": index, "state": "tracked" if index <= 2 or index == 6 else "lost"}
        for index in range(8)
    ]

    assert tracked_span(brief_loss, timestamps, 2) == (0.0, 7.0)
    assert tracked_span(disappearance, timestamps, 2) == (0.0, 2.0)


def test_tracking_boundaries_separate_uncertainty_from_confirmed_absence():
    timestamps = [float(index) for index in range(8)]
    uncertain = [
        {
            "frame_index": index,
            "state": "tracked" if index == 3 else "uncertain",
        }
        for index in range(8)
    ]
    absent = [
        {
            "frame_index": index,
            "state": "tracked" if 1 <= index <= 3 else "lost",
        }
        for index in range(8)
    ]

    uncertain_evidence = tracked_span_evidence(uncertain, timestamps, 3)
    absent_evidence = tracked_span_evidence(absent, timestamps, 3)

    assert uncertain_evidence.left_boundary == "uncertain"
    assert uncertain_evidence.right_boundary == "uncertain"
    assert absent_evidence.left_boundary == "uncertain"
    assert absent_evidence.right_boundary == "confirmed_absent"
    assert absent_evidence.end == pytest.approx(3.0)


def test_precise_target_moves_with_broader_parent_without_growing():
    target = {"x": 0.45, "y": 0.50, "w": 0.10, "h": 0.05}
    seed_parent = {"x": 0.30, "y": 0.30, "w": 0.40, "h": 0.40}
    moved_parent = {"x": 0.40, "y": 0.20, "w": 0.50, "h": 0.50}

    moved = project_target_through_parent(target, seed_parent, moved_parent)

    assert moved["x"] == pytest.approx(0.5875)
    assert moved["y"] == pytest.approx(0.45)
    assert moved["w"] == pytest.approx(0.125)
    assert moved["h"] == pytest.approx(0.0625)


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


def test_trajectory_change_covers_occurrence_instead_of_old_local_position():
    intent = EditIntent(
        raw_prompt="make this person run",
        change_type="motion",
        duration_policy="trajectory_continuation",
        temporal_behavior="future_changing_motion",
    )
    result = resolve_window_from_evidence(
        intent=intent,
        seed_ts=10.0,
        duration=30.0,
        analysis_start=7.0,
        analysis_end=13.0,
        shot_start=4.0,
        shot_end=18.0,
        tracked_start=5.0,
        tracked_end=17.0,
        frames_inspected=25,
    )

    assert result.edit_core.start_ts == pytest.approx(5.0)
    assert result.edit_core.end_ts == pytest.approx(17.0)
    assert result.generation_context.start_ts == pytest.approx(4.25)
    assert result.generation_context.end_ts == pytest.approx(17.75)


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


@pytest.mark.asyncio
async def test_state_change_expands_outward_until_target_is_lost(tmp_path):
    clear_local_range_cache()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unused")
    project = SimpleNamespace(video_path=str(source), video_url="", duration=100.0)
    selection = SimpleNamespace(
        id="selection-state",
        source_revision="revision-state",
        frame_ts=50.0,
        bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
        mask_url=None,
    )
    intent = EditIntent(
        raw_prompt="make this ball pink",
        change_type="appearance",
        duration_policy="continuous_occurrence",
    )
    timestamps_by_path = {}
    tracker_calls = 0

    async def fake_extract(_source, timestamp, output):
        timestamps_by_path[str(output)] = timestamp
        cv2.imwrite(str(output), np.full((36, 64, 3), 80, dtype=np.uint8))
        return output

    async def fake_track(paths, **kwargs):
        nonlocal tracker_calls
        tracker_calls += 1
        frames = []
        for index, path in enumerate(paths):
            timestamp = timestamps_by_path[path]
            frames.append(
                {
                    "frame_index": index,
                    "state": "tracked" if 40.0 <= timestamp <= 60.0 else "lost",
                    "confidence": 1.0,
                }
            )
        return TrackResult(
            tracker="test",
            frames=frames,
            processed_start_index=0,
            processed_end_index=len(frames) - 1,
        )

    result = await resolve_local_range(
        project,
        selection,
        intent,
        extract_frame=fake_extract,
        track_frames=fake_track,
    )

    assert tracker_calls == 3
    assert result.analysis_start == pytest.approx(38.0)
    assert result.analysis_end == pytest.approx(62.0)
    assert result.occurrence_start == pytest.approx(40.0)
    assert result.occurrence_end == pytest.approx(60.0)
    assert result.generation_context.start_ts == pytest.approx(39.25)
    assert result.generation_context.end_ts == pytest.approx(60.75)


@pytest.mark.asyncio
async def test_thin_persistent_target_expands_to_full_local_presence(tmp_path):
    clear_local_range_cache()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unused")
    project = SimpleNamespace(video_path=str(source), video_url="", duration=10.08)
    selection = SimpleNamespace(
        id="selection-shirt-text",
        source_revision="revision-shirt-text",
        frame_ts=0.0,
        bbox_json={"x": 0.438, "y": 0.642, "w": 0.313, "h": 0.068},
        mask_url=None,
    )
    intent = EditIntent(
        raw_prompt='replace the shirt text with "Hello World"',
        change_type="replacement",
        duration_policy="continuous_occurrence",
        temporal_behavior="persistent_state",
    )
    seen_boxes = []

    async def fake_extract(_source, _timestamp, output):
        cv2.imwrite(str(output), np.full((72, 128, 3), 80, dtype=np.uint8))
        return output

    async def fake_track(paths, **kwargs):
        seen_boxes.append(kwargs["bbox"])
        return TrackResult(
            tracker="test",
            frames=[
                {
                    "frame_index": index,
                    "state": "tracked",
                    "confidence": 1.0,
                }
                for index in range(len(paths))
            ],
            processed_start_index=0,
            processed_end_index=len(paths) - 1,
        )

    result = await resolve_local_range(
        project,
        selection,
        intent,
        extract_frame=fake_extract,
        track_frames=fake_track,
    )

    assert result.edit_core.start_ts == pytest.approx(0.0)
    assert result.edit_core.end_ts == pytest.approx(10.08)
    assert len(seen_boxes) >= 2
    assert all(box["h"] >= 0.28 for box in seen_boxes)
    assert all(
        frame["bbox"]["h"] == pytest.approx(selection.bbox_json["h"])
        for frame in result.tracked_frames
    )


@pytest.mark.asyncio
async def test_uncertain_persistent_tracking_expands_to_shot_boundaries(tmp_path):
    clear_local_range_cache()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unused")
    project = SimpleNamespace(video_path=str(source), video_url="", duration=12.0)
    selection = SimpleNamespace(
        id="selection-uncertain",
        source_revision="revision-uncertain",
        frame_ts=6.0,
        bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
        mask_url=None,
    )
    intent = EditIntent(
        raw_prompt="make this coat green",
        change_type="appearance",
        duration_policy="continuous_occurrence",
    )

    async def fake_extract(_source, timestamp, output):
        if timestamp < 2.0:
            level = 10
        elif timestamp < 10.0:
            level = 120
        else:
            level = 240
        cv2.imwrite(str(output), np.full((36, 64, 3), level, dtype=np.uint8))
        return output

    async def uncertain_tracker(paths, **kwargs):
        seed_index = kwargs["seed_frame_index"]
        return TrackResult(
            tracker="test",
            frames=[
                {
                    "frame_index": index,
                    "state": "tracked" if index == seed_index else "uncertain",
                    "confidence": 1.0 if index == seed_index else 0.4,
                }
                for index in range(len(paths))
            ],
            processed_start_index=0,
            processed_end_index=len(paths) - 1,
        )

    result = await resolve_local_range(
        project,
        selection,
        intent,
        extract_frame=fake_extract,
        track_frames=uncertain_tracker,
    )

    assert result.analysis_start == pytest.approx(0.0)
    assert result.analysis_end == pytest.approx(12.0)
    assert result.occurrence_start == pytest.approx(2.0)
    assert result.occurrence_end == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_state_change_fails_closed_when_tracking_is_unavailable(tmp_path):
    clear_local_range_cache()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unused")
    project = SimpleNamespace(video_path=str(source), video_url="", duration=20.0)
    selection = SimpleNamespace(
        id="selection-state-failure",
        source_revision="revision-state-failure",
        frame_ts=10.0,
        bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
        mask_url=None,
    )
    intent = EditIntent(
        raw_prompt="make this ball pink",
        change_type="appearance",
        duration_policy="continuous_occurrence",
    )

    async def fake_extract(_source, _timestamp, output):
        cv2.imwrite(str(output), np.full((36, 64, 3), 80, dtype=np.uint8))
        return output

    async def unavailable_tracker(*args, **kwargs):
        raise ConnectionError("tracker unavailable")

    with pytest.raises(ValueError, match="could not reliably track"):
        await resolve_local_range(
            project,
            selection,
            intent,
            extract_frame=fake_extract,
            track_frames=unavailable_tracker,
        )


@pytest.mark.asyncio
async def test_state_change_rejects_occurrences_over_thirty_seconds(tmp_path):
    clear_local_range_cache()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unused")
    project = SimpleNamespace(video_path=str(source), video_url="", duration=100.0)
    selection = SimpleNamespace(
        id="selection-long-state",
        source_revision="revision-long-state",
        frame_ts=50.0,
        bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
        mask_url=None,
    )
    intent = EditIntent(
        raw_prompt="make this ball pink",
        change_type="appearance",
        duration_policy="continuous_occurrence",
    )
    timestamps_by_path = {}

    async def fake_extract(_source, timestamp, output):
        timestamps_by_path[str(output)] = timestamp
        cv2.imwrite(str(output), np.full((36, 64, 3), 80, dtype=np.uint8))
        return output

    async def fake_track(paths, **kwargs):
        return TrackResult(
            tracker="test",
            frames=[
                {
                    "frame_index": index,
                    "state": (
                        "tracked"
                        if 30.0 <= timestamps_by_path[path] <= 70.0
                        else "lost"
                    ),
                    "confidence": 1.0,
                }
                for index, path in enumerate(paths)
            ],
            processed_start_index=0,
            processed_end_index=len(paths) - 1,
        )

    with pytest.raises(ValueError, match="more than 30 seconds"):
        await resolve_local_range(
            project,
            selection,
            intent,
            extract_frame=fake_extract,
            track_frames=fake_track,
        )


@pytest.mark.asyncio
async def test_uncertain_state_change_stops_at_thirty_second_budget(tmp_path):
    clear_local_range_cache()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unused")
    project = SimpleNamespace(video_path=str(source), video_url="", duration=100.0)
    selection = SimpleNamespace(
        id="selection-long-uncertain",
        source_revision="revision-long-uncertain",
        frame_ts=50.0,
        bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
        mask_url=None,
    )
    intent = EditIntent(
        raw_prompt="make this ball pink",
        change_type="appearance",
        duration_policy="continuous_occurrence",
    )

    async def fake_extract(_source, _timestamp, output):
        cv2.imwrite(str(output), np.full((36, 64, 3), 80, dtype=np.uint8))
        return output

    async def uncertain_tracker(paths, **kwargs):
        seed_index = kwargs["seed_frame_index"]
        return TrackResult(
            tracker="test",
            frames=[
                {
                    "frame_index": index,
                    "state": "tracked" if index == seed_index else "uncertain",
                    "confidence": 1.0 if index == seed_index else 0.4,
                }
                for index in range(len(paths))
            ],
            processed_start_index=0,
            processed_end_index=len(paths) - 1,
        )

    with pytest.raises(ValueError, match="more than 30 seconds"):
        await resolve_local_range(
            project,
            selection,
            intent,
            extract_frame=fake_extract,
            track_frames=uncertain_tracker,
        )
