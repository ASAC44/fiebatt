from __future__ import annotations

import base64

import cv2
import numpy as np

from app.ai.services.sam import TrackResult
from app.services.local_compositor import (
    evaluate_output_track,
    feathered_composite_frames,
)


def _mask_b64(mask: np.ndarray) -> str:
    ok, encoded = cv2.imencode(".png", mask.astype(np.uint8) * 255)
    assert ok
    return base64.b64encode(encoded).decode()


def _track(masks: list[np.ndarray], *, lost_index: int | None = None) -> TrackResult:
    frames = []
    for index, mask in enumerate(masks):
        lost = index == lost_index
        frames.append(
            {
                "frame_index": index,
                "state": "lost" if lost else "tracked",
                "confidence": 0.2 if lost else 0.94,
                "mask_b64": None if lost else _mask_b64(mask),
            }
        )
    return TrackResult(
        tracker="sam2_video",
        frames=frames,
        processed_start_index=0,
        processed_end_index=len(masks) - 1,
    )


def _moving_masks() -> list[np.ndarray]:
    masks = []
    for x in (8, 10, 12):
        mask = np.zeros((48, 64), dtype=bool)
        mask[12:38, x : x + 16] = True
        masks.append(mask)
    return masks


def test_complete_high_confidence_generated_track_is_allowed():
    masks = _moving_masks()
    decision, decoded = evaluate_output_track(
        _track(masks),
        frame_count=3,
        frame_shape=(48, 64),
        seed_confidence=0.91,
    )
    assert decision.allowed is True
    assert len(decoded) == 3


def test_mask_loss_falls_back_to_provider_native_output():
    decision, masks = evaluate_output_track(
        _track(_moving_masks(), lost_index=1),
        frame_count=3,
        frame_shape=(48, 64),
        seed_confidence=0.91,
    )
    assert decision.allowed is False
    assert decision.reason == "generated target was lost"
    assert masks == ()


def test_stub_or_incomplete_tracking_is_never_used_for_compositing():
    result = _track(_moving_masks())
    result = TrackResult(
        tracker="stub",
        frames=result.frames[:-1],
        processed_start_index=0,
        processed_end_index=1,
        warning="bbox fallback",
    )
    decision, _ = evaluate_output_track(
        result,
        frame_count=3,
        frame_shape=(48, 64),
        seed_confidence=0.99,
    )
    assert decision.allowed is False


def test_changed_pose_uses_generated_output_mask_not_source_mask():
    source = tuple(np.full((48, 64, 3), 20, dtype=np.uint8) for _ in range(3))
    generated = []
    for mask in _moving_masks():
        frame = np.full((48, 64, 3), 200, dtype=np.uint8)
        frame[mask] = (0, 0, 255)
        generated.append(frame)
    masks = tuple(_moving_masks())
    output = feathered_composite_frames(source, tuple(generated), masks)

    # Last pose moved beyond the first/source mask. Generated red pixels must
    # survive there; using the original mask for every frame would clip them.
    assert output[-1][24, 26, 2] > 150
    # Far background stays exactly original instead of provider-regenerated.
    assert np.array_equal(output[-1][5, 55], source[-1][5, 55])


def test_feathered_edge_avoids_binary_halo():
    source = (np.zeros((48, 64, 3), dtype=np.uint8),)
    generated = (np.full((48, 64, 3), 255, dtype=np.uint8),)
    mask = np.zeros((48, 64), dtype=bool)
    mask[12:36, 20:44] = True
    output = feathered_composite_frames(source, generated, (mask,))[0]
    edge_value = int(output[24, 20, 0])
    assert 0 < edge_value < 255
    assert int(output[24, 32, 0]) > 245
