"""Tests for Qwen service. Requires DASHSCOPE_API_KEY or GEMINI_API_KEY in .env."""

import os
import json
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DASHSCOPE_API_KEY") and not os.getenv("GEMINI_API_KEY"),
    reason="No API key configured",
)


@pytest.mark.asyncio
async def test_create_edit_plan():
    from ai.services.qwen import create_edit_plan

    result = await create_edit_plan(
        prompt="make this car red",
        bbox={"x": 0.25, "y": 0.4, "w": 0.3, "h": 0.35},
        entity_description="silver sedan car",
    )

    assert "variants" in result
    assert len(result["variants"]) >= 1

    for variant in result["variants"]:
        assert "description" in variant
        assert "tone" in variant
        assert "prompt_for_veo" in variant or "prompt_for_runway" in variant


@pytest.mark.asyncio
async def test_generate_narration_script():
    from ai.services.qwen import generate_narration_script

    result = await generate_narration_script(
        variant_description="Deep cherry red with warm cinematic color grade",
        original_prompt="make this car red",
    )

    assert isinstance(result, str)
    assert len(result) > 10
    assert len(result) < 500