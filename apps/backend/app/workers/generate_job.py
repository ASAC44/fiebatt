"""Generate-variant worker.

Single-edit mode: one generation per prompt.

Flow per submitted job:
  1. flip job to 'processing'
  2. extract source clip via ffmpeg
  3. grab the reference frame + crop the bbox (image-conditioning slot)
  4. ask Gemini for a structured edit plan (we use plan[0])
   5. run one generation (Wan or HappyHorse, per provider config), write the resulting Variant row
  6. score the result with Gemini (best-effort)
  7. flip job to 'done' (or 'error' if generation failed)

Every stage also publishes a structured "thought process" event through
``app.services.job_events``. The SSE route in ``app.api.routes.jobs``
relays those events to the browser so the user can watch the model's
reasoning (plan JSON, generation task id, poll ticks, quality scores) in real time.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai import services as ai
from ai.services import sam as sam_service
from ai.services.provider_capabilities import (
    normalize_video_provider,
    select_video_provider,
)
from app.db.session import AsyncSessionLocal
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.services.credentials import provider_overrides
from app.services import ffmpeg, job_events, storage

log = logging.getLogger("fiebatt.jobs.generate")

from ai.services.config import get_settings as _get_ai_settings
_SETTINGS = _get_ai_settings()
_PROVIDER = _SETTINGS.normalized_video_gen_provider
_PROVIDER_LABEL = _SETTINGS.video_gen_provider_label


def _normalize_provider(value: str | None) -> str:
    return normalize_video_provider(value, default=_PROVIDER)


def _provider_label(provider: str) -> str:
    return {
        "auto": "Auto",
        "wan": "Wan",
        "happyhorse": "HappyHorse",
        "veo": "Veo",
        "meshapi_veo": "Mesh API Veo",
    }.get(provider, _PROVIDER_LABEL)

VARIANT_COUNT = 1


def _provider_model(provider: str) -> str:
    return {
        "wan": "wan2.7-videoedit",
        "happyhorse": "happyhorse-1.0-video-edit",
        "veo": _SETTINGS.veo_model,
        "meshapi_veo": _SETTINGS.mesh_video_model,
    }.get(provider, provider)


async def _update_job(db: AsyncSession, job_id: str, **fields) -> None:
    job = await db.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    await db.commit()


async def _update_variant(db: AsyncSession, variant_id: str, **fields) -> None:
    v = await db.get(Variant, variant_id)
    if v is None:
        return
    for k, v2 in fields.items():
        setattr(v, k, v2)
    await db.commit()


async def _emit(job_id: str, stage: str, msg: str, **data: Any) -> None:
    """Publish a typed event on the job's SSE channel."""
    event: dict[str, Any] = {"stage": stage, "msg": msg, "ts": time.time()}
    if data:
        event["data"] = data
    await job_events.publish(job_id, event)


async def _emit_terminal(job_id: str, stage: str, msg: str, **data: Any) -> None:
    event: dict[str, Any] = {
        "stage": stage,
        "msg": msg,
        "ts": time.time(),
        "terminal": True,
    }
    if data:
        event["data"] = data
    await job_events.publish(job_id, event)


async def _run_variant(
    job_id: str,
    variant_id: str,
    clip_path: Path,
    clip_url: str,
    plan: dict,
    frame_path: str | None,
    last_frame_path: str | None = None,
    duration: float = 5,
    resolution: str = "720P",
    source_video_url: str | None = None,
    mask_image_url: str | None = None,
    mask_frame_id: int = 1,
) -> bool:
    provider = _normalize_provider(str(plan.get("_video_gen_provider") or ""))
    provider_label = _provider_label(provider)

    async with AsyncSessionLocal() as db:
        await _update_variant(db, variant_id, status="processing")

    async def tick(evt: dict[str, Any]) -> None:
        kind = evt.get("kind", "gen.tick")
        if kind == "gen.submit":
            await _emit(
                job_id,
                "gen_submit",
                f"{provider_label} accepted the generation (task={evt.get('task_id')})",
                **{k: v for k, v in evt.items() if k != "kind"},
            )
        elif kind == "gen.poll":
            await _emit(
                job_id,
                "gen_poll",
                f"{provider_label} still rendering... ({evt.get('elapsed')}s elapsed)",
                **{k: v for k, v in evt.items() if k != "kind"},
            )

    log.info(
        "[gen.variant] START job=%s variant=%s frame=%s plan_keys=%s",
        job_id,
        variant_id,
        "yes" if frame_path else "none (text-only)",
        sorted(plan.keys()) if isinstance(plan, dict) else "?",
    )

    try:
        import time as _time
        t0 = _time.monotonic()
        result = await ai.runway.generate(
            str(clip_path),
            plan,
            frame_path=frame_path,
            last_frame_path=last_frame_path,
            source_video_url=source_video_url,
            mask_image_url=mask_image_url,
            mask_frame_id=mask_frame_id,
            on_tick=tick,
            duration=round(duration),
            resolution=resolution,
        )
        log.info(
            "[gen.variant] %s OK job=%s variant=%s took=%.1fs url=%s",
            provider_label.lower(),
            job_id,
            variant_id,
            _time.monotonic() - t0,
            (result or {}).get("url"),
        )
    except Exception as e:
        error_message = str(e).strip() or type(e).__name__
        log.exception(
            "[gen.variant] %s FAILED job=%s variant=%s err=%s",
            provider_label.lower(), job_id, variant_id, error_message[:200],
        )
        await _emit(
            job_id,
            "gen_error",
            f"{provider_label} generation failed: {error_message}",
            error=error_message[:500],
        )
        async with AsyncSessionLocal() as db:
            await _update_variant(db, variant_id, status="error", error=error_message[:500])
        return False

    # stubs (and some real providers) may echo the input clip path back as
    # the "url". normalise anything filesystem-y into an external URL the
    # frontend can actually load.
    raw = result.get("url") or ""
    if raw in (str(clip_path), Path(clip_path).as_posix()):
        variant_url = clip_url
        await _emit(
            job_id,
            "gen_echo",
            "provider echoed the source clip back (stub mode or no-op generation)",
        )
    else:
        variant_url = storage.normalize_url_like(raw, fallback=clip_url)

        generated_path = Path(str(result.get("path") or ""))
        if generated_path.is_file():
            conformed_path, _ = storage.new_path("generated", "mp4")
            try:
                await ffmpeg.conform_generated_edit(
                    generated_path,
                    clip_path,
                    duration,
                    conformed_path,
                )
                variant_url = await storage.publish(conformed_path, content_type="video/mp4")
            except Exception as exc:
                log.exception("generated clip validation/conform failed")
                await _emit(
                    job_id,
                    "gen_validation_error",
                    f"generated clip failed technical validation: {exc}",
                )
                async with AsyncSessionLocal() as db:
                    await _update_variant(db, variant_id, status="error", error=str(exc)[:500])
                return False

        await _emit(
            job_id,
            "gen_done",
            f"{provider_label} returned a generated clip",
            url=variant_url,
            description=result.get("description") or plan.get("description"),
        )

    async with AsyncSessionLocal() as db:
        await _update_variant(
            db,
            variant_id,
            status="done",
            url=variant_url,
            description=result.get("description") or plan.get("description"),
        )
    return True


def _public_url_or_none(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return None
    return url


async def _score_variant_safe(frames: list[str], prompt: str) -> dict | None:
    # The facade's stable contract is (frames, prompt). The real adapter also
    # accepts provider-style keyword names, but the local stub intentionally
    # exposes only this public contract.
    try:
        return await ai.gemini.score_variant(frames, prompt)
    except Exception:
        log.exception("scoring failed")
        return None


async def _sample_variant_frames(variant_url: str, count: int = 7) -> list[str]:
    variant_path = await storage.path_from_url(variant_url)
    metadata = await ffmpeg.probe(variant_path)
    duration = float(metadata["duration"])
    if duration <= 0:
        raise ValueError("generated clip has no measurable duration")

    frames: list[str] = []
    for index in range(count):
        frame_path, _ = storage.new_path("keyframes", "jpg")
        timestamp = duration * (index + 0.5) / count
        await ffmpeg.extract_frame(variant_path, timestamp, frame_path)
        frames.append(str(frame_path))
    return frames


async def run(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            log.warning("generate job %s missing", job_id)
            return
        proj = await db.get(Project, job.project_id)
        if proj is None:
            await _update_job(db, job_id, status="error", error="project missing")
            await _emit_terminal(job_id, "error", "project missing")
            return

        owner_session = await db.get(SessionModel, proj.session_id)
        if owner_session is not None and owner_session.user_id:
            from ai.services.config import set_settings_overrides

            set_settings_overrides(
                await provider_overrides(db, owner_session.user_id)
            )

        await _update_job(db, job_id, status="processing")

        start_ts = float(job.start_ts or 0.0)
        end_ts = float(job.end_ts or 0.0)
        bbox = job.bbox_json or {}
        prompt = job.prompt or ""
        reference_frame_ts = float(job.reference_frame_ts or start_ts)
        payload = dict(job.payload or {})
        requested_provider = _normalize_provider(str(payload.get("video_gen_provider") or ""))
        video_provider = select_video_provider(
            requested_provider,
            source_video=True,
            duration=end_ts - start_ts,
        )
        video_provider_label = _provider_label(video_provider)
        _, sequenced_motion, _ = ai._rewrite_motion_prompt(prompt)  # type: ignore[attr-defined]
        requested_end_ts = end_ts
        clip_end_ts = requested_end_ts
        payload.update({
            "requested_provider": requested_provider,
            "selected_provider": video_provider,
            "selected_model": _provider_model(video_provider),
            "warnings": [],
        })
        job.payload = payload
        await db.commit()

    bbox_w = float(bbox.get("w", 0.0))
    bbox_h = float(bbox.get("h", 0.0))
    bbox_is_full_frame = bbox_w >= 0.98 and bbox_h >= 0.98

    await _emit(
        job_id,
        "queued",
        "kicking off generation pipeline",
        project_id=proj.id,
        start_ts=start_ts,
        end_ts=requested_end_ts,
        context_end_ts=clip_end_ts,
        requested_provider=requested_provider,
        selected_provider=video_provider,
        selected_model=_provider_model(video_provider),
        sequenced_motion=sequenced_motion,
        bbox=bbox,
        bbox_is_full_frame=bbox_is_full_frame,
        prompt=prompt,
        reference_frame_ts=reference_frame_ts,
    )

    if bbox_is_full_frame:
        await _emit(
            job_id,
            "bbox_missing",
            "no region was drawn — treating this as a full-frame regeneration. "
            "The selected provider may regenerate the entire scene. For targeted "
            "tweaks (e.g. color changes on one subject) draw a tight box first.",
        )

    # ---- parallel prep: extract clip + extract frame concurrently ----
    clip_tmp_path, _ = storage.new_path("clips", "mp4")
    frame_path, _ = storage.new_path("keyframes", "jpg")
    end_frame_path, _ = storage.new_path("keyframes", "jpg")

    await _emit(
        job_id,
        "extract_clip",
        f"slicing source context {start_ts:.2f}s→{clip_end_ts:.2f}s",
        requested_end_ts=requested_end_ts,
    )
    await _emit(job_id, "extract_frame", f"grabbing reference frame @ {reference_frame_ts:.2f}s")

    async def _do_extract_clip() -> tuple[Path, str]:
        await ffmpeg.extract_clip(proj.video_path, start_ts, clip_end_ts, clip_tmp_path)
        clip_url = await storage.publish(clip_tmp_path, content_type="video/mp4")
        return clip_tmp_path, clip_url

    async def _do_extract_frame() -> tuple[Path, bool]:
        try:
            await ffmpeg.extract_frame(proj.video_path, reference_frame_ts, frame_path)
            await storage.publish(frame_path, content_type="image/jpeg")
            return frame_path, True
        except Exception as e:
            log.exception("frame extract failed (continuing without frame)")
            await _emit(job_id, "extract_frame_error", f"couldn't grab reference frame: {e}")
            return frame_path, False

    async def _do_extract_end_frame() -> tuple[Path, bool]:
        if video_provider != "veo" or round(requested_end_ts - start_ts) != 8:
            return end_frame_path, False
        try:
            await ffmpeg.extract_frame(proj.video_path, requested_end_ts - 0.05, end_frame_path)
            await storage.publish(end_frame_path, content_type="image/jpeg")
            return end_frame_path, True
        except Exception as e:
            log.exception("end frame extract failed (continuing with first frame only)")
            await _emit(job_id, "extract_end_frame_error", f"couldn't grab end frame: {e}")
            return end_frame_path, False

    clip_task = asyncio.create_task(_do_extract_clip())
    frame_task = asyncio.create_task(_do_extract_frame())
    end_frame_task = asyncio.create_task(_do_extract_end_frame())

    # frame is needed for plan_variants — wait for it first
    frame_path, frame_ok = await frame_task
    end_frame_path, end_frame_ok = await end_frame_task
    conditioning_frame = str(frame_path) if frame_ok else None
    conditioning_end_frame = str(end_frame_path) if end_frame_ok else None

    # crop the bbox for Gemini reference while plan runs
    crop_path: Path | None = None
    if frame_ok and bbox and not bbox_is_full_frame:
        try:
            crop_path = await ffmpeg.crop_bbox_from_frame(frame_path, bbox)
            await storage.publish(crop_path, content_type="image/png")
            await _emit(
                job_id,
                "crop_bbox",
                "cropped bbox region for planning and localization fallback",
                bbox=bbox,
                crop_path=str(crop_path),
            )
        except Exception as e:
            log.exception("bbox crop failed (falling back to whole frame)")
            await _emit(
                job_id,
                "crop_bbox_error",
                f"bbox crop failed, falling back to full frame: {e}",
            )
            crop_path = None

    edit_duration = requested_end_ts - start_ts
    segment_duration = round(edit_duration)
    generation_resolution = "720P"

    # Derive generation conditioning from the actual SAM pixels, not merely
    # from the rectangle coordinates. Wan VACE consumes the published mask
    # directly for short tracked edits; other paths receive an isolated target
    # reference so the provider can unambiguously identify the selected entity.
    mask_path: str | None = None
    mask_image_url: str | None = None
    subject_reference_path: str | None = None
    if frame_ok and bbox and not bbox_is_full_frame:
        try:
            if await sam_service.is_available():
                mask_path = await sam_service.bbox_to_mask(str(frame_path), bbox)
                mask_image_url = await storage.publish(Path(mask_path), content_type="image/png")
                subject_reference_path = sam_service.create_subject_reference(
                    str(frame_path),
                    mask_path,
                )
                await storage.publish(Path(subject_reference_path), content_type="image/png")
                await _emit(
                    job_id,
                    "sam_mask",
                    "SAM isolated the selected subject for localized generation",
                    native_tracking_candidate=video_provider == "wan" and edit_duration <= 5.001,
                )
            else:
                await _emit(job_id, "sam_unavailable", "SAM unavailable; using bbox crop fallback")
        except Exception as exc:
            log.warning("SAM generation conditioning failed; using bbox crop", exc_info=True)
            await _emit(job_id, "sam_error", f"SAM localization failed; using bbox crop: {exc}")

    await _emit(
        job_id,
        "plan_start",
        "asking Gemini to structure the edit plan",
        user_prompt=prompt,
        bbox=bbox,
        duration=segment_duration,
    )

    # start Gemini plan while clip finishes in background
    plan_task = asyncio.create_task(ai.gemini.plan_variants(prompt, bbox, str(frame_path)))
    clip_path, clip_url = await clip_task  # should be done by now
    try:
        plans = await plan_task
    except Exception as e:
        log.exception("plan_variants failed")
        async with AsyncSessionLocal() as db:
            await _update_job(db, job_id, status="error", error=f"plan failed: {e}")
        await _emit_terminal(job_id, "error", f"plan failed: {e}")
        return

    if not plans:
        async with AsyncSessionLocal() as db:
            await _update_job(db, job_id, status="error", error="no plans returned")
        await _emit_terminal(job_id, "error", "Gemini returned no plans")
        return

    plan = list(plans)[0]
    plan["_video_gen_provider"] = video_provider

    intent = str(plan.get("intent") or "").lower()
    strategy = "first_frame"

    conditioning_frame_effective: str | None = (
        subject_reference_path
        or (str(crop_path) if crop_path is not None else None)
        or conditioning_frame
    )

    safe_plan = {
        "description": plan.get("description"),
        "intent": intent or None,
        "conditioning_strategy": strategy,
        "tone": plan.get("tone"),
        "color_grading": plan.get("color_grading"),
        "region_emphasis": plan.get("region_emphasis"),
        "prompt": prompt,
    }
    await _emit(
        job_id,
        "plan_done",
        "Gemini returned a structured edit plan",
        plan=safe_plan,
        variant_count=len(list(plans)),
    )

    # create the single Variant row so the polling endpoint sees it from t0
    async with AsyncSessionLocal() as db:
        v = Variant(job_id=job_id, index=0, status="pending")
        db.add(v)
        await db.flush()
        variant_id = v.id
        await db.commit()

    if subject_reference_path:
        conditioned_on = "SAM-isolated subject reference"
    elif crop_path is not None:
        conditioned_on = "bbox crop fallback"
    elif conditioning_frame_effective:
        conditioned_on = "full frame"
    else:
        conditioned_on = "text_only (scene regenerated from prose)"

    source_video_url = _public_url_or_none(clip_url)
    mask_public_url = _public_url_or_none(mask_image_url or "")
    project_fps = float(proj.fps or 1.0)
    mask_frame_id = min(
        round(edit_duration * project_fps) + 1,
        max(1, round(max(0.0, reference_frame_ts - start_ts) * project_fps) + 1),
    )
    warnings: list[str] = []
    if video_provider in {"wan", "happyhorse"} and source_video_url is None:
        warnings.append(
            f"{video_provider} could not access a public source clip; using frame-conditioned generation"
        )
    if mask_path and video_provider == "wan" and edit_duration > 5.001:
        warnings.append(
            "Wan native tracked-mask edits are limited to 5 seconds; using the isolated SAM subject reference"
        )
    elif mask_path and video_provider == "wan" and mask_public_url is None:
        warnings.append(
            "SAM mask is not provider-accessible; using the isolated SAM subject reference"
        )
    async with AsyncSessionLocal() as db:
        current_job = await db.get(Job, job_id)
        if current_job is not None:
            current_payload = dict(current_job.payload or {})
            current_payload["warnings"] = warnings
            current_job.payload = current_payload
            await db.commit()

    await _emit(
        job_id,
        "gen_start",
        f"dispatching prompt to {video_provider_label}",
        prompt=safe_plan["prompt"],
        strategy=strategy,
        conditioned_on=conditioned_on,
        provider=video_provider,
        model=_provider_model(video_provider),
        warnings=warnings,
    )

    await _run_variant(
        job_id=job_id,
        variant_id=variant_id,
        clip_path=clip_path,
        clip_url=clip_url,
        plan={**plan, "_edit_prompt": prompt},
        frame_path=conditioning_frame_effective,
        last_frame_path=conditioning_end_frame,
        duration=edit_duration,
        resolution=generation_resolution,
        source_video_url=source_video_url,
        mask_image_url=mask_public_url if edit_duration <= 5.001 else None,
        mask_frame_id=mask_frame_id,
    )

    # collect outcome
    async with AsyncSessionLocal() as db:
        variants = (
            await db.execute(select(Variant).where(Variant.job_id == job_id))
        ).scalars().all()
        done = [v for v in variants if v.status == "done"]

    log.info(
        "[gen.run] job=%s variants=%d done=%d err=%d",
        job_id,
        len(variants),
        len(done),
        sum(1 for v in variants if v.status == "error"),
    )

    if not done:
        err = variants[0].error if variants else "generation failed"
        log.error("[gen.run] job=%s FAILED all variants — first_error=%s", job_id, err)
        async with AsyncSessionLocal() as db:
            await _update_job(db, job_id, status="error", error=err or "generation failed")
        await _emit_terminal(job_id, "error", err or "generation failed")
        return

    async def score_variant(variant: Variant) -> dict | None:
        if not variant.url:
            return None
        try:
            sampled_frames = await _sample_variant_frames(variant.url)
        except Exception as exc:
            log.exception("generated frame sampling failed")
            await _emit(job_id, "score_skipped", f"couldn't sample generated video: {exc}")
            return None
        return await _score_variant_safe(sampled_frames, prompt)

    await _emit(job_id, "score_start", "sampling generated video for quality scoring")
    score = await score_variant(done[0])

    needs_retry = isinstance(score, dict) and (
        int(score.get("visual_coherence") or 0) < 5
        or int(score.get("prompt_adherence") or 0) < 6
    )
    if needs_retry:
        previous_url = done[0].url
        previous_description = done[0].description
        await _emit(
            job_id,
            "gen_retry",
            "quality validation failed; making one corrective generation attempt",
            attempt=2,
            previous_score=score,
        )
        correction = (
            "\n\nCORRECTIVE RETRY: The previous result failed quality validation. "
            "Make every requested action visually distinct, preserve the exact action order and count, "
            "avoid ghosting or blended limbs, and finish in the requested continuing motion."
        )
        await _run_variant(
            job_id=job_id,
            variant_id=variant_id,
            clip_path=clip_path,
            clip_url=clip_url,
            plan={**plan, "_edit_prompt": prompt + correction},
            frame_path=conditioning_frame_effective,
            last_frame_path=conditioning_end_frame,
            duration=edit_duration,
            resolution=generation_resolution,
            source_video_url=source_video_url,
            mask_image_url=mask_public_url if edit_duration <= 5.001 else None,
            mask_frame_id=mask_frame_id,
        )
        async with AsyncSessionLocal() as db:
            retried = await db.get(Variant, variant_id)
            if retried is not None and retried.status == "done":
                done = [retried]
                score = await score_variant(retried)
            elif retried is not None:
                await _update_variant(
                    db,
                    variant_id,
                    status="done",
                    url=previous_url,
                    description=previous_description,
                    error=None,
                )
                await _emit(
                    job_id,
                    "gen_retry_failed",
                    "corrective attempt failed; retaining the first generated result",
                )

    async with AsyncSessionLocal() as db:
        if isinstance(score, dict):
            await _update_variant(
                db,
                done[0].id,
                visual_coherence=score.get("visual_coherence"),
                prompt_adherence=score.get("prompt_adherence"),
            )
            await _emit(
                job_id,
                "score_done",
                "variant scored",
                visual_coherence=score.get("visual_coherence"),
                prompt_adherence=score.get("prompt_adherence"),
            )
        else:
            await _emit(job_id, "score_skipped", "scoring was unavailable; continuing")
        await _update_job(db, job_id, status="done")

    log.info(
        "[gen.run] job=%s COMPLETE variant_url=%s",
        job_id,
        done[0].url,
    )

    await _emit_terminal(
        job_id,
        "done",
        "generation complete",
        variant_url=done[0].url,
    )
