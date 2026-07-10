"""ai/services facade.

This module defines the stable surface that [backend/app/workers/](backend/app/workers/)
imports. The backend expects:

    gemini.plan_variants(prompt, bbox, frame_path) -> list[EditPlan]
    gemini.score_variant(frames, prompt) -> {visual_coherence, prompt_adherence}
    gemini.identify_entity(crop_path) -> {description, category, attributes}
    gemini.find_entity_in_keyframes(entity, keyframes) -> list[EntityHit]
    runway.generate(clip_path, plan, style_ref=None) -> {url, description}
    elevenlabs.narrate(text) -> bytes

Two modes:
  - USE_AI_STUBS=true  (default) -> route to _stubs.py, no API keys needed
  - USE_AI_STUBS=false          -> adapt the real modules (Qwen/DashScope for VLM,
                                   HappyHorse/DashScope for video generation,
                                   ElevenLabs for TTS) to the surface above.

Name mapping from real modules:
  qwen.create_edit_plan              -> gemini.plan_variants
  qwen.score_variant                -> gemini.score_variant   (arg order adapted)
  qwen.identify_entity              -> gemini.identify_entity (attr rename)
  qwen.search_keyframes_for_entity -> gemini.find_entity_in_keyframes
  happyhorse.generate_variant       -> runway.generate
  elevenlabs.generate_narration     -> elevenlabs.narrate    (path -> bytes)
"""
from __future__ import annotations

import json
import types as _types
from pathlib import Path

from ai.services import _stubs
from ai.services.config import get_settings as _get_ai_settings


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


if _USE_AI_STUBS:
    gemini = _stubs.gemini
    runway = _stubs.runway
    elevenlabs = _stubs.elevenlabs
    entity_tracker = _stubs.entity_tracker

else:
    _real_settings = _get_ai_settings()
    if not _real_settings.real_ai_ready:
        raise RuntimeError(
            "USE_AI_STUBS=false requires DASHSCOPE_API_KEY for the live gemini/happyhorse "
            "provider path. leave USE_AI_STUBS=true for local stub mode."
        )

    from app.services import storage
    from ai.services import qwen as _gemini_real
    from ai.services import happyhorse as _happyhorse
    from ai.services import elevenlabs as _el_real
    from ai.services import entity_tracker as _et_real

    # ------------------------ gemini adapter ------------------------

    # simple in-memory plan cache: keyed on (prompt, bbox_string), max 32 entries
    _plan_cache: dict[str, list[dict]] = {}
    _plan_cache_order: list[str] = []
    _PLAN_CACHE_MAX = 32

    async def _plan_variants(prompt: str, bbox: dict, frame_path: str) -> list[dict]:
        # only cache text-only plans (frame_path makes every call unique anyway)
        cache_key = f"{prompt}|{json.dumps(bbox, sort_keys=True)}"
        cached = _plan_cache.get(cache_key)
        if cached is not None:
            return cached

        plan = await _gemini_real.create_edit_plan(
            prompt, bbox, frame_path=frame_path
        )
        variants = plan.get("variants") if isinstance(plan, dict) else None
        if not variants:
            return []
        # The prompt schema uses prompt_for_veo; workers use prompt_for_runway.
        # Normalize so downstream code always finds prompt_for_runway, and
        # give every variant a conditioning_strategy default so the worker
        # never has to second-guess a missing field.
        for v in variants:
            v.setdefault("prompt_for_runway", v.get("prompt_for_veo", ""))
            strategy = str(v.get("conditioning_strategy", "")).lower()
            if strategy not in ("first_frame", "text_only"):
                intent = str(v.get("intent", "")).lower()
                # always use first_frame — text_only produces garbage output
                # because veo has no visual context for the scene
                v["conditioning_strategy"] = "first_frame"

        # cache the result for repeated prompts (propagation reuses same prompt)
        _plan_cache[cache_key] = variants
        _plan_cache_order.append(cache_key)
        if len(_plan_cache_order) > _PLAN_CACHE_MAX:
            oldest = _plan_cache_order.pop(0)
            _plan_cache.pop(oldest, None)

        return variants

    async def _score_variant(
        frames: list[str] | None = None,
        prompt: str = "",
        *,
        original_prompt: str | None = None,
        variant_frame_paths: list[str] | None = None,
    ) -> dict:
        # accept both positional (frames, prompt) and keyword (original_prompt, variant_frame_paths) calling conventions
        p = original_prompt or prompt
        f = variant_frame_paths or frames or []
        return await _gemini_real.score_variant(original_prompt=p, variant_frame_paths=f)

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
        plan_variants=_plan_variants,
        score_variant=_score_variant,
        identify_entity=_identify_entity,
        find_entity_in_keyframes=_find_entity_in_keyframes,
    )

    # ------------------------ runway (happyhorse) adapter ------------------------

    async def _runway_generate(
        clip_path: str,
        plan: dict,
        style_ref: str | None = None,
        frame_path: str | None = None,
        on_tick=None,
        duration: int = 5,
        resolution: str = "720P",
    ) -> dict:
        """Drive one HappyHorse generation.

        ``clip_path`` is the source MP4 slice — not passed to HappyHorse
        (its image conditioning slot expects a still frame).

        ``frame_path`` is a still image (png/jpg) that HappyHorse uses as the
        opening frame. Pass ``None`` for text-only generation.

        ``duration`` is passed to HappyHorse (3-15s range, clamped internally).

        ``resolution`` — "720P" or "480P". 480P is ~2x faster for previews.
        """
        prompt_text = plan.get("prompt_for_runway") or plan.get("prompt_for_veo") or plan.get("prompt") or plan.get("description", "")
        conditioning = frame_path or style_ref
        if style_ref:
            out_path = await _happyhorse.generate_propagation_variant(
                prompt=prompt_text,
                style_reference_path=style_ref,
                reference_frame_path=frame_path,
                duration=duration,
                resolution=resolution,
            )
        else:
            out_path = await _happyhorse.generate_variant(
                prompt=prompt_text,
                reference_frame_path=conditioning,
                on_tick=on_tick,
                duration=duration,
                resolution=resolution,
            )
        published_url = await storage.publish(Path(out_path), content_type="video/mp4")
        return {
            "url": published_url,
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
