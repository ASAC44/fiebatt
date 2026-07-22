"""ai/services facade.

This module defines the stable surface that [apps/api/app/workers/](apps/api/app/workers/)
imports. The backend expects:

    gemini.plan_variants(prompt, bbox, frame_path) -> list[EditPlan]
    gemini.score_variant(frames, prompt) -> {visual_coherence, prompt_adherence}
    gemini.identify_entity(crop_path) -> {description, category, attributes}
    gemini.find_entity_in_keyframes(entity, keyframes) -> list[EntityHit]
    runway.generate(clip_path, plan, style_ref=None) -> {url, description}
    elevenlabs.narrate(text) -> bytes

Two modes:
  - USE_AI_STUBS=true  (default) -> route to _stubs.py, no API keys needed
  - USE_AI_STUBS=false          -> adapt the real modules (Qwen/DashScope/Mesh for VLM,
                                   Veo/Wan/HappyHorse/Mesh API Veo for video generation,
                                   ElevenLabs for TTS) to the surface above.

Name mapping from real modules:
  qwen.create_edit_plan              -> gemini.plan_variants
  qwen.score_variant                -> gemini.score_variant   (arg order adapted)
  qwen.identify_entity              -> gemini.identify_entity (attr rename)
  qwen.search_keyframes_for_entity -> gemini.find_entity_in_keyframes
  veo.generate_variant              -> runway.generate
  happyhorse.generate_variant       -> runway.generate
  elevenlabs.generate_narration     -> elevenlabs.narrate    (path -> bytes)
"""
from __future__ import annotations

import json
import re
import types as _types
from pathlib import Path

from app.ai.services import _stubs
from app.ai.services import sam
from app.ai.services.conditioning import (
    GenerationConditioning,
    route_provider_conditioning,
)
from app.ai.services.config import get_settings as _get_ai_settings
from app.ai.services.provider_capabilities import select_source_edit_mode


def _load_backend_stub_mode() -> bool | None:
    try:
        from app.config.settings import get_settings as _get_backend_settings
    except ModuleNotFoundError:
        return None
    return _get_backend_settings().use_ai_stubs


def _resolve_use_ai_stubs() -> bool:
    backend_value = _load_backend_stub_mode()
    if backend_value is not None:
        return backend_value
    return _get_ai_settings().use_ai_stubs


_USE_AI_STUBS = _resolve_use_ai_stubs()


_MOTION_EDIT_RE = re.compile(
    r"\b(jump|jumping|bounce|bouncing|run|running|walk|walking|dance|dancing|wave|waving|"
    r"turn|turning|spin|spinning|sit|sitting|stand|standing|kick|throw|"
    r"catch|clap|clapping|nod|bow|fall|falls|leap|leaping)\b"
)
_SEQUENCED_MOTION_RE = re.compile(
    r"\b(then|after|before|followed by|continues?|continue into|resume|"
    r"resumes?|lands?|smoothly|without stopping|without pause|a few times?|"
    r"once|twice|three times?)\b"
)


def _rewrite_motion_prompt(prompt_text: str) -> tuple[bool, bool, str]:
    lowered = prompt_text.lower()
    motion_edit = bool(_MOTION_EDIT_RE.search(lowered))
    sequenced_motion = motion_edit and bool(_SEQUENCED_MOTION_RE.search(lowered))
    if not motion_edit:
        return False, False, prompt_text
    if sequenced_motion:
        count_instruction = (
            "Interpret 'a few times' as exactly three distinct repetitions. "
            if re.search(r"\ba few times?\b", lowered)
            else ""
        )
        return (
            True,
            True,
            "MANDATORY MOTION EDIT: honor the requested action timing exactly. "
            "Keep the movement phases in the order the user described. Do not "
            "loop, extend, or repeat the action beyond the requested count. "
            "When the prompt asks the subject to resume normal motion, blend "
            "smoothly back into the original gait and forward momentum without "
            "a stop, freeze, or hard reset. Keep the subject identity, scene, "
            "camera, lighting, and background consistent. "
            + count_instruction
            + "\n\n"
            + prompt_text,
        )
    return (
        True,
        False,
        "MANDATORY MOTION EDIT: perform the requested action clearly. Do not "
        "merely reproduce the original motion or leave the subject unchanged. "
        "Keep the subject identity, scene, camera, lighting, and background "
        "consistent.\n\n"
        + prompt_text,
    )


if _USE_AI_STUBS:
    gemini = _stubs.gemini
    runway = _stubs.runway
    elevenlabs = _stubs.elevenlabs
    entity_tracker = _stubs.entity_tracker

else:
    _real_settings = _get_ai_settings()

    from app.services import storage
    from app.ai.services import qwen as _gemini_real
    from app.ai.services import veo as _veo
    from app.ai.services import meshapi_veo as _meshapi_veo
    from app.ai.services import wan as _wan
    from app.ai.services import happyhorse as _happyhorse
    from app.ai.services import elevenlabs as _el_real
    from app.ai.services import entity_tracker as _et_real

    # ------------------------ gemini adapter ------------------------

    # simple in-memory plan cache: keyed on (prompt, bbox_string), max 32 entries
    _plan_cache: dict[str, dict] = {}
    _plan_cache_order: list[str] = []
    _PLAN_CACHE_MAX = 32

    async def _interpret_edit(prompt: str, bbox: dict, frame_path: str) -> dict:
        # Include visual source identity. Same words and bbox coordinates can
        # describe completely different targets in different frames.
        cache_key = f"{prompt}|{json.dumps(bbox, sort_keys=True)}|{frame_path}"
        cached = _plan_cache.get(cache_key)
        if cached is not None:
            return cached

        plan = await _gemini_real.create_edit_plan(
            prompt, bbox, frame_path=frame_path
        )
        _plan_cache[cache_key] = plan
        _plan_cache_order.append(cache_key)
        if len(_plan_cache_order) > _PLAN_CACHE_MAX:
            oldest = _plan_cache_order.pop(0)
            _plan_cache.pop(oldest, None)
        return plan

    async def _plan_variants(prompt: str, bbox: dict, frame_path: str) -> list[dict]:
        plan = await _interpret_edit(prompt, bbox, frame_path)
        variants = plan.get("variants") if isinstance(plan, dict) else None
        if not variants:
            return []
        # The prompt schema uses prompt_for_veo; workers use prompt_for_runway.
        # Normalize so downstream code always finds prompt_for_runway, and
        # give every variant a conditioning_strategy default so the worker
        # never has to second-guess a missing field.
        for v in variants:
            v.setdefault(
                "prompt_for_runway",
                v.get("prompt_for_video_edit") or v.get("prompt_for_veo", ""),
            )
            strategy = str(v.get("conditioning_strategy", "")).lower()
            if strategy not in ("first_frame", "text_only"):
                intent = str(v.get("intent", "")).lower()
                # always use first_frame — text_only produces garbage output
                # because veo has no visual context for the scene
                v["conditioning_strategy"] = "first_frame"

        return variants

    async def _score_variant(
        frames: list[str] | None = None,
        prompt: str = "",
        *,
        original_prompt: str | None = None,
        variant_frame_paths: list[str] | None = None,
        target_frame_paths: list[str] | None = None,
        reference_target_path: str | None = None,
    ) -> dict:
        # accept both positional (frames, prompt) and keyword (original_prompt, variant_frame_paths) calling conventions
        p = original_prompt or prompt
        f = variant_frame_paths or frames or []
        return await _gemini_real.score_variant(
            original_prompt=p,
            variant_frame_paths=f,
            target_frame_paths=target_frame_paths,
            reference_target_path=reference_target_path,
        )

    async def _identify_entity(crop_path: str) -> dict:
        result = await _gemini_real.identify_entity(crop_path)
        # rename visual_attributes -> attributes to match the backend's schema
        if isinstance(result, dict) and "visual_attributes" in result:
            result["attributes"] = result.pop("visual_attributes")
        result.setdefault("attributes", {})
        return result

    async def _find_entity_in_keyframes(entity: dict, keyframes: list[str]) -> list[dict]:
        description = entity.get("description", "")
        out: list[dict] = []
        for batch_start in range(0, len(keyframes), 10):
            batch = keyframes[batch_start : batch_start + 10]
            hits = await _gemini_real.search_keyframes_for_entity(description, batch)
            # Stephen returns [{keyframe_index, confidence, found}].
            # Convert to the EntityHit shape the backend expects. Assume 1fps
            # sampling (backend's entity_job sets KEYFRAMES_PER_SECOND=1.0).
            for h in hits:
                if not h.get("found"):
                    continue
                idx = batch_start + int(h.get("keyframe_index", 0))
                out.append({
                    "start_ts": float(idx),
                    "end_ts": float(idx) + 1.0,
                    "keyframe_url": keyframes[idx] if idx < len(keyframes) else "",
                    "confidence": float(h.get("confidence", 0.0)),
                })
        return out

    gemini = _types.SimpleNamespace(
        interpret_edit=_interpret_edit,
        plan_variants=_plan_variants,
        score_variant=_score_variant,
        identify_entity=_identify_entity,
        find_entity_in_keyframes=_find_entity_in_keyframes,
    )

    # ------------------------ runway adapter (wan | veo | happyhorse) ------------------------

    async def _runway_generate(
        clip_path: str,
        plan: dict,
        style_ref: str | None = None,
        frame_path: str | None = None,
        last_frame_path: str | None = None,
        subject_reference_path: str | None = None,
        start_anchor_path: str | None = None,
        end_anchor_path: str | None = None,
        source_video_url: str | None = None,
        mask_image_url: str | None = None,
        mask_frame_id: int = 1,
        on_tick=None,
        duration: int = 5,
        resolution: str = "720P",
    ) -> dict:
        prompt_text = (
            plan.get("_edit_prompt")
            or plan.get("prompt_for_runway")
            or plan.get("prompt_for_veo")
            or plan.get("prompt")
            or plan.get("description", "")
        )
        provider = str(plan.get("_video_gen_provider") or _real_settings.normalized_video_gen_provider)
        motion_edit, _, prompt_text = _rewrite_motion_prompt(prompt_text)
        change_type = str(plan.get("_change_type") or ("motion" if motion_edit else ""))
        temporal_behavior = str(plan.get("_temporal_behavior") or "temporary")
        edit_mode = select_source_edit_mode(
            provider,
            duration=duration,
            source_video=source_video_url is not None,
            mask_available=mask_image_url is not None,
            change_type=change_type or None,
            temporal_behavior=temporal_behavior or None,
        )
        effective_source_video_url = (
            source_video_url
            if edit_mode in {"source_video", "tracked_mask"}
            else None
        )

        # ``frame_path``/``last_frame_path`` remain compatibility aliases for
        # older callers. New callers must use semantic subject/boundary names.
        subject_path = subject_reference_path
        first_path = start_anchor_path
        if frame_path and subject_path is None and first_path is None:
            if provider in {"veo", "meshapi_veo"} or not effective_source_video_url:
                first_path = frame_path
            else:
                subject_path = frame_path
        conditioning = GenerationConditioning(
            subject_reference_path=subject_path,
            start_anchor_path=first_path,
            end_anchor_path=end_anchor_path or last_frame_path,
        )
        routed = route_provider_conditioning(
            provider,
            conditioning,
            source_video=effective_source_video_url is not None,
            duration=duration,
        )
        if provider == "wan":
            if style_ref:
                out_path = await _wan.generate_propagation_variant(
                    prompt=prompt_text,
                    style_reference_path=style_ref,
                    reference_frame_path=conditioning.subject_reference_path,
                    duration=duration,
                    resolution=resolution,
                )
            elif edit_mode == "tracked_mask":
                # Wan 2.7 has no mask field. VACE is the native local-edit path:
                # it tracks the SAM target while preserving pixels outside it.
                out_path = await _wan.generate_local_edit_variant(
                    prompt=prompt_text,
                    source_video_url=effective_source_video_url,
                    mask_image_url=mask_image_url,
                    mask_frame_id=mask_frame_id,
                    on_tick=on_tick,
                )
            elif edit_mode == "source_video":
                # Wan's video-edit model receives the real source clip. This keeps
                # the surrounding motion and temporal context instead of falling
                # back to an unrelated text-only generation.
                out_path = await _wan.generate_edit_variant(
                    prompt=prompt_text,
                    source_video_url=effective_source_video_url,
                    reference_frame_path=routed.subject_reference_path,
                    motion_edit=motion_edit,
                    on_tick=on_tick,
                    resolution=resolution,
                )
            else:
                out_path = await _wan.generate_variant(
                    prompt=prompt_text,
                    reference_frame_path=routed.first_frame_path,
                    last_frame_path=(
                        routed.last_frame_path
                        if edit_mode == "first_last_frames"
                        else None
                    ),
                    on_tick=on_tick,
                    duration=duration,
                    resolution=resolution,
                )
        elif provider == "veo":
            if style_ref:
                out_path = await _veo.generate_propagation_variant(
                    prompt=prompt_text,
                    style_reference_path=style_ref,
                    reference_frame_path=conditioning.subject_reference_path,
                    duration=duration,
                    resolution=resolution,
                )
            else:
                out_path = await _veo.generate_variant(
                    prompt=prompt_text,
                    reference_frame_path=routed.first_frame_path,
                    last_frame_path=routed.last_frame_path,
                    on_tick=on_tick,
                    duration=duration,
                    resolution=resolution,
                )
        elif provider == "meshapi_veo":
            if style_ref:
                out_path = await _meshapi_veo.generate_propagation_variant(
                    prompt=prompt_text,
                    style_reference_path=style_ref,
                    reference_frame_path=conditioning.subject_reference_path,
                    duration=duration,
                    resolution=resolution,
                )
            else:
                out_path = await _meshapi_veo.generate_variant(
                    prompt=prompt_text,
                    reference_frame_path=routed.first_frame_path,
                    on_tick=on_tick,
                    duration=duration,
                    resolution=resolution,
                )
        else:
            if style_ref:
                out_path = await _happyhorse.generate_propagation_variant(
                    prompt=prompt_text,
                    style_reference_path=style_ref,
                    reference_frame_path=conditioning.subject_reference_path,
                    duration=duration,
                    resolution=resolution,
                )
            else:
                if effective_source_video_url:
                    target = (
                        "the exact subject isolated in the reference image"
                        if routed.subject_reference_path
                        else "the subject named in the request"
                    )
                    prompt_text = (
                        "Edit the supplied video directly. Apply the requested change "
                        f"ONLY to {target}; preserve the camera, framing, "
                        "background, lighting, shadows, clothing, and every other "
                        "person or object exactly as in the source. Respect the "
                        "requested timing relative to the start of this video. "
                        "Do not regenerate the whole scene.\n\n"
                        + prompt_text
                    )
                out_path = await _happyhorse.generate_variant(
                    prompt=prompt_text,
                    reference_frame_path=(
                        routed.subject_reference_path or routed.first_frame_path
                    ),
                    source_video_url=effective_source_video_url,
                    on_tick=on_tick,
                    duration=duration,
                    resolution=resolution,
                )
        published_url = await storage.publish(Path(out_path), content_type="video/mp4")
        return {
            "url": published_url,
            "path": str(out_path),
            "description": plan.get("description", ""),
        }

    runway = _types.SimpleNamespace(generate=_runway_generate)

    # ------------------------ elevenlabs adapter ------------------------

    async def _narrate(text: str) -> bytes:
        mp3_path = await _el_real.generate_narration(text)
        return Path(mp3_path).read_bytes()

    elevenlabs = _types.SimpleNamespace(narrate=_narrate)

    # Stephen's entity_tracker is higher-level orchestration; expose as-is for
    # anyone who wants to bypass the backend worker and use his flow directly.
    entity_tracker = _et_real
