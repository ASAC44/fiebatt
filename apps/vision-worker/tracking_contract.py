from __future__ import annotations

from typing import Any


def bounded_window(total_frames: int, seed_index: int, max_frames: int) -> tuple[int, int, int]:
    """Return source start/end-exclusive and seed index inside the bounded window."""
    if total_frames < 1:
        raise ValueError("at least one frame is required")
    if seed_index < 0 or seed_index >= total_frames:
        raise ValueError("seed index outside frames")
    if max_frames < 1:
        raise ValueError("max_frames must be positive")
    half_before = max_frames // 2
    start = max(0, seed_index - half_before)
    end = min(total_frames, start + max_frames)
    start = max(0, end - max_frames)
    return start, end, seed_index - start


def stub_frame(frame_index: int, bbox: dict[str, float]) -> dict[str, Any]:
    return {
        "frame_index": frame_index,
        "bbox": dict(bbox),
        "mask_b64": None,
        "confidence": 1.0,
        "state": "tracked",
    }
