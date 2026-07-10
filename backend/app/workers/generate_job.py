"""Generate-variant worker.

Single-edit mode: one generation per prompt.

Flow per submitted job:
  1. flip job to 'processing'
  2. extract source clip via ffmpeg
  3. grab the reference frame + crop the bbox (image-conditioning slot)
  4. ask Gemini for a structured edit plan (we use plan[0])
  5. run one HappyHorse generation, write the resulting Variant row
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai import services as ai
from app.db.session import AsyncSessionLocal
from app.models.job import Job, Variant
from app.models.project import Project
from app.services import ffmpeg, job_events, storage

log = logging.getLogger("iris.jobs.generate")

# single-edit mode. keep the fan-out scaffolding in case we re-enable variants
# later, but only ever request one plan + one generation.
VARIANT_COUNT = 1


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
    duration: int = 5,
    resolution: str = "720P",
) -> None:
    async with AsyncSessionLocal() as db:
        await _update_variant(db, variant_id, status="processing")

    async def tick(evt: dict[str, Any]) -> None:
        kind = evt.get("kind", "gen.tick")
        if kind == "gen.submit":
            await _emit(
                job_id,
                "gen_submit",
                f"HappyHorse accepted the generation (task={evt.get('task_id')})",
                **{k: v for k, v in evt.items() if k != "kind"},
            )
        elif kind == "gen.poll":
            await _emit(
                job_id,
                "gen_poll",
                f"HappyHorse still rendering... ({evt.get('elapsed')}s elapsed)",
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
            on_tick=tick,
            duration=duration,
            resolution=resolution,
        )
        log.info(
            "[gen.variant] happyhorse OK job=%s variant=%s took=%.1fs url=%s",
            job_id,
            variant_id,
            _time.monotonic() - t0,
            (result or {}).get("url"),
        )
    except Exception as e:
        log.exception(
            "[gen.variant] happyhorse FAILED job=%s variant=%s err=%s",
            job_id, variant_id, str(e)[:200],
        )
        await _emit(job_id, "gen_error", f"HappyHorse generation failed: {e}", error=str(e)[:500])
        async with AsyncSessionLocal() as db:
            await _update_variant(db, variant_id, status="error", error=str(e)[:500])
        return

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

        await _emit(
            job_id,
            "gen_done",
            "HappyHorse returned a generated clip",
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


async def _score_variant_safe(frames: list[str], prompt: str) -> dict | None:
    # NB: gemini.score_variant takes (original_prompt, variant_frame_paths) —
    # the argument order here is easy to swap by accident, which previously
    # caused the iterator in score_variant to walk over the characters of
    # the prompt string as if they were filesystem paths and blow up.
    try:
        return await ai.gemini.score_variant(
            original_prompt=prompt,
            variant_frame_paths=frames,
        )
    except Exception:
        log.exception("scoring failed")
        return None


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

        await _update_job(db, job_id, status="processing")

        start_ts = float(job.start_ts or 0.0)
        end_ts = float(job.end_ts or 0.0)
        bbox = job.bbox_json or {}
        prompt = job.prompt or ""
        reference_frame_ts = float(job.reference_frame_ts or start_ts)

    bbox_w = float(bbox.get("w", 0.0))
    bbox_h = float(bbox.get("h", 0.0))
    bbox_is_full_frame = bbox_w >= 0.98 and bbox_h >= 0.98

    await _emit(
        job_id,
        "queued",
        "kicking off generation pipeline",
        project_id=proj.id,
        start_ts=start_ts,
        end_ts=end_ts,
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
            "HappyHorse regenerates the entire scene from the prompt, which gives it "
            "more freedom to honor 'remove' / 'replace' intents. For targeted "
            "tweaks (e.g. color changes on one subject) draw a tight box first.",
        )

    # ---- parallel prep: extract clip + extract frame concurrently ----
    clip_tmp_path, _ = storage.new_path("clips", "mp4")
    frame_path, _ = storage.new_path("keyframes", "jpg")

    await _emit(job_id, "extract_clip", f"slicing source {start_ts:.2f}s→{end_ts:.2f}s")
    await _emit(job_id, "extract_frame", f"grabbing reference frame @ {reference_frame_ts:.2f}s")

    async def _do_extract_clip() -> tuple[Path, str]:
        await ffmpeg.extract_clip(proj.video_path, start_ts, end_ts, clip_tmp_path)
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

    clip_task = asyncio.create_task(_do_extract_clip())
    frame_task = asyncio.create_task(_do_extract_frame())

    # frame is needed for plan_variants — wait for it first
    frame_path, frame_ok = await frame_task
    conditioning_frame = str(frame_path) if frame_ok else None

    # crop the bbox for Gemini reference while plan runs
    crop_path: Path | None = None
    if frame_ok and bbox and not bbox_is_full_frame:
        try:
            crop_path = await ffmpeg.crop_bbox_from_frame(frame_path, bbox)
            await storage.publish(crop_path, content_type="image/png")
            await _emit(
                job_id,
                "crop_bbox",
                "cropped bbox region for Gemini reference (not sent to Veo)",
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

    # Compute duration for HappyHorse — clamp to HappyHorse's supported range (3-15s)
    segment_duration = min(max(round(end_ts - start_ts), 3), 15)
    generation_resolution = "720P"

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

    # HappyHorse i2v is first-frame-conditioned — it keeps whatever subject
    # is in the opening frame.
    intent = str(plan.get("intent") or "").lower()
    # ALWAYS send the reference frame to HappyHorse. Without it, the model
    # regenerates the entire scene from text and produces output that doesn't
    # match the original footage at all.
    strategy = "first_frame"

    conditioning_frame_effective: str | None = conditioning_frame

    safe_plan = {
        "description": plan.get("description"),
        "intent": intent or None,
        "conditioning_strategy": strategy,
        "tone": plan.get("tone"),
        "color_grading": plan.get("color_grading"),
        "region_emphasis": plan.get("region_emphasis"),
        "prompt": plan.get("prompt_for_veo") or plan.get("prompt_for_runway"),
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

    if conditioning_frame_effective:
        conditioned_on = "full_frame (bbox region described in prompt)"
    else:
        conditioned_on = "text_only (scene regenerated from prose)"

    await _emit(
        job_id,
        "gen_start",
        "dispatching prompt to HappyHorse",
        prompt=safe_plan["prompt"],
        strategy=strategy,
        conditioned_on=conditioned_on,
    )

    await _run_variant(
        job_id, variant_id, clip_path, clip_url, plan, conditioning_frame_effective, segment_duration, generation_resolution
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

    # best-effort scoring
    await _emit(job_id, "score_start", "asking Gemini to score the variant")
    score = await _score_variant_safe([str(frame_path)], prompt)
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
