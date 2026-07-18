"""In-memory stub implementations for local dev without API keys.

Each stub mirrors the real signature Person 3 will ship. Runway stubs simply
echo the source clip back so the whole pipeline (generate -> accept -> stitch ->
export) is end-to-end testable with a real video file and no network.
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path

from app.ai.services.types import (
    BBoxDict,
    EditPlan,
    VariantResult,
    QualityScore,
    EntityIdentity,
    EntityHit,
)


# ------------------------------ gemini ------------------------------

async def _gemini_interpret_edit(
    prompt: str,
    bbox: BBoxDict,
    frame_path: str,
) -> dict:
    from app.services.edit_scope import plan_prompt_intent

    intent = plan_prompt_intent(prompt).intent
    region = f"region at ({bbox['x']:.2f},{bbox['y']:.2f}) {bbox['w']:.2f}x{bbox['h']:.2f}"
    return {
        "decision": {
            "scope": intent.scope,
            "change_type": intent.change_type,
            "duration_policy": intent.duration_policy,
            "temporal_behavior": intent.temporal_behavior,
            "target_description": intent.target_description or region,
            "action_phases": intent.action_phases,
            "estimated_action_seconds": intent.estimated_action_seconds,
            "requires_recovery_motion": intent.requires_recovery_motion,
            "preservation_requirements": intent.preservation_requirements,
            "reasoning": "deterministic test interpretation",
        },
        "variants": [
            {
                "intent": intent.change_type,
                "conditioning_strategy": "first_frame",
                "description": f"Apply requested edit to {region}",
                "tone": "original visual tone unless explicitly changed",
                "color_grading": "original grade unless explicitly changed",
                "region_emphasis": region,
                "prompt_for_video_edit": (
                    f"Edit only the selected target in {region}. {prompt}. "
                    "Preserve all unselected content. Do not regenerate the scene."
                ),
            }
        ],
    }

async def _gemini_plan_variants(
    prompt: str,
    bbox: BBoxDict,
    frame_path: str,
) -> list[EditPlan]:
    interpreted = await _gemini_interpret_edit(prompt, bbox, frame_path)
    return interpreted["variants"]


async def _gemini_score_variant(
    frames: list[str],
    prompt: str,
    *,
    target_frame_paths: list[str] | None = None,
    reference_target_path: str | None = None,
) -> QualityScore:
    await asyncio.sleep(0.1)
    # deterministic-ish pseudo-score derived from prompt length so UI has something to show
    base = 6 + (len(prompt) % 4)
    return QualityScore(
        visual_coherence=min(10, base + 1),
        prompt_adherence=min(10, base),
    )


async def _gemini_identify_entity(crop_path: str) -> EntityIdentity:
    await asyncio.sleep(0.1)
    return EntityIdentity(
        description="stub entity (silver sedan)",
        category="vehicle",
        attributes={"color": "silver", "type": "sedan"},
    )


async def _gemini_find_entity_in_keyframes(
    entity: EntityIdentity,
    keyframes: list[str],
) -> list[EntityHit]:
    await asyncio.sleep(0.5)
    # pretend we find the entity in 1/3 of the keyframes for demo flow
    hits: list[EntityHit] = []
    for i, kf in enumerate(keyframes):
        if i % 3 == 1:
            hits.append(
                EntityHit(
                    start_ts=float(i),
                    end_ts=float(i) + 1.0,
                    keyframe_url=kf,
                    confidence=0.8,
                )
            )
    return hits


gemini = types.SimpleNamespace(
    interpret_edit=_gemini_interpret_edit,
    plan_variants=_gemini_plan_variants,
    score_variant=_gemini_score_variant,
    identify_entity=_gemini_identify_entity,
    find_entity_in_keyframes=_gemini_find_entity_in_keyframes,
)


# ------------------------------ wan ------------------------------

async def _wan_generate(
    clip_path: str,
    plan: EditPlan,
    style_ref: str | None = None,
    frame_path: str | None = None,
    on_tick=None,
    duration: int = 5,
    resolution: str = "1080P",
) -> VariantResult:
    import random
    delay = 1.5 + random.random() * 2.5
    await asyncio.sleep(delay)
    return VariantResult(
        url=Path(clip_path).as_posix(),
        description=plan["description"],
    )


wan = types.SimpleNamespace(
    generate=_wan_generate,
)


# ------------------------------ runway ------------------------------

async def _runway_generate(
    clip_path: str,
    plan: EditPlan,
    style_ref: str | None = None,
    frame_path: str | None = None,  # noqa: ARG001 — mirrors real adapter signature
    last_frame_path: str | None = None,  # noqa: ARG001
    subject_reference_path: str | None = None,  # noqa: ARG001
    start_anchor_path: str | None = None,  # noqa: ARG001
    end_anchor_path: str | None = None,  # noqa: ARG001
    source_video_url: str | None = None,  # noqa: ARG001
    mask_image_url: str | None = None,  # noqa: ARG001
    mask_frame_id: int = 1,  # noqa: ARG001
    on_tick=None,  # noqa: ARG001 — mirrors real adapter signature
    duration: int = 5,  # noqa: ARG001 — mirrors real adapter signature
    resolution: str = "720P",  # noqa: ARG001 — mirrors real adapter signature
) -> VariantResult:
    # staggered sleeps so progressive reveal is visible in the polling loop
    import random
    delay = 1.5 + random.random() * 2.5
    await asyncio.sleep(delay)
    # echo input clip back as the "generated" variant — pipeline works end-to-end
    return VariantResult(
        url=Path(clip_path).as_posix(),
        description=plan["description"],
    )


runway = types.SimpleNamespace(
    generate=_runway_generate,
)


# ------------------------------ elevenlabs ------------------------------

async def _elevenlabs_narrate(text: str) -> bytes:
    await asyncio.sleep(0.2)
    # tiny MP3 header sentinel; enough for the frontend to treat it as audio
    return b"\xff\xfb\x90\x00" + b"\x00" * 2048


elevenlabs = types.SimpleNamespace(
    narrate=_elevenlabs_narrate,
)


# ------------------------------ entity_tracker ------------------------------
# Re-exports for callers that want a single import surface.
entity_tracker = types.SimpleNamespace(
    identify=_gemini_identify_entity,
    search_keyframes=_gemini_find_entity_in_keyframes,
)
