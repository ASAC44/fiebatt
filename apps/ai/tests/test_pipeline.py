"""End-to-end pipeline test through stubs.

Simulates the full iris flow:
  prompt -> edit plan -> generate 3 variants -> score -> accept -> entity search -> propagate

Runs entirely with stubs, no API keys needed.
"""

import os
import pytest

os.environ["USE_AI_STUBS"] = "true"


@pytest.mark.asyncio
async def test_full_edit_loop():
    """Run the complete scrub-select-prompt-generate-compare-accept loop."""
    from ai.services._stubs import gemini, runway

    # Step 1: user provides prompt + bbox
    prompt = "make this car red"
    bbox = {"x": 0.25, "y": 0.4, "w": 0.3, "h": 0.35}

    # Step 2: gemini creates edit plan with 3 variants
    plans = await gemini.plan_variants(prompt, bbox, "/tmp/frame.png")
    assert len(plans) == 3

    # Step 3: generate 3 variants in parallel (via stubs)
    variants = []
    for plan in plans:
        result = await runway.generate("/tmp/clip.mp4", plan)
        assert "url" in result
        assert "description" in result
        variants.append(result)

    assert len(variants) == 3

    # Step 4: score each variant
    scores = []
    for _ in variants:
        score = await gemini.score_variant(
            ["/tmp/f1.png", "/tmp/f2.png", "/tmp/f3.png"],
            prompt,
        )
        assert 1 <= score["visual_coherence"] <= 10
        assert 1 <= score["prompt_adherence"] <= 10
        scores.append(score)

    assert len(scores) == 3

    # Step 5: user picks variant 0 (best score)
    chosen = variants[0]
    assert chosen["url"] is not None


@pytest.mark.asyncio
async def test_entity_tracking_loop():
    """Run the entity identification and search flow."""
    from ai.services._stubs import gemini

    # Step 1: identify entity from bbox crop
    entity = await gemini.identify_entity("/tmp/crop.png")
    assert "description" in entity
    assert "category" in entity

    # Step 2: search keyframes for entity
    keyframes = [f"/tmp/kf_{i}.png" for i in range(30)]
    hits = await gemini.find_entity_in_keyframes(entity, keyframes)

    assert isinstance(hits, list)
    # stubs find entity in ~1/3 of keyframes
    assert len(hits) > 0
    assert len(hits) <= len(keyframes)

    # Step 3: each hit has valid timestamps
    for hit in hits:
        assert hit["start_ts"] >= 0
        assert hit["end_ts"] > hit["start_ts"]
        assert 0 <= hit["confidence"] <= 1


@pytest.mark.asyncio
async def test_narration_flow():
    """Run the narration generation flow."""
    from ai.services._stubs import gemini, elevenlabs

    # Step 1: generate narration script (simulated via plan description)
    description = "Deep cherry red with warm cinematic color grade"

    # Step 2: generate audio
    audio_bytes = await elevenlabs.narrate(
        f"Watch as the silver sedan transforms. {description}"
    )

    assert isinstance(audio_bytes, bytes)
    assert len(audio_bytes) > 0


@pytest.mark.asyncio
async def test_propagation_flow():
    """Run the continuity propagation flow."""
    from ai.services._stubs import gemini, runway

    # Step 1: identify entity
    entity = await gemini.identify_entity("/tmp/crop.png")

    # Step 2: find appearances
    keyframes = [f"/tmp/kf_{i}.png" for i in range(20)]
    hits = await gemini.find_entity_in_keyframes(entity, keyframes)

    # Step 3: propagate — generate 1 variant per segment with style reference
    plan = {
        "description": "red car propagation",
        "tone": "cinematic",
        "color_grading": "warm",
        "region_emphasis": "car",
        "prompt_for_runway": "make the car red, matching previous edit",
    }

    propagated = []
    for hit in hits[:3]:  # propagate to first 3 segments
        result = await runway.generate(
            "/tmp/clip.mp4",
            plan,
            style_ref="/tmp/style_ref.png",
        )
        assert "url" in result
        propagated.append(result)

    assert len(propagated) > 0
