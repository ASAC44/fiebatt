"""Tests for GPU worker (SAM + CLIP). Requires GPU_WORKER_URL reachable."""

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("GPU_WORKER_URL"),
    reason="GPU_WORKER_URL not set",
)


@pytest.mark.asyncio
async def test_gpu_worker_health():
    from ai.services.sam import is_available

    available = await is_available()
    assert available


@pytest.mark.asyncio
async def test_sam_segmentation(tmp_path):
    """Test that SAM produces a mask from a bbox."""
    import subprocess
    from ai.services.sam import bbox_to_mask

    # generate a test frame
    frame = str(tmp_path / "frame.png")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=1",
            "-frames:v", "1", frame,
        ],
        capture_output=True,
        check=True,
    )

    mask_path = await bbox_to_mask(
        frame,
        {"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3},
    )

    from pathlib import Path
    assert Path(mask_path).exists()
    assert Path(mask_path).stat().st_size > 0


@pytest.mark.asyncio
async def test_clip_similarity():
    """Test that CLIP returns embeddings and similarity works."""
    from ai.services.clip_search import cosine_similarity, embed_image
    import subprocess
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        # generate two test frames
        for i, color in enumerate(["red", "blue"]):
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s=64x64:d=1",
                    "-frames:v", "1", f"{tmp}/frame_{i}.png",
                ],
                capture_output=True,
                check=True,
            )

        emb_a = await embed_image(f"{tmp}/frame_0.png")
        emb_b = await embed_image(f"{tmp}/frame_1.png")

        # same image should have high self-similarity
        assert cosine_similarity(emb_a, emb_a) > 0.99
        # different images should have lower similarity
        assert cosine_similarity(emb_a, emb_b) < 0.95
