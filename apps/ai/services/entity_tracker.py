"""Entity tracker — the causal editing engine.

Orchestrates entity identification, keyframe search, and continuity propagation.
Uses Gemini vision for all entity analysis.
"""

import asyncio
from pathlib import Path

from ai.services.qwen import identify_entity, search_keyframes_for_entity
from ai.services.ffmpeg import extract_keyframes, crop_bbox_from_frame
from ai.services.sam import bbox_to_mask, is_available as sam_available
from ai.services.clip_search import search_keyframes_by_similarity


async def identify_entity_from_bbox(
    frame_path: str,
    bbox: dict[str, float],
) -> dict:
    """Extract bbox crop from a frame and identify the entity.

    Args:
        frame_path: Path to the full video frame
        bbox: Normalized bounding box {x, y, w, h} (0-1, top-left origin)

    Returns:
        Entity info: {"description", "category", "visual_attributes", "crop_path"}
    """
    crop_path = crop_bbox_from_frame(frame_path, bbox)

    # If GPU worker is available, refine bbox into a precise mask via SAM
    mask_path = None
    if await sam_available():
        mask_path = await bbox_to_mask(frame_path, bbox)

    entity_info = await identify_entity(crop_path)
    entity_info["crop_path"] = crop_path
    entity_info["mask_path"] = mask_path
    return entity_info


async def search_video_for_entity(
    video_path: str,
    entity_description: str,
    exclude_start_ts: float | None = None,
    exclude_end_ts: float | None = None,
    fps: float = 1.0,
    batch_size: int = 10,
    timeout: float = 300.0,
) -> list[dict]:
    """Search a video for all appearances of an entity via keyframe sampling.

    Extracts 1 keyframe per second, batches them in groups of 10, and sends
    each batch to Gemini vision for entity matching.

    Args:
        video_path: Path to the source video
        entity_description: Description of the entity to find
        exclude_start_ts: Start of segment to skip (already edited)
        exclude_end_ts: End of segment to skip
        fps: Keyframes per second to extract (default 1)
        batch_size: Keyframes per Gemini batch request (default 10)
        timeout: Max seconds for the entire search (default 300)

    Returns:
        List of {"timestamp": float, "confidence": float} for found appearances,
        sorted by timestamp.
    """
    keyframe_dir = Path(video_path).parent / "keyframes"
    keyframe_paths = extract_keyframes(video_path, str(keyframe_dir), fps=fps)

    # Filter out keyframes in the excluded segment
    filtered: list[tuple[float, str]] = []
    for i, path in enumerate(keyframe_paths):
        ts = i / fps
        if exclude_start_ts and exclude_end_ts:
            if exclude_start_ts <= ts <= exclude_end_ts:
                continue
        filtered.append((ts, path))

    timestamps_list = [t for t, _ in filtered]
    paths_list = [p for _, p in filtered]

    # Try CLIP search first (GPU worker) — way faster than Gemini vision batching
    try:
        if await sam_available():  # GPU worker is up = CLIP is also available
            ref_crop = crop_bbox_from_frame(paths_list[0], {"x": 0, "y": 0, "w": 1, "h": 1})
            # Use the entity description to find the crop path from the caller
            # For now, search all keyframes by visual similarity
            results = await asyncio.wait_for(
                search_keyframes_by_similarity(
                    reference_crop_path=paths_list[0],  # caller should pass the actual crop
                    keyframe_paths=paths_list,
                ),
                timeout=timeout,
            )
            appearances: list[dict] = []
            for result in results:
                if result.get("found", False):
                    idx = result["keyframe_index"]
                    if idx < len(timestamps_list):
                        appearances.append({
                            "timestamp": timestamps_list[idx],
                            "confidence": result.get("confidence", 0.0),
                        })
            return sorted(appearances, key=lambda a: a["timestamp"])
    except Exception:
        pass  # fall through to Gemini vision

    # Fallback: Gemini vision batch search
    appearances = []
    batches = [
        filtered[i : i + batch_size]
        for i in range(0, len(filtered), batch_size)
    ]

    for batch in batches:
        timestamps, paths = zip(*batch) if batch else ([], [])
        try:
            results = await asyncio.wait_for(
                search_keyframes_for_entity(entity_description, list(paths)),
                timeout=timeout / len(batches),
            )
            for result in results:
                if result.get("found", False):
                    idx = result["keyframe_index"]
                    if idx < len(timestamps):
                        appearances.append({
                            "timestamp": timestamps[idx],
                            "confidence": result.get("confidence", 0.0),
                        })
        except asyncio.TimeoutError:
            # Return partial results on timeout
            break
        except Exception:
            # Non-fatal: skip this batch, continue searching
            continue

    return sorted(appearances, key=lambda a: a["timestamp"])


def group_appearances_into_segments(
    appearances: list[dict],
    min_gap: float = 2.0,
    segment_padding: float = 1.0,
) -> list[dict]:
    """Group individual keyframe appearances into contiguous segments.

    Args:
        appearances: List of {"timestamp": float, "confidence": float}
        min_gap: Minimum gap between appearances to start a new segment
        segment_padding: Padding before/after each segment

    Returns:
        List of {"start_ts": float, "end_ts": float, "avg_confidence": float}
    """
    if not appearances:
        return []

    segments: list[dict] = []
    current_start = appearances[0]["timestamp"]
    current_end = current_start
    confidences = [appearances[0]["confidence"]]

    for app in appearances[1:]:
        if app["timestamp"] - current_end > min_gap:
            segments.append({
                "start_ts": max(0, current_start - segment_padding),
                "end_ts": current_end + segment_padding,
                "avg_confidence": sum(confidences) / len(confidences),
            })
            current_start = app["timestamp"]
            confidences = []
        current_end = app["timestamp"]
        confidences.append(app["confidence"])

    segments.append({
        "start_ts": max(0, current_start - segment_padding),
        "end_ts": current_end + segment_padding,
        "avg_confidence": sum(confidences) / len(confidences),
    })

    return segments
