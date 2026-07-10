import os
from pathlib import Path

os.environ["USE_AI_STUBS"] = "true"

import pytest  # noqa: E402

from ai import services as ai  # noqa: E402
from app.workers import generate_job  # noqa: E402
from app.workers.generate_job import _public_url_or_none  # noqa: E402


def test_public_url_gate_accepts_remote_https():
    assert _public_url_or_none("https://cdn.example.test/clip.mp4") == "https://cdn.example.test/clip.mp4"


def test_public_url_gate_rejects_local_urls():
    assert _public_url_or_none("/media/clips/clip.mp4") is None
    assert _public_url_or_none("http://localhost:8000/media/clips/clip.mp4") is None
    assert _public_url_or_none("http://127.0.0.1:8000/media/clips/clip.mp4") is None


def test_jump_then_walk_prompt_is_sequenced_motion():
    motion, sequenced, _ = ai._rewrite_motion_prompt(  # type: ignore[attr-defined]
        "The man jumps up and down a few times, then lands and smoothly continues into a normal walk, without stopping."
    )

    assert motion is True
    assert sequenced is True


@pytest.mark.asyncio
async def test_happyhorse_bridge_uses_short_crossfade(monkeypatch, tmp_path):
    class DummySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    events = []
    concat_calls = []
    render_calls = []

    async def noop(*args, **kwargs):
        return None

    async def fake_generate_variant(*args, **kwargs):
        return str(tmp_path / "action.mp4")

    async def fake_generate_propagation_variant(*args, **kwargs):
        return str(tmp_path / "continue.mp4")

    async def fake_extract_frame(src, ts, out):
        Path(out).touch()
        return Path(out)

    async def fake_render_clip_span(src, start, end, out, **kwargs):
        render_calls.append({"src": src, "start": start, "end": end, "out": out})
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).touch()
        return Path(out)

    async def fake_concat_clips(paths, out, *, transitions=None):
        concat_calls.append({"paths": paths, "out": out, "transitions": transitions})
        Path(out).touch()
        return Path(out)

    async def fake_publish(path, *, content_type=None):
        return "https://cdn.example.test/generated/bridge.mp4"

    async def fake_emit(job_id, stage, msg, **data):
        events.append({"stage": stage, "data": data})

    monkeypatch.setattr(generate_job, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(generate_job, "_update_variant", noop)
    monkeypatch.setattr(generate_job, "_update_job", noop)
    monkeypatch.setattr(generate_job, "_emit", fake_emit)
    monkeypatch.setattr(generate_job.happyhorse, "generate_variant", fake_generate_variant)
    monkeypatch.setattr(generate_job.happyhorse, "generate_propagation_variant", fake_generate_propagation_variant)
    monkeypatch.setattr(generate_job.ffmpeg, "extract_frame", fake_extract_frame)
    monkeypatch.setattr(generate_job.ffmpeg, "render_clip_span", fake_render_clip_span)
    monkeypatch.setattr(generate_job.ffmpeg, "concat_clips", fake_concat_clips)
    monkeypatch.setattr(generate_job.storage, "new_path", lambda category, ext: (tmp_path / f"{category}.{ext}", "/media/out"))
    monkeypatch.setattr(generate_job.storage, "path_for", lambda category, filename: tmp_path / filename)
    monkeypatch.setattr(generate_job.storage, "publish", fake_publish)

    await generate_job._run_happyhorse_motion_bridge(
        job_id="job-1",
        variant_id="variant-12345678",
        source_video_url="https://cdn.example.test/clips/source.mp4",
        reference_frame_path=str(tmp_path / "reference.jpg"),
        action_duration=3.0,
        continuation_duration=3.0,
        output_width=1280,
        output_height=720,
        output_fps=24.0,
        bridge_end_ts=6.0,
        resolution="720P",
    )

    assert concat_calls
    assert render_calls[0]["end"] == 3.0 + generate_job.BRIDGE_SEAM_CROSSFADE_SECONDS
    assert concat_calls[0]["transitions"] == [generate_job.BRIDGE_SEAM_CROSSFADE_SECONDS]
    assert concat_calls[0]["transitions"][0] > 0.0
    stitch_event = next(event for event in events if event["stage"] == "gen_bridge_stitch_done")
    assert stitch_event["data"]["seam_crossfade_seconds"] == generate_job.BRIDGE_SEAM_CROSSFADE_SECONDS
    assert stitch_event["data"]["action_part_end"] == 3.0 + generate_job.BRIDGE_SEAM_CROSSFADE_SECONDS
