from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.generation_window import GenerationWindow
from app.services import global_chunk_execution as execution


def _window() -> GenerationWindow:
    return GenerationWindow(
        core_start=5.75,
        core_end=9.25,
        context_start=5.0,
        context_end=10.0,
        adaptive=True,
    )


def test_first_internal_chunk_preserves_entrance_but_continues_edit_to_handoff():
    prompt = execution.global_chunk_prompt(
        "make the jacket blue",
        _window(),
        {
            "protect_source_before": True,
            "protect_source_after": False,
            "handoff_from_previous": False,
            "handoff_to_next": True,
        },
    )

    assert "Preserve the original entrance" in prompt
    assert "Continue the requested edit through the end" in prompt
    assert "do not return to the original appearance" in prompt


def test_middle_chunk_preserves_edited_handoff_without_reverting():
    prompt = execution.global_chunk_prompt(
        "make the jacket blue",
        _window(),
        {
            "protect_source_before": False,
            "protect_source_after": False,
            "handoff_from_previous": True,
            "handoff_to_next": True,
        },
    )

    assert "accepted ending of the previous chunk" in prompt
    assert "continue from it without reverting" in prompt
    assert "Continue the requested edit through the end" in prompt


def test_last_chunk_matches_original_only_after_edit_core():
    prompt = execution.global_chunk_prompt(
        "make the jacket blue",
        _window(),
        {
            "protect_source_before": False,
            "protect_source_after": True,
            "handoff_from_previous": True,
            "handoff_to_next": False,
        },
    )

    assert "accepted ending of the previous chunk" in prompt
    assert "Use the final 0.750 seconds to leave the edit" in prompt


@pytest.mark.asyncio
async def test_reference_subject_uses_box_frame_from_accepted_video(
    monkeypatch,
    tmp_path,
):
    extracted = []
    cropped = []

    async def path_from_url(url):
        return tmp_path / "accepted.mp4"

    async def extract_frame(source, timestamp, output):
        extracted.append((Path(source), timestamp, Path(output)))
        return Path(output)

    async def crop(frame, bbox):
        cropped.append((Path(frame), bbox))
        return tmp_path / "subject.png"

    monkeypatch.setattr(execution.storage, "path_from_url", path_from_url)
    monkeypatch.setattr(
        execution.storage,
        "new_path",
        lambda *args: (tmp_path / "frame.jpg", "/frame"),
    )
    monkeypatch.setattr(execution.ffmpeg, "extract_frame", extract_frame)
    monkeypatch.setattr(execution.ffmpeg, "crop_bbox_from_frame", crop)

    bbox = {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7}
    result = await execution.prepare_reference_subject(
        reference_video_url="/media/accepted.mp4",
        reference_json={
            "media_start": 0.75,
            "media_end": 4.75,
            "media_timestamp": 1.5,
            "bbox": bbox,
        },
    )

    assert result == tmp_path / "subject.png"
    assert extracted[0][1] == pytest.approx(1.5)
    assert cropped == [(tmp_path / "frame.jpg", bbox)]


@pytest.mark.asyncio
async def test_source_preparation_injects_previous_overlap(monkeypatch, tmp_path):
    paths = iter(
        [
            (tmp_path / "source.mp4", "/source"),
            (tmp_path / "overlap.mp4", "/overlap"),
            (tmp_path / "handed.mp4", "/handed"),
        ]
    )
    extracts = []
    handoffs = []

    monkeypatch.setattr(execution.storage, "new_path", lambda *args: next(paths))

    async def extract(source, start, end, output, *, with_audio):
        extracts.append((Path(source), start, end, Path(output), with_audio))
        return Path(output)

    async def path_from_url(url):
        assert url == "/previous"
        return tmp_path / "previous.mp4"

    async def prepend(base, handoff, duration, output):
        handoffs.append((Path(base), Path(handoff), duration, Path(output)))
        return Path(output)

    monkeypatch.setattr(execution.ffmpeg, "extract_clip", extract)
    monkeypatch.setattr(execution.storage, "path_from_url", path_from_url)
    monkeypatch.setattr(execution.ffmpeg, "prepend_video_handoff", prepend)

    project_path = tmp_path / "project.mp4"
    project_path.write_bytes(b"source")
    project = SimpleNamespace(video_path=project_path, video_url="/project")
    chunk = SimpleNamespace(
        context_start=9.25,
        context_end=14.0,
        payload_json={"boundary_contract": {"handoff_from_previous": True}},
    )
    result = await execution._prepare_source_clip(
        project=project,
        chunk=chunk,
        previous=execution.PreviousChunk(5.0, 10.75, "/previous"),
    )

    assert result == tmp_path / "handed.mp4"
    assert extracts[0][1:3] == (9.25, 14.0)
    assert extracts[1][1:3] == pytest.approx((4.25, 5.75))
    assert handoffs[0][2] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_generation_uses_source_video_target_box_and_accepted_reference(
    monkeypatch,
    tmp_path,
):
    source_path = tmp_path / "source.mp4"
    reference_path = tmp_path / "reference.png"
    calls = []

    async def on_tick(event):
        return None

    async def prepare_source(**kwargs):
        return source_path

    async def publish(path, *, content_type):
        return "https://media.example/source.mp4"

    async def extract_frame(source, timestamp, output):
        return Path(output)

    async def generate(clip_path, plan, **kwargs):
        calls.append((clip_path, plan, kwargs))
        return {"url": clip_path, "description": "stub"}

    monkeypatch.setattr(execution, "_prepare_source_clip", prepare_source)
    monkeypatch.setattr(execution.storage, "publish", publish)
    monkeypatch.setattr(
        execution.storage,
        "new_path",
        lambda *args: (tmp_path / "target.jpg", "/target"),
    )
    monkeypatch.setattr(execution.ffmpeg, "extract_frame", extract_frame)
    monkeypatch.setattr(execution.ai.runway, "generate", generate)

    project = SimpleNamespace(fps=30.0, video_path=tmp_path / "project.mp4")
    chunk = SimpleNamespace(
        index=0,
        provider="wan",
        edit_start=5.75,
        edit_end=9.25,
        context_start=5.0,
        context_end=10.0,
        payload_json={
            "boundary_contract": {
                "protect_source_before": True,
                "protect_source_after": True,
            },
            "track_frames": [
                {
                    "timestamp": 7.5,
                    "state": "tracked",
                    "bbox": {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
                }
            ],
        },
    )
    result = await execution.execute_global_chunk(
        project=project,
        chunk=chunk,
        prompt="make the jacket blue",
        reference_subject_path=reference_path,
        previous=None,
        on_tick=on_tick,
    )

    assert result.output_url == "https://media.example/source.mp4"
    _, plan, kwargs = calls[0]
    assert plan["_video_gen_provider"] == "wan"
    assert "make the jacket blue" in plan["_edit_prompt"]
    assert kwargs["source_video_url"] == "https://media.example/source.mp4"
    assert kwargs["subject_reference_path"] == str(reference_path)
    assert kwargs["on_tick"] is on_tick
    assert result.metadata["target_bbox"]["w"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_real_generation_fails_closed_without_public_media(monkeypatch, tmp_path):
    async def prepare_source(**kwargs):
        return tmp_path / "source.mp4"

    async def publish(path, *, content_type):
        return "/media/source.mp4"

    monkeypatch.setattr(execution, "_prepare_source_clip", prepare_source)
    monkeypatch.setattr(execution.storage, "publish", publish)
    monkeypatch.setattr(
        execution,
        "get_settings",
        lambda: SimpleNamespace(use_ai_stubs=False),
    )

    chunk = SimpleNamespace(
        context_start=1.0,
        context_end=4.0,
    )
    with pytest.raises(ValueError, match="provider-accessible media storage"):
        await execution.execute_global_chunk(
            project=SimpleNamespace(video_path=tmp_path / "project.mp4"),
            chunk=chunk,
            prompt="edit",
            reference_subject_path=tmp_path / "reference.png",
            previous=None,
        )
