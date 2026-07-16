"""Tests for Google Veo video generation. Requires GEMINI_API_KEY in .env.

WARNING: These tests make real API calls and generate actual video.
They cost money and take 30-120 seconds each. Run sparingly.
"""

import os
import pytest
from pathlib import Path

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY not set",
    ),
    pytest.mark.slow,
]


@pytest.mark.asyncio
async def test_generate_single_variant():
    from app.ai.services.veo import generate_variant

    path = await generate_variant(
        prompt="A silver sedan car driving down a city street, the car transforms from silver to deep cherry red, cinematic warm lighting, 4K",
        duration=4,
    )

    assert Path(path).exists()
    assert Path(path).stat().st_size > 0
    assert path.endswith(".mp4")
