"""Generate-variant worker.

Single-edit mode: one generation per prompt.

Flow per submitted job:
  1. flip job to 'processing'
  2. extract source clip via ffmpeg
  3. extract a subject reference separately from full-frame boundary anchors
  4. use the stored structured plan (legacy jobs plan once here)
  5. run the selected provider and write the first Variant row
  6. review it and optionally run one evidence-driven correction
  7. flip job to 'done' (or 'error' if generation failed)

Every stage also publishes a structured "thought process" event through
``app.services.job_events``. The SSE route in ``app.api.routes.jobs``
relays those events to the browser so the user can watch the model's
reasoning (plan JSON, generation task id, poll ticks, quality scores) in real time.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import services as ai
from app.ai.services import sam as sam_service
from app.ai.services.conditioning import (
    GenerationConditioning,
    boundary_anchor_timestamps,
)
from app.ai.services.provider_capabilities import (
    normalize_video_provider,
    select_source_edit_mode,
    select_video_provider,
)
from app.db.session import AsyncSessionLocal
from app.models.job import Job, Variant
from app.models.project import Project
from app.schemas.edit_plan import EditIntent
from app.services import ffmpeg, job_events, storage
from app.services.continuity_validator import (
    ContinuityReport,
    validate_generated_continuity,
)
from app.services.generation_window import (
    protected_context_prompt,
    resolve_generation_window,
)
from app.services.generation_quality import (
    GenerationQualityAction,
    attempt_quality_rank,
    corrective_prompt,
    decide_generation_quality,
    final_candidate_quality,
    final_semantic_quality,
    semantic_quality_evidence,
)
from app.services.generation_telemetry import build_local_flow_telemetry
from app.services.generation_failure import classify_generation_failure
from app.services.job_progress import persist_job_progress
from app.services.local_compositor import composite_generated_target
from app.services.local_seam import continuity_at_selected_seams, match_local_context
from app.services.edit_prompt import planned_edit_prompt

log = logging.getLogger("fiebatt.jobs.generate")

from app.ai.services.config import get_settings as _get_ai_settings
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
RETRY_GRACE_SECONDS = 10.0


def _provider_model(provider: str, edit_mode: str | None = None) -> str:
    if provider == "wan" and edit_mode == "tracked_mask":
        return "wan2.1-vace-plus"
    if provider == "wan" and edit_mode in {"first_frame", "first_last_frames"}:
        return "wan2.7-i2v-2026-04-25"
    return {
        "wan": "wan2.7-videoedit",
        "happyhorse": "happyhorse-1.0-video-edit",
        "veo": _SETTINGS.veo_model,
        "meshapi_veo": _SETTINGS.mesh_video_model,
    }.get(provider, provider)


_planned_edit_prompt = planned_edit_prompt


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
    if stage != "gen_poll":
        await persist_job_progress(
            job_id,
            stage=stage,
            message=msg,
            data={key: value for key, value in data.items() if key != "url"},
            session_factory=AsyncSessionLocal,
        )


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
    await persist_job_progress(
        job_id,
        stage=stage,
        message=msg,
        status="done" if stage == "done" else "failed",
        data={key: value for key, value in data.items() if key != "variant_url"},
        session_factory=AsyncSessionLocal,
    )


async def _record_attempt_failure(
    job_id: str,
    variant_id: str,
    error: Exception | str,
    *,
    provider_label: str,
) -> None:
    failure = classify_generation_failure(error)
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is not None:
            payload = dict(job.payload or {})
            attempts = list(payload.get("attempt_failures") or [])
            attempts.append({**failure.metadata(), "provider": provider_label})
            payload["attempt_failures"] = attempts
            job.payload = payload
        variant = await db.get(Variant, variant_id)
        if variant is not None:
            variant.status = "error"
            variant.error = failure.user_message
        await db.commit()
    await _emit(
        job_id,
        "attempt_failed",
        "This render attempt could not be used. Checking the safe recovery path…",
        code=failure.code,
        retryable=failure.retryable,
    )


async def _fail_job(job_id: str, error: Exception | str) -> None:
    failure = classify_generation_failure(error)
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is not None:
            payload = dict(job.payload or {})
            payload["failure_state"] = failure.metadata()
            job.payload = payload
            job.status = "error"
            job.error = failure.user_message
            await db.commit()
    await _emit_terminal(
        job_id,
        "failed",
        failure.user_message,
        code=failure.code,
        retryable=failure.retryable,
    )


async def _await_retry_permission(
    job_id: str,
    evidence: tuple[str, ...],
    *,
    grace_seconds: float = RETRY_GRACE_SECONDS,
    poll_seconds: float = 0.5,
) -> bool:
    """Persist the grace window and return only when a retry may be dispatched."""
    retry_at = time.time() + grace_seconds
    correction = corrective_prompt(evidence)
    retry_state = {
        "status": "waiting",
        "retry_at": retry_at,
        "evidence": list(evidence),
        "correction": correction,
    }
    async with AsyncSessionLocal() as db:
        current_job = await db.get(Job, job_id)
        if current_job is None:
            return False
        current_payload = dict(current_job.payload or {})
        current_payload["retry_state"] = retry_state
        current_job.payload = current_payload
        await db.commit()
    await _emit(
        job_id,
        "retry_pending",
        "first pass review found a specific issue; corrective retry is waiting",
        **retry_state,
    )
    while time.time() < retry_at:
        await asyncio.sleep(poll_seconds)
        async with AsyncSessionLocal() as db:
            current_job = await db.get(Job, job_id)
            if current_job is None:
                return False
            current_state = dict((current_job.payload or {}).get("retry_state") or {})
        if current_state.get("status") == "cancelled":
            await _emit(
                job_id,
                "retry_cancelled",
                "corrective retry stopped; keeping the first pass",
            )
            return False
        if current_state.get("status") == "retry_now":
            break
    async with AsyncSessionLocal() as db:
        current_job = await db.get(Job, job_id)
        if current_job is not None:
            current_payload = dict(current_job.payload or {})
            current_state = dict(current_payload.get("retry_state") or retry_state)
            if current_state.get("status") == "cancelled":
                return False
            current_state["status"] = "dispatched"
            current_state["dispatched_at"] = time.time()
            current_payload["retry_state"] = current_state
            current_job.payload = current_payload
            await db.commit()
    await _emit(
        job_id,
        "retry_dispatched",
        "starting one evidence-driven corrective retry",
        evidence=list(evidence),
    )
    return True


async def _run_variant(
    job_id: str,
    variant_id: str,
    clip_path: Path,
    clip_url: str,
    plan: dict,
    conditioning: GenerationConditioning,
    duration: float = 5,
    resolution: str = "720P",
    source_video_url: str | None = None,
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
        "[gen.variant] START job=%s variant=%s subject=%s anchors=%s/%s plan_keys=%s",
        job_id,
        variant_id,
        "yes" if conditioning.subject_reference_path else "no",
        "yes" if conditioning.start_anchor_path else "no",
        "yes" if conditioning.end_anchor_path else "no",
        sorted(plan.keys()) if isinstance(plan, dict) else "?",
    )

    try:
        import time as _time
        t0 = _time.monotonic()
        result = await ai.runway.generate(
            str(clip_path),
            plan,
            subject_reference_path=conditioning.subject_reference_path,
            start_anchor_path=conditioning.start_anchor_path,
            end_anchor_path=conditioning.end_anchor_path,
            source_video_url=source_video_url,
            mask_image_url=conditioning.mask_image_url,
            mask_frame_id=conditioning.mask_frame_id,
            on_tick=tick,
            # Providers render whole seconds. Round upward so fractional
            # adaptive context is never shorter than the source window;
            # conform_generated_edit trims the result back exactly.
            duration=math.ceil(duration - 1e-6),
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
        await _record_attempt_failure(
            job_id,
            variant_id,
            error_message,
            provider_label=provider_label,
        )
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
                await _record_attempt_failure(
                    job_id,
                    variant_id,
                    exc,
                    provider_label=provider_label,
                )
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


async def _score_variant_safe(
    frames: list[str],
    prompt: str,
    *,
    source_frames: list[str] | None = None,
    target_frames: list[str] | None = None,
    reference_target_path: str | None = None,
    change_type: str | None = None,
) -> dict | None:
    try:
        return await ai.gemini.score_variant(
            frames,
            prompt,
            source_frame_paths=source_frames,
            target_frame_paths=target_frames,
            reference_target_path=reference_target_path,
            change_type=change_type,
        )
    except Exception:
        log.exception("scoring failed")
        return None


async def _sample_video_frames(video_path: Path, count: int = 7) -> list[str]:
    metadata = await ffmpeg.probe(video_path)
    duration = float(metadata["duration"])
    if duration <= 0:
        raise ValueError("generated clip has no measurable duration")

    frames: list[str] = []
    for index in range(count):
        frame_path, _ = storage.new_path("keyframes", "jpg")
        timestamp = duration * (index + 0.5) / count
        await ffmpeg.extract_frame(video_path, timestamp, frame_path)
        frames.append(str(frame_path))
    return frames


async def _sample_variant_frames(variant_url: str, count: int = 7) -> list[str]:
    variant_path = await storage.path_from_url(variant_url)
    return await _sample_video_frames(variant_path, count=count)


async def _run(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            log.warning("generate job %s missing", job_id)
            return
        proj = await db.get(Project, job.project_id)
        if proj is None:
            await _fail_job(job_id, "project missing")
            return

        await _update_job(db, job_id, status="processing")

        start_ts = float(job.start_ts or 0.0)
        end_ts = float(job.end_ts or 0.0)
        bbox = job.bbox_json or {}
        prompt = job.prompt or ""
        reference_frame_ts = float(job.reference_frame_ts or start_ts)
        payload = dict(job.payload or {})
        project_video_path = proj.video_path
        project_video_url = str(payload.get("source_revision") or proj.video_url)
        generation_window = resolve_generation_window(
            start_ts,
            end_ts,
            payload=payload,
            project_duration=float(proj.duration),
        )
        requested_provider = _normalize_provider(str(payload.get("video_gen_provider") or ""))
        video_provider = select_video_provider(
            requested_provider,
            source_video=True,
            duration=generation_window.context_duration,
        )
        video_provider_label = _provider_label(video_provider)
        _, sequenced_motion, _ = ai._rewrite_motion_prompt(prompt)  # type: ignore[attr-defined]
        requested_end_ts = generation_window.core_end
        clip_start_ts = generation_window.context_start
        clip_end_ts = generation_window.context_end
        project_fps = float(proj.fps or 1.0)
        start_anchor_ts, end_anchor_ts = boundary_anchor_timestamps(
            clip_start_ts,
            clip_end_ts,
            project_fps,
        )
        payload.update({
            "requested_provider": requested_provider,
            "selected_provider": video_provider,
            "selected_model": _provider_model(video_provider),
            "execution_window": generation_window.metadata(),
            "warnings": [],
        })
        job.payload = payload
        await db.commit()

    try:
        if project_video_url == proj.video_url:
            source_video_path = await storage.materialize_source(
                project_video_path,
                project_video_url,
            )
        else:
            source_video_path = await storage.path_from_url(project_video_url)
    except Exception as exc:
        error = f"source video unavailable: {exc}"
        await _fail_job(job_id, error)
        return

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
        context_start_ts=clip_start_ts,
        context_end_ts=clip_end_ts,
        requested_provider=requested_provider,
        selected_provider=video_provider,
        selected_model=_provider_model(video_provider),
        sequenced_motion=sequenced_motion,
        bbox=bbox,
        bbox_is_full_frame=bbox_is_full_frame,
        prompt=prompt,
        reference_frame_ts=reference_frame_ts,
        start_anchor_ts=start_anchor_ts,
        end_anchor_ts=end_anchor_ts,
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
    subject_frame_path, _ = storage.new_path("keyframes", "jpg")
    start_anchor_path, _ = storage.new_path("keyframes", "jpg")
    end_frame_path, _ = storage.new_path("keyframes", "jpg")

    await _emit(
        job_id,
        "extract_clip",
        f"slicing source context {clip_start_ts:.2f}s→{clip_end_ts:.2f}s",
        requested_start_ts=start_ts,
        requested_end_ts=requested_end_ts,
    )
    await _emit(job_id, "extract_frame", f"grabbing reference frame @ {reference_frame_ts:.2f}s")

    async def _do_extract_clip() -> tuple[Path, str]:
        await ffmpeg.extract_clip(
            source_video_path,
            clip_start_ts,
            clip_end_ts,
            clip_tmp_path,
        )
        clip_url = await storage.publish(clip_tmp_path, content_type="video/mp4")
        return clip_tmp_path, clip_url

    async def _do_extract_subject_frame() -> tuple[Path, bool]:
        try:
            await ffmpeg.extract_frame(
                source_video_path,
                reference_frame_ts,
                subject_frame_path,
            )
            await storage.publish(subject_frame_path, content_type="image/jpeg")
            return subject_frame_path, True
        except Exception as e:
            log.exception("subject frame extract failed (continuing without subject reference)")
            await _emit(
                job_id,
                "extract_frame_error",
                f"couldn't grab subject reference frame: {e}",
            )
            return subject_frame_path, False

    async def _do_extract_start_anchor() -> tuple[Path, bool]:
        try:
            await ffmpeg.extract_frame(
                source_video_path,
                start_anchor_ts,
                start_anchor_path,
            )
            await storage.publish(start_anchor_path, content_type="image/jpeg")
            return start_anchor_path, True
        except Exception as e:
            log.exception("start anchor extract failed")
            await _emit(
                job_id,
                "extract_start_anchor_error",
                f"couldn't grab full-frame start anchor: {e}",
            )
            return start_anchor_path, False

    async def _do_extract_end_frame() -> tuple[Path, bool]:
        try:
            await ffmpeg.extract_frame(source_video_path, end_anchor_ts, end_frame_path)
            await storage.publish(end_frame_path, content_type="image/jpeg")
            return end_frame_path, True
        except Exception as e:
            log.exception("end anchor extract failed")
            await _emit(
                job_id,
                "extract_end_frame_error",
                f"couldn't grab full-frame end anchor: {e}",
            )
            return end_frame_path, False

    clip_task = asyncio.create_task(_do_extract_clip())
    subject_frame_task = asyncio.create_task(_do_extract_subject_frame())
    start_anchor_task = asyncio.create_task(_do_extract_start_anchor())
    end_frame_task = asyncio.create_task(_do_extract_end_frame())

    # Subject frame is needed for plan_variants; anchors remain full-frame.
    subject_frame_path, subject_frame_ok = await subject_frame_task
    start_anchor_path, start_anchor_ok = await start_anchor_task
    end_frame_path, end_frame_ok = await end_frame_task
    conditioning_start_anchor = str(start_anchor_path) if start_anchor_ok else None
    conditioning_end_anchor = str(end_frame_path) if end_frame_ok else None

    # crop the bbox for Gemini reference while plan runs
    crop_path: Path | None = None
    if subject_frame_ok and bbox and not bbox_is_full_frame:
        try:
            crop_path = await ffmpeg.crop_bbox_from_frame(subject_frame_path, bbox)
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

    generation_duration = generation_window.context_duration
    segment_duration = round(generation_duration)
    generation_resolution = "720P"

    # Derive generation conditioning from the actual SAM pixels, not merely
    # from the rectangle coordinates. Wan VACE consumes the published mask
    # directly for short tracked edits; other paths receive an isolated target
    # reference so the provider can unambiguously identify the selected entity.
    mask_path: str | None = None
    mask_image_url: str | None = None
    subject_reference_path: str | None = None
    sam_available = False
    sam_video_available = False
    if subject_frame_ok and bbox and not bbox_is_full_frame:
        try:
            sam_available = await sam_service.is_available()
            if sam_available:
                mask_path = await sam_service.bbox_to_mask(
                    str(subject_frame_path),
                    bbox,
                )
                mask_image_url = await storage.publish(Path(mask_path), content_type="image/png")
                subject_reference_path = sam_service.create_subject_reference(
                    str(subject_frame_path),
                    mask_path,
                )
                await storage.publish(Path(subject_reference_path), content_type="image/png")
                await _emit(
                    job_id,
                    "sam_mask",
                    "SAM isolated the selected subject for localized generation",
                    native_tracking_candidate=(
                        select_source_edit_mode(
                            video_provider,
                            duration=generation_duration,
                            source_video=True,
                            mask_available=True,
                        )
                        == "tracked_mask"
                    ),
                )
            else:
                await _emit(job_id, "sam_unavailable", "SAM unavailable; using bbox crop fallback")
        except Exception as exc:
            log.warning("SAM generation conditioning failed; using bbox crop", exc_info=True)
            await _emit(job_id, "sam_error", f"SAM localization failed; using bbox crop: {exc}")
        sam_video_available = await sam_service.video_tracking_available()

    planned_intent: EditIntent | None = None
    raw_planned_intent = payload.get("planned_intent")
    if isinstance(raw_planned_intent, dict):
        try:
            planned_intent = EditIntent.model_validate(raw_planned_intent)
        except Exception:
            log.warning("job contains invalid planned intent", exc_info=True)

    await _emit(
        job_id,
        "plan_start",
        (
            "reusing the edit instruction chosen before window analysis"
            if planned_intent is not None and planned_intent.grounded_edit is not None
            else "asking Qwen to structure the edit plan"
        ),
        user_prompt=str(payload.get("user_prompt") or prompt),
        bbox=bbox,
        duration=segment_duration,
    )

    # New planned jobs already contain Qwen's grounded instruction. Legacy
    # jobs still plan here so old clients remain usable.
    planning_frame_path = (
        str(subject_frame_path)
        if subject_frame_ok
        else (conditioning_start_anchor or "")
    )
    plan_task = (
        None
        if planned_intent is not None and planned_intent.grounded_edit is not None
        else asyncio.create_task(
            ai.gemini.plan_variants(prompt, bbox, planning_frame_path)
        )
    )
    clip_path, clip_url = await clip_task  # should be done by now
    if plan_task is None:
        assert planned_intent is not None and planned_intent.grounded_edit is not None
        plans = [planned_intent.grounded_edit.model_dump(mode="json")]
    else:
        try:
            plans = await plan_task
        except Exception as e:
            log.exception("plan_variants failed")
            await _fail_job(job_id, f"plan failed: {e}")
            return

    if not plans:
        await _fail_job(job_id, "no edit plans returned")
        return

    plan = list(plans)[0]
    plan["_video_gen_provider"] = video_provider
    if planned_intent is not None:
        plan["_change_type"] = planned_intent.change_type
        plan["_temporal_behavior"] = planned_intent.temporal_behavior
    generation_prompt = _planned_edit_prompt(prompt, plan)

    intent = str(plan.get("intent") or "").lower()
    subject_reference_effective: str | None = (
        subject_reference_path
        or (str(crop_path) if crop_path is not None else None)
    )
    planned_change_type = planned_intent.change_type if planned_intent else None
    planned_temporal_behavior = (
        planned_intent.temporal_behavior if planned_intent else None
    )
    edit_mode = select_source_edit_mode(
        video_provider,
        duration=generation_duration,
        source_video=_public_url_or_none(clip_url) is not None,
        mask_available=bool(mask_image_url),
        change_type=planned_change_type,
        temporal_behavior=planned_temporal_behavior,
    )
    if video_provider == "wan" and edit_mode == "first_last_frames":
        strategy = "first_last_boundary_frames"
    elif video_provider == "wan" and edit_mode == "first_frame":
        strategy = "start_boundary_frame"
    elif video_provider in {"wan", "happyhorse"}:
        strategy = "source_video_with_subject_reference"
    elif video_provider == "veo" and abs(generation_duration - 8.0) <= 0.05:
        strategy = "first_last_boundary_frames"
    else:
        strategy = "start_boundary_frame"

    safe_plan = {
        "description": plan.get("description"),
        "intent": intent or None,
        "conditioning_strategy": strategy,
        "tone": plan.get("tone"),
        "color_grading": plan.get("color_grading"),
        "region_emphasis": plan.get("region_emphasis"),
        "prompt": generation_prompt,
    }
    await _emit(
        job_id,
        "plan_done",
        "structured edit instructions are ready",
        plan=safe_plan,
        variant_count=len(list(plans)),
    )

    async def create_attempt_variant(index: int) -> str:
        async with AsyncSessionLocal() as db:
            variant = Variant(job_id=job_id, index=index, status="pending")
            db.add(variant)
            await db.flush()
            variant_id = variant.id
            await db.commit()
            return variant_id

    # The first row exists before dispatch so polling can show its live state.
    variant_id = await create_attempt_variant(0)

    source_video_url = _public_url_or_none(clip_url)
    mask_public_url = _public_url_or_none(mask_image_url or "")
    edit_mode = select_source_edit_mode(
        video_provider,
        duration=generation_duration,
        source_video=source_video_url is not None,
        mask_available=mask_public_url is not None,
        change_type=planned_change_type,
        temporal_behavior=planned_temporal_behavior,
    )
    selected_model = _provider_model(video_provider, edit_mode)
    mask_frame_id = min(
        round(generation_duration * project_fps) + 1,
        max(
            1,
            round(max(0.0, reference_frame_ts - clip_start_ts) * project_fps) + 1,
        ),
    )
    generation_conditioning = GenerationConditioning(
        subject_reference_path=subject_reference_effective,
        subject_reference_timestamp=(
            reference_frame_ts if subject_reference_effective else None
        ),
        mask_image_url=mask_public_url if edit_mode == "tracked_mask" else None,
        mask_frame_id=mask_frame_id,
        start_anchor_path=conditioning_start_anchor,
        start_anchor_timestamp=(start_anchor_ts if conditioning_start_anchor else None),
        end_anchor_path=conditioning_end_anchor,
        end_anchor_timestamp=(end_anchor_ts if conditioning_end_anchor else None),
    )
    conditioned_on = {
        "subject_reference": subject_reference_effective is not None,
        "source_video": source_video_url is not None,
        "start_anchor": conditioning_start_anchor is not None,
        "end_anchor": conditioning_end_anchor is not None,
    }
    warnings: list[str] = []
    if video_provider in {"wan", "happyhorse"} and source_video_url is None:
        warnings.append(
            f"{video_provider} could not access a public source clip; using frame-conditioned generation"
        )
    if video_provider in {"veo", "meshapi_veo"} and conditioning_start_anchor is None:
        warnings.append(
            f"{video_provider} start boundary anchor is unavailable; using text-only generation"
        )
    async with AsyncSessionLocal() as db:
        current_job = await db.get(Job, job_id)
        if current_job is not None:
            current_payload = dict(current_job.payload or {})
            current_payload["warnings"] = warnings
            current_payload["conditioning"] = generation_conditioning.metadata()
            current_payload["selected_model"] = selected_model
            current_payload["selected_edit_mode"] = edit_mode
            current_job.payload = current_payload
            await db.commit()

    def prompt_for_provider(provider: str, correction: str = "") -> str:
        base = (
            protected_context_prompt(
                generation_prompt,
                generation_window,
                temporal_behavior=(
                    planned_intent.temporal_behavior
                    if planned_intent is not None
                    else "temporary"
                ),
                effect_extent=(
                    planned_intent.effect_extent
                    if planned_intent is not None
                    else "subject"
                ),
            )
            if provider in {"wan", "happyhorse"} and source_video_url is not None
            else generation_prompt
        )
        return base + correction

    async def dispatch(
        provider: str,
        correction: str = "",
        *,
        target_variant_id: str,
    ) -> bool:
        attempt_mode = select_source_edit_mode(
            provider,
            duration=generation_duration,
            source_video=source_video_url is not None,
            mask_available=mask_public_url is not None,
            change_type=planned_change_type,
            temporal_behavior=planned_temporal_behavior,
        )
        attempt_conditioning = replace(
            generation_conditioning,
            mask_image_url=(
                mask_public_url if attempt_mode == "tracked_mask" else None
            ),
        )
        return await _run_variant(
            job_id=job_id,
            variant_id=target_variant_id,
            clip_path=clip_path,
            clip_url=clip_url,
            plan={
                **plan,
                "_video_gen_provider": provider,
                "_edit_prompt": prompt_for_provider(provider, correction),
                "_change_type": planned_change_type,
                "_temporal_behavior": planned_temporal_behavior,
            },
            conditioning=attempt_conditioning,
            duration=generation_duration,
            resolution=generation_resolution,
            source_video_url=source_video_url,
        )

    await _emit(
        job_id,
        "gen_start",
        f"dispatching prompt to {video_provider_label}",
        prompt=safe_plan["prompt"],
        strategy=strategy,
        conditioned_on=conditioned_on,
        provider=video_provider,
        model=selected_model,
        edit_mode=edit_mode,
        warnings=warnings,
    )

    attempts = 1
    generated_seconds = generation_duration
    provider_attempts = [video_provider]
    # Technical provider failures contain no visual feedback for a useful
    # correction. End with a precise retryable error instead of paying for a
    # blind second render.
    await dispatch(video_provider, target_variant_id=variant_id)

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
        async with AsyncSessionLocal() as db:
            current_job = await db.get(Job, job_id)
            attempt_failures = list(
                ((current_job.payload or {}).get("attempt_failures") or [])
                if current_job is not None
                else []
            )
        err = (
            attempt_failures[-1].get("technical_message")
            if attempt_failures
            else (variants[0].error if variants else "generation failed")
        )
        log.error("[gen.run] job=%s FAILED all variants — first_error=%s", job_id, err)
        await _fail_job(job_id, err or "generation failed")
        return

    async def maybe_composite(
        variant: Variant,
        provider: str,
    ) -> Variant:
        if (
            not generation_window.adaptive
            or bbox_is_full_frame
            or not sam_video_available
            or not variant.url
        ):
            return variant
        attempt_mode = select_source_edit_mode(
            provider,
            duration=generation_duration,
            source_video=source_video_url is not None,
            mask_available=mask_public_url is not None,
            change_type=planned_change_type,
            temporal_behavior=planned_temporal_behavior,
        )
        if attempt_mode == "tracked_mask":
            await _emit(
                job_id,
                "localized_composite_skipped",
                "provider already used its native tracked-mask edit path",
                provider=provider,
                reason="provider_native_tracked_mask",
            )
            return variant

        try:
            generated_path = await storage.path_from_url(variant.url)
            composite_path, _ = storage.new_path("generated", "mp4")
            result = await composite_generated_target(
                source_path=clip_path,
                generated_path=generated_path,
                bbox=bbox,
                seed_frame_index=mask_frame_id - 1,
                output_path=composite_path,
            )
        except Exception as exc:
            log.exception("generated-output compositing failed; keeping provider output")
            result_metadata: dict[str, Any] = {
                "applied": False,
                "provider": provider,
                "reason": f"output compositing unavailable: {exc}",
            }
        else:
            result_metadata = {
                "applied": result.applied,
                "provider": provider,
                "reason": result.reason,
                "metrics": result.metrics,
            }
            if result.applied and result.path is not None:
                composite_url = await storage.publish(
                    result.path,
                    content_type="video/mp4",
                )
                async with AsyncSessionLocal() as db:
                    await _update_variant(db, variant.id, url=composite_url)
                    refreshed = await db.get(Variant, variant.id)
                    if refreshed is not None:
                        variant = refreshed

        async with AsyncSessionLocal() as db:
            current_job = await db.get(Job, job_id)
            if current_job is not None:
                current_payload = dict(current_job.payload or {})
                history = list(current_payload.get("localized_compositing") or [])
                history.append(result_metadata)
                current_payload["localized_compositing"] = history
                current_job.payload = current_payload
                await db.commit()
        await _emit(
            job_id,
            (
                "localized_composite_done"
                if result_metadata["applied"]
                else "localized_composite_skipped"
            ),
            (
                "composited tracked generated target over original footage"
                if result_metadata["applied"]
                else "kept provider-native output because generated-target tracking was unsafe"
            ),
            **result_metadata,
        )
        return variant

    if generation_window.adaptive:
        done = [await maybe_composite(done[0], video_provider)]

    async def score_variant(variant: Variant) -> dict | None:
        if not variant.url:
            return None
        try:
            sampled_frames = await _sample_variant_frames(variant.url)
            source_frames = await _sample_video_frames(
                clip_path,
                count=len(sampled_frames),
            )
        except Exception as exc:
            log.exception("generated frame sampling failed")
            await _emit(job_id, "score_skipped", f"couldn't sample generated video: {exc}")
            return None
        target_frames: list[str] = []
        moving_target = (
            planned_intent is not None
            and planned_intent.effect_extent in {"motion_path", "new_object_path"}
        )
        if not bbox_is_full_frame and sampled_frames and not moving_target:
            try:
                # Full frames catch broad spill, but a brief wrong colour or
                # shape inside the selected target can be too small to judge
                # there. Score a crop from every chronological sample so a
                # transient target regression cannot hide between endpoints.
                for index in range(len(sampled_frames)):
                    target_path, _ = storage.new_path("keyframes", "png")
                    await ffmpeg.crop_bbox_from_frame(
                        sampled_frames[index],
                        bbox,
                        target_path,
                    )
                    target_frames.append(str(target_path))
            except Exception:
                log.exception("target crop extraction failed; scoring full frames only")
                target_frames = []
        return await _score_variant_safe(
            sampled_frames,
            str(payload.get("user_prompt") or prompt),
            source_frames=source_frames,
            target_frames=target_frames,
            reference_target_path=subject_reference_effective,
            change_type=planned_change_type,
        )

    validation_by_url: dict[str, tuple[ContinuityReport, dict | None]] = {}

    async def validate_continuity(variant: Variant) -> ContinuityReport | None:
        if not variant.url:
            return None
        seam_metadata: dict | None = None
        try:
            generated_path = await storage.path_from_url(variant.url)
            report = await validate_generated_continuity(
                source_path=clip_path,
                generated_path=generated_path,
                window=generation_window,
                bbox=bbox,
            )
            if generation_window.adaptive:
                try:
                    seam_selection = await match_local_context(
                        source_path=clip_path,
                        generated_path=generated_path,
                        window=generation_window,
                        bbox=bbox,
                        tracked_frames=(
                            (payload.get("planned_context") or {}).get("tracked_frames")
                            if isinstance(payload.get("planned_context"), dict)
                            else None
                        ),
                    )
                except Exception as seam_exc:
                    log.exception("frame-matched seam selection failed")
                    await _emit(
                        job_id,
                        "seam_match_unavailable",
                        f"couldn't match safe cut frames: {seam_exc}",
                    )
                else:
                    report = continuity_at_selected_seams(report, seam_selection)
                    seam_metadata = seam_selection.metadata()
                    await _emit(
                        job_id,
                        "seam_match_done",
                        (
                            "found safe source-to-edit cut frames"
                            if seam_selection.passed
                            else "no safe source-to-edit cut frames found"
                        ),
                        **seam_metadata,
                    )
        except Exception as exc:
            log.exception("continuity validation failed")
            await _emit(
                job_id,
                "continuity_validation_unavailable",
                f"couldn't validate generated seams: {exc}",
            )
            return None
        validation_by_url[variant.url] = (report, seam_metadata)
        await _emit(
            job_id,
            "continuity_validation_done",
            "multi-frame seam and handle validation complete",
            **report.metadata(),
        )
        return report

    async def persist_candidate_review(
        variant: Variant,
        candidate_score: dict | None,
        candidate_continuity: ContinuityReport | None,
    ) -> None:
        decision = (
            final_candidate_quality(candidate_score, candidate_continuity)
            if generation_window.adaptive
            else final_semantic_quality(candidate_score)
        )
        final_validation = validation_by_url.get(variant.url or "")
        seams = final_validation[1] if final_validation is not None else None
        review = {
            "attempt": variant.index + 1,
            "label": "First pass" if variant.index == 0 else "Corrected pass",
            "quality_state": decision.action.value,
            "evidence": list(decision.evidence),
            "continuity_validation": (
                candidate_continuity.metadata() if candidate_continuity is not None else None
            ),
            "selected_seams": seams,
            "semantic_score": candidate_score,
        }
        async with AsyncSessionLocal() as db:
            current_job = await db.get(Job, job_id)
            if current_job is not None:
                current_payload = dict(current_job.payload or {})
                reviews = dict(current_payload.get("candidate_reviews") or {})
                reviews[variant.id] = review
                current_payload["candidate_reviews"] = reviews
                current_job.payload = current_payload
            await _update_variant(
                db,
                variant.id,
                visual_coherence=(candidate_score or {}).get("visual_coherence"),
                prompt_adherence=(candidate_score or {}).get("prompt_adherence"),
            )
            await db.commit()
        await _emit(
            job_id,
            "candidate_review_done",
            (
                "first pass review complete"
                if variant.index == 0
                else "corrected pass review complete"
            ),
            variant_id=variant.id,
            variant_index=variant.index,
            **review,
        )

    await _emit(
        job_id,
        "score_start",
        "sampling generated video for quality and continuity scoring",
    )
    score, continuity_report = await asyncio.gather(
        score_variant(done[0]),
        validate_continuity(done[0]),
    )
    await persist_candidate_review(done[0], score, continuity_report)

    quality_state = GenerationQualityAction.PASS
    quality_evidence: list[str] = []
    if generation_window.adaptive:
        decision = decide_generation_quality(
            score=score,
            continuity=continuity_report,
            duration=generation_duration,
            attempts=attempts,
            generated_seconds=generated_seconds,
        )
        while decision.action == GenerationQualityAction.CORRECTIVE_RETRY:
            if not await _await_retry_permission(job_id, decision.evidence):
                decision = final_candidate_quality(score, continuity_report)
                break
            previous_variant = done[0]
            previous_score = score
            previous_continuity = continuity_report
            previous_provider = video_provider
            next_provider = video_provider
            attempts += 1
            generated_seconds += generation_duration
            provider_attempts.append(next_provider)
            retry_variant_id = await create_attempt_variant(attempts - 1)
            await _emit(
                job_id,
                "gen_retry",
                f"review found a specific issue; retrying {_provider_label(next_provider)} once",
                attempt=attempts,
                provider=next_provider,
                evidence=list(decision.evidence),
                generated_seconds=generated_seconds,
            )
            succeeded = await dispatch(
                next_provider,
                corrective_prompt(decision.evidence),
                target_variant_id=retry_variant_id,
            )
            video_provider = next_provider
            async with AsyncSessionLocal() as db:
                retried = await db.get(Variant, retry_variant_id)
                generation_error = (
                    retried.error
                    if retried is not None and not succeeded
                    else None
                )
                if retried is not None and succeeded and retried.status == "done":
                    done = [retried]
                else:
                    done = [previous_variant]
            if succeeded:
                done = [await maybe_composite(done[0], video_provider)]
                candidate_score, candidate_continuity = await asyncio.gather(
                    score_variant(done[0]),
                    validate_continuity(done[0]),
                )
                await persist_candidate_review(
                    done[0], candidate_score, candidate_continuity
                )
                if attempt_quality_rank(
                    candidate_score,
                    candidate_continuity,
                ) > attempt_quality_rank(previous_score, previous_continuity):
                    score = candidate_score
                    continuity_report = candidate_continuity
                else:
                    done = [previous_variant]
                    score = previous_score
                    continuity_report = previous_continuity
                    video_provider = previous_provider
                    await _emit(
                        job_id,
                        "gen_retry_rejected",
                        "retry scored no better; retaining the stronger result",
                        attempt=attempts,
                    )
            else:
                await _emit(
                    job_id,
                    "gen_retry_failed",
                    "generation attempt failed; retaining the last rendered result",
                    provider=next_provider,
                    error=generation_error,
                )
            decision = decide_generation_quality(
                score=score,
                continuity=continuity_report,
                duration=generation_duration,
                attempts=attempts,
                generated_seconds=generated_seconds,
                generation_error=generation_error,
            )
        quality_state = decision.action
        quality_evidence = list(decision.evidence)
    else:
        needs_retry = isinstance(score, dict) and (
            int(score.get("visual_coherence") or 0) < 5
            or int(score.get("prompt_adherence") or 0) < 6
        )
        if needs_retry:
            retry_evidence = semantic_quality_evidence(score)
            if not await _await_retry_permission(job_id, retry_evidence):
                needs_retry = False
            previous_variant = done[0]
            previous_score = score
            previous_continuity = continuity_report
            attempts += int(needs_retry)
            if not needs_retry:
                final_quality = final_semantic_quality(score)
                quality_state = final_quality.action
                quality_evidence = list(final_quality.evidence)
            else:
                generated_seconds += generation_duration
                provider_attempts.append(video_provider)
                retry_variant_id = await create_attempt_variant(attempts - 1)
            if needs_retry:
                await _emit(
                    job_id,
                    "gen_retry",
                    "quality validation failed; making one corrective generation attempt",
                    attempt=attempts,
                    previous_score=score,
                )
            correction = corrective_prompt(retry_evidence)
            succeeded = needs_retry and await dispatch(
                video_provider,
                correction,
                target_variant_id=retry_variant_id,
            )
            if needs_retry:
                async with AsyncSessionLocal() as db:
                    retried = await db.get(Variant, retry_variant_id)
                if retried is not None and succeeded and retried.status == "done":
                    done = [retried]
                    candidate_score, candidate_continuity = await asyncio.gather(
                        score_variant(retried),
                        validate_continuity(retried),
                    )
                    await persist_candidate_review(
                        retried, candidate_score, candidate_continuity
                    )
                    if attempt_quality_rank(
                        candidate_score,
                        candidate_continuity,
                    ) > attempt_quality_rank(previous_score, previous_continuity):
                        score = candidate_score
                        continuity_report = candidate_continuity
                    else:
                        done = [previous_variant]
                        score = previous_score
                        continuity_report = previous_continuity
                        await _emit(
                            job_id,
                            "gen_retry_rejected",
                            "retry scored no better; retaining the stronger result",
                            attempt=attempts,
                        )
                elif retried is not None:
                    done = [previous_variant]
                    await _emit(
                        job_id,
                        "gen_retry_failed",
                        "corrective attempt failed; retaining the first generated result",
                    )
            if needs_retry:
                final_quality = final_semantic_quality(score)
                quality_state = final_quality.action
                quality_evidence = list(final_quality.evidence)
        else:
            final_quality = final_semantic_quality(score)
            quality_state = final_quality.action
            quality_evidence = list(final_quality.evidence)

    local_flow_telemetry: dict[str, Any] | None = None
    async with AsyncSessionLocal() as db:
        current_job = await db.get(Job, job_id)
        if current_job is not None:
            current_payload = dict(current_job.payload or {})
            final_edit_mode = select_source_edit_mode(
                video_provider,
                duration=generation_duration,
                source_video=source_video_url is not None,
                mask_available=mask_public_url is not None,
                change_type=planned_change_type,
                temporal_behavior=planned_temporal_behavior,
            )
            current_payload.update(
                {
                    "generation_quality_state": quality_state.value,
                    "generation_quality_evidence": quality_evidence,
                    "generation_attempts": attempts,
                    "generated_seconds": generated_seconds,
                    "provider_attempts": provider_attempts,
                    "selected_provider": video_provider,
                    "selected_model": _provider_model(video_provider, final_edit_mode),
                    "selected_edit_mode": final_edit_mode,
                    "recommended_variant_id": done[0].id,
                }
            )
            retry_state = dict(current_payload.get("retry_state") or {})
            if retry_state.get("status") == "dispatched":
                retry_state["status"] = "completed"
                retry_state["completed_at"] = time.time()
                current_payload["retry_state"] = retry_state
            final_validation = validation_by_url.get(done[0].url or "")
            if final_validation is not None:
                final_report, final_seams = final_validation
                current_payload["continuity_validation"] = final_report.metadata()
                if final_seams is not None:
                    current_payload["selected_seams"] = final_seams
            local_flow_telemetry = build_local_flow_telemetry(
                payload=current_payload,
                window=generation_window,
                continuity=continuity_report,
                quality_state=quality_state.value,
                attempts=attempts,
                generated_seconds=generated_seconds,
                provider_attempts=provider_attempts,
                selected_provider=video_provider,
            )
            current_payload["local_flow_telemetry"] = local_flow_telemetry
            current_job.payload = current_payload
            await db.commit()
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

    if local_flow_telemetry is not None:
        await _emit(
            job_id,
            "local_flow_metrics",
            "recorded local generation cost and seam metrics",
            **local_flow_telemetry,
        )

    log.info(
        "[gen.run] job=%s COMPLETE variant_url=%s",
        job_id,
        done[0].url,
    )

    await _emit_terminal(
        job_id,
        "done",
        (
            "generation complete with a continuity hard-fail"
            if quality_state == GenerationQualityAction.HARD_FAIL
            else "generation complete"
        ),
        variant_url=done[0].url,
        generation_quality_state=quality_state.value,
        generation_quality_evidence=quality_evidence,
        acceptance_blocked=quality_state == GenerationQualityAction.HARD_FAIL,
        attempts=attempts,
        generated_seconds=generated_seconds,
        local_flow_telemetry=local_flow_telemetry,
    )


async def run(job_id: str) -> None:
    """Keep an unexpected worker exception from leaving a job stuck forever."""
    try:
        await _run(job_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception("generate job crashed job=%s", job_id)
        await _fail_job(job_id, str(exc).strip() or type(exc).__name__)
