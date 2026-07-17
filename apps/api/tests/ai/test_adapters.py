"""Tests for the adapter layer between backend workers and AI services.

Validates that:
1. Stubs return correct shapes matching TypedDict contracts
2. Real adapters normalize field names correctly
3. Config switch routes to the right implementation
"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Force stubs mode for these tests
os.environ["USE_AI_STUBS"] = "true"


@pytest.mark.asyncio
async def test_qwen_json_mode_explicitly_requests_json(monkeypatch):
    from app.ai.services import qwen

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
    )
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    monkeypatch.setattr(qwen, "_make_client", lambda **_kwargs: client)
    monkeypatch.setattr(
        qwen,
        "_provider_config",
        lambda **_kwargs: ("test-key", qwen.QWEN_BASE_URL, qwen.DEFAULT_MODEL),
    )

    result = await qwen._chat_json(
        "Plan the edit.",
        [{"type": "text", "text": "make the car green"}],
    )

    request = client.chat.completions.create.await_args.kwargs
    assert result == '{"ok": true}'
    assert "json" in request["messages"][0]["content"].lower()
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_stub_plan_variants():
    """gemini.plan_variants returns one grounded edit instruction."""
    from app.ai.services._stubs import gemini

    plans = await gemini.plan_variants(
        "make this car red",
        {"x": 0.25, "y": 0.4, "w": 0.3, "h": 0.35},
        "/tmp/frame.png",
    )

    assert len(plans) == 1
    for plan in plans:
        assert "description" in plan
        assert "tone" in plan
        assert "color_grading" in plan
        assert "region_emphasis" in plan
        assert "prompt_for_video_edit" in plan
        assert isinstance(plan["prompt_for_video_edit"], str)
        assert len(plan["prompt_for_video_edit"]) > 0


@pytest.mark.asyncio
async def test_stub_interpretation_separates_state_from_action():
    from app.ai.services._stubs import gemini

    bbox = {"x": 0.25, "y": 0.4, "w": 0.3, "h": 0.35}
    state = await gemini.interpret_edit("make this ball pink", bbox, "")
    action = await gemini.interpret_edit("make this ball bounce", bbox, "")

    assert state["decision"]["duration_policy"] == "continuous_occurrence"
    assert action["decision"]["duration_policy"] == "bounded_action"


@pytest.mark.asyncio
async def test_stub_score_variant():
    """gemini.score_variant returns {visual_coherence, prompt_adherence} ints 1-10."""
    from app.ai.services._stubs import gemini

    score = await gemini.score_variant(
        ["/tmp/f1.png", "/tmp/f2.png", "/tmp/f3.png"],
        "make this car red",
    )

    assert "visual_coherence" in score
    assert "prompt_adherence" in score
    assert 1 <= score["visual_coherence"] <= 10
    assert 1 <= score["prompt_adherence"] <= 10


@pytest.mark.asyncio
async def test_qwen_quality_request_labels_source_and_target_crops(monkeypatch, tmp_path):
    from app.ai.services import qwen

    paths = {}
    for name in ("source", "full", "target"):
        path = tmp_path / f"{name}.png"
        path.write_bytes(name.encode())
        paths[name] = str(path)

    captured: dict[str, list[dict]] = {}

    async def fake_chat_json(system_prompt, user_content, model=qwen.DEFAULT_MODEL):
        captured["content"] = user_content
        return '{"visual_coherence": 8, "prompt_adherence": 1, "evidence": ["car is white"]}'

    monkeypatch.setattr(qwen, "_chat_json", fake_chat_json)
    score = await qwen.score_variant(
        "Make the car green",
        [paths["full"]],
        target_frame_paths=[paths["target"]],
        reference_target_path=paths["source"],
    )

    labels = [
        item["text"]
        for item in captured["content"]
        if item["type"] == "text"
    ]
    assert any("SOURCE TARGET BEFORE EDIT" in label for label in labels)
    assert any("GENERATED FULL FRAME" in label for label in labels)
    assert any("GENERATED TARGET CROP" in label for label in labels)
    assert score["prompt_adherence"] == 1
    assert score["evidence"] == ["car is white"]


@pytest.mark.asyncio
async def test_stub_identify_entity():
    """gemini.identify_entity returns {description, category, attributes}."""
    from app.ai.services._stubs import gemini

    entity = await gemini.identify_entity("/tmp/crop.png")

    assert "description" in entity
    assert "category" in entity
    assert "attributes" in entity
    assert isinstance(entity["description"], str)


@pytest.mark.asyncio
async def test_stub_find_entity_in_keyframes():
    """gemini.find_entity_in_keyframes returns list of EntityHit dicts."""
    from app.ai.services._stubs import gemini

    entity = {"description": "silver sedan", "category": "vehicle", "attributes": {}}
    keyframes = [f"/tmp/kf_{i}.png" for i in range(10)]

    hits = await gemini.find_entity_in_keyframes(entity, keyframes)

    assert isinstance(hits, list)
    for hit in hits:
        assert "start_ts" in hit
        assert "end_ts" in hit
        assert "keyframe_url" in hit
        assert "confidence" in hit
        assert isinstance(hit["confidence"], float)
        assert 0 <= hit["confidence"] <= 1


@pytest.mark.asyncio
async def test_stub_runway_generate():
    """runway.generate returns {url, description}."""
    from app.ai.services._stubs import runway

    plan = {
        "description": "red car",
        "tone": "cinematic",
        "color_grading": "warm",
        "region_emphasis": "center",
        "prompt_for_runway": "make the car red",
    }

    result = await runway.generate("/tmp/clip.mp4", plan)

    assert "url" in result
    assert "description" in result
    assert isinstance(result["url"], str)


@pytest.mark.asyncio
async def test_stub_elevenlabs_narrate():
    """elevenlabs.narrate returns bytes."""
    from app.ai.services._stubs import elevenlabs

    audio = await elevenlabs.narrate("The car transforms to red")

    assert isinstance(audio, bytes)
    assert len(audio) > 0


class TestRealAdapters:
    """Test that real adapters normalize field names correctly.

    Only runs when GEMINI_API_KEY is set.
    """

    pytestmark = pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY not set",
    )

    @pytest.mark.asyncio
    async def test_real_plan_variants_normalizes_field_names(self):
        """prompt_for_veo gets aliased to prompt_for_runway."""
        os.environ["USE_AI_STUBS"] = "false"

        from app.ai.services.gemini import create_edit_plan

        plan = await create_edit_plan(
            prompt="make this car red",
            bbox={"x": 0.25, "y": 0.4, "w": 0.3, "h": 0.35},
        )

        for variant in plan.get("variants", []):
            assert "prompt_for_veo" in variant

        os.environ["USE_AI_STUBS"] = "true"

    @pytest.mark.asyncio
    async def test_real_identify_entity_has_visual_attributes(self):
        """Real identify_entity returns visual_attributes (adapter renames to attributes)."""
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # generate a test frame
            subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", "testsrc=duration=1:size=640x360:rate=1",
                    "-frames:v", "1", f"{tmp}/frame.png",
                ],
                capture_output=True, check=True,
            )

            from app.ai.services.gemini import identify_entity
            result = await identify_entity(f"{tmp}/frame.png")

            assert "description" in result
            assert "category" in result
            # real module returns visual_attributes
            assert "visual_attributes" in result


def test_wan_video_edit_payload_targets_isolated_reference_and_source(tmp_path):
    """Wan edits must receive the source video and identify the isolated target."""
    from app.ai.services.wan import _build_video_edit_payload

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"not-a-real-png-but-valid-fixture-for-base64-shape")
    payload = _build_video_edit_payload(
        "Make only the man jump up and down while walking.",
        "https://cdn.example.test/source.mp4",
        reference_frame_path=str(frame),
        resolution="720P",
    )

    assert payload["input"]["media"][0] == {
        "type": "video",
        "url": "https://cdn.example.test/source.mp4",
    }
    assert payload["input"]["media"][1]["type"] == "reference_image"
    assert "exact isolated target subject" in payload["input"]["prompt"].lower()
    assert "every other person and object" in payload["input"]["prompt"].lower()
    assert "ghosting" in payload["input"]["negative_prompt"]
    assert payload["parameters"]["resolution"] == "720P"


def test_wan_local_edit_payload_uses_tracked_sam_mask():
    from app.ai.services.wan import _build_local_edit_payload

    payload = _build_local_edit_payload(
        "Make the selected person jump.",
        "https://cdn.example.test/source.mp4",
        "https://cdn.example.test/mask.png",
        mask_frame_id=42,
    )

    assert payload["input"] == {
        "prompt": "Make the selected person jump.",
        "function": "video_edit",
        "video_url": "https://cdn.example.test/source.mp4",
        "mask_image_url": "https://cdn.example.test/mask.png",
        "mask_frame_id": 42,
    }
    assert payload["parameters"]["mask_type"] == "tracking"
    assert payload["parameters"]["expand_mode"] == "original"


def test_happyhorse_keeps_subject_reference_out_of_first_frame_slot(tmp_path):
    from app.ai.services.happyhorse import _build_generation_payload

    subject = tmp_path / "subject.png"
    subject.write_bytes(b"subject")
    model, payload, _ = _build_generation_payload(
        "Make only the selected person jump.",
        reference_frame_path=str(subject),
        source_video_url="https://cdn.example.test/context.mp4",
        duration=5,
    )

    assert "video-edit" in model
    assert [item["type"] for item in payload["input"]["media"]] == [
        "video",
        "reference_image",
    ]


def test_happyhorse_image_generation_uses_full_start_boundary(tmp_path):
    from app.ai.services.happyhorse import _build_generation_payload

    start_anchor = tmp_path / "start-full-frame.jpg"
    start_anchor.write_bytes(b"full-frame")
    model, payload, _ = _build_generation_payload(
        "Make the selected person jump.",
        reference_frame_path=str(start_anchor),
        source_video_url=None,
        duration=5,
    )

    assert "i2v" in model
    assert payload["input"]["media"][0]["type"] == "first_frame"


@pytest.mark.asyncio
async def test_veo_uses_full_start_and_end_boundary_frames(monkeypatch, tmp_path):
    from app.ai.services import veo

    start_anchor = tmp_path / "start.jpg"
    end_anchor = tmp_path / "end.jpg"
    start_anchor.write_bytes(b"start-full-frame")
    end_anchor.write_bytes(b"end-full-frame")
    captured = {}

    async def capture_generation(**kwargs):
        captured.update(kwargs)
        return str(tmp_path / "result.mp4")

    monkeypatch.setattr(veo, "_generate_and_download", capture_generation)
    await veo.generate_variant(
        "Make the selected person jump.",
        reference_frame_path=str(start_anchor),
        last_frame_path=str(end_anchor),
        duration=8,
    )

    assert captured["image"].image_bytes == b"start-full-frame"
    assert captured["config"].last_frame.image_bytes == b"end-full-frame"


def test_video_provider_aliases_normalize_to_runtime_provider():
    from app.ai.services.config import Settings

    assert Settings(VIDEO_GEN_PROVIDER="auto").normalized_video_gen_provider == "auto"
    assert Settings(VIDEO_GEN_PROVIDER="wan").normalized_video_gen_provider == "wan"
    assert Settings(VIDEO_GEN_PROVIDER="veo").normalized_video_gen_provider == "veo"
    assert Settings(VIDEO_GEN_PROVIDER="happyhorse").normalized_video_gen_provider == "happyhorse"
    assert Settings(VIDEO_GEN_PROVIDER="veo").video_gen_provider_label == "Veo"
    assert Settings(VIDEO_GEN_PROVIDER="happyhorse").video_gen_provider_label == "HappyHorse"
    assert Settings(VIDEO_GEN_PROVIDER="auto").video_gen_provider_label == "Auto"


def test_vision_worker_url_accepts_legacy_environment_name():
    from app.ai.services.config import Settings

    canonical = Settings(VISION_WORKER_URL="https://vision.example.test")
    legacy = Settings(GPU_WORKER_URL="https://legacy.example.test")

    assert canonical.vision_worker_url == "https://vision.example.test"
    assert legacy.vision_worker_url == "https://legacy.example.test"


def test_segmentation_worker_can_be_configured_separately():
    from app.ai.services.config import Settings

    settings = Settings(SAM_SEGMENTATION_URL="https://segment.hf.space")

    assert settings.sam_segmentation_url == "https://segment.hf.space"


def test_veo_reference_images_include_style_and_asset(tmp_path):
    from app.ai.services.veo import _build_reference_images

    style = tmp_path / "style.png"
    frame = tmp_path / "frame.png"
    style.write_bytes(b"style")
    frame.write_bytes(b"frame")

    refs = _build_reference_images(
        style_reference_path=str(style),
        reference_frame_path=str(frame),
    )

    assert len(refs) == 2
    assert refs[0].reference_type.value == "STYLE"
    assert refs[1].reference_type.value == "ASSET"


def test_motion_prompt_rewrite_preserves_sequence_language():
    from app.ai.services.__init__ import _rewrite_motion_prompt

    motion, sequenced, rewritten = _rewrite_motion_prompt(
        "The man jumps up and down a few times, then lands and smoothly continues into a normal walk, without stopping."
    )

    assert motion is True
    assert sequenced is True
    assert "Do not loop, extend, or repeat the action beyond the requested count." in rewritten
    assert "blend smoothly back into the original gait and forward momentum" in rewritten
    assert "exactly three distinct repetitions" in rewritten
    assert "perform the requested action clearly and repeatedly" not in rewritten
