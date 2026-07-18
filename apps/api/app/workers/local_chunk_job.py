"""Render and validate one long local edit as overlapping provider-sized chunks."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models.edit_plan import EditPlanRecord, GenerationChunk
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.selection import SelectionArtifact
from app.schemas.edit_plan import EditIntent, LocalRangeResolution
from app.services import ffmpeg, job_events, storage
from app.services.edit_prompt import planned_edit_prompt
from app.services.generation_quality import final_semantic_quality
from app.services.generation_failure import classify_generation_failure
from app.services.job_progress import persist_job_progress
from app.services.generation_window import GenerationWindow
from app.services.global_chunk_execution import (
    PreviousChunk,
    execute_global_chunk,
    target_bbox,
)
from app.services.global_seam import assemble_global_occurrence
from app.workers.generate_job import (
    _provider_model,
    _sample_variant_frames,
    _score_variant_safe,
)


log = logging.getLogger("fiebatt.jobs.local_chunks")


@dataclass(slots=True)
class ExecutableChunk:
    id: str
    index: int
    edit_start: float
    edit_end: float
    context_start: float
    context_end: float
    provider: str
    payload_json: dict
    output_url: str | None = None


async def _emit(job_id: str, stage: str, message: str, *, terminal: bool = False, **data: Any) -> None:
    event: dict[str, Any] = {
        "stage": stage,
        "msg": message,
        "ts": time.time(),
    }
    if terminal:
        event["terminal"] = True
    if data:
        event["data"] = data
    await job_events.publish(job_id, event)
    if stage != "chunk_poll":
        await persist_job_progress(
            job_id,
            stage=stage,
            message=message,
            status=("done" if stage == "done" else "failed" if terminal else "running"),
            data={key: value for key, value in data.items() if key != "variant_url"},
            session_factory=AsyncSessionLocal,
        )


async def _reference_subject(
    project: Project,
    selection: SelectionArtifact,
) -> Path:
    if selection.subject_reference_url:
        try:
            return await storage.path_from_url(selection.subject_reference_url)
        except Exception:
            log.warning("stored subject reference unavailable; rebuilding it", exc_info=True)
    frame_path, _ = storage.new_path("keyframes", "jpg")
    source_path = await storage.materialize_source(project.video_path, project.video_url)
    await ffmpeg.extract_frame(source_path, selection.frame_ts, frame_path)
    return await ffmpeg.crop_bbox_from_frame(frame_path, selection.bbox_json)


async def _fail(
    job_id: str,
    error: str,
    *,
    variant_id: str | None = None,
    chunk_id: str | None = None,
) -> None:
    failure = classify_generation_failure(error)
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is not None:
            payload = dict(job.payload or {})
            payload["failure_state"] = failure.metadata()
            job.payload = payload
            job.status = "error"
            job.error = failure.user_message
        if variant_id:
            variant = await db.get(Variant, variant_id)
            if variant is not None:
                variant.status = "error"
                variant.error = failure.user_message
        if chunk_id:
            chunk = await db.get(GenerationChunk, chunk_id)
            if chunk is not None:
                chunk.status = "error"
        await db.commit()
    await _emit(
        job_id,
        "failed",
        failure.user_message,
        terminal=True,
        code=failure.code,
        retryable=failure.retryable,
    )


async def run(job_id: str) -> None:
    variant_id: str | None = None
    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            if job is None:
                return
            project = await db.get(Project, job.project_id)
            plan_id = str((job.payload or {}).get("plan_id") or "")
            plan = (
                await db.execute(
                    select(EditPlanRecord)
                    .where(EditPlanRecord.id == plan_id)
                    .options(selectinload(EditPlanRecord.chunks))
                )
            ).scalar_one_or_none()
            if project is None or plan is None:
                raise ValueError("chunked edit source or plan is missing")
            selection = await db.get(SelectionArtifact, plan.selection_id)
            if selection is None or selection.project_id != project.id:
                raise ValueError("chunked edit selection is missing")
            chunks = list(plan.chunks)
            if len(chunks) < 2:
                raise ValueError("chunked edit requires at least two planned chunks")
            resolution = LocalRangeResolution.model_validate(plan.range_json)
            if resolution.edit_core.duration > 30.05:
                raise ValueError(
                    "this edit covers more than 30 seconds; video rendering was not started"
                )
            intent = EditIntent.model_validate(plan.intent_json)
            grounded = (
                intent.grounded_edit.model_dump(mode="json")
                if intent.grounded_edit is not None
                else {}
            )
            generation_prompt = planned_edit_prompt(plan.raw_prompt, grounded)
            variant = Variant(job_id=job.id, index=0, status="processing")
            db.add(variant)
            await db.flush()
            variant_id = variant.id
            job.status = "processing"
            job.error = None
            payload = dict(job.payload or {})
            window = GenerationWindow(
                core_start=resolution.edit_core.start_ts,
                core_end=resolution.edit_core.end_ts,
                context_start=resolution.generation_context.start_ts,
                context_end=resolution.generation_context.end_ts,
                adaptive=True,
            )
            payload.update(
                {
                    "execution_window": window.metadata(),
                    "generation_attempts": len(chunks),
                    "generated_seconds": sum(
                        chunk.context_end - chunk.context_start for chunk in chunks
                    ),
                    "provider_attempts": [chunk.provider for chunk in chunks],
                    "selected_provider": plan.provider,
                    "selected_model": _provider_model(plan.provider, "source_video"),
                    "selected_edit_mode": "source_video",
                }
            )
            job.payload = payload
            await db.commit()

        await _emit(
            job_id,
            "chunk_plan",
            f"rendering {len(chunks)} overlapping clips for the tracked edit window",
            chunk_count=len(chunks),
        )
        reference_subject = await _reference_subject(project, selection)
        executable = [
            ExecutableChunk(
                id=chunk.id,
                index=chunk.index,
                edit_start=chunk.edit_start,
                edit_end=chunk.edit_end,
                context_start=chunk.context_start,
                context_end=chunk.context_end,
                provider=chunk.provider,
                payload_json=dict(chunk.payload_json or {}),
            )
            for chunk in chunks
        ]

        previous: PreviousChunk | None = None
        for chunk in executable:
            async with AsyncSessionLocal() as db:
                row = await db.get(GenerationChunk, chunk.id)
                if row is not None:
                    row.status = "processing"
                    await db.commit()
            await _emit(
                job_id,
                "chunk_start",
                f"rendering clip {chunk.index + 1} of {len(executable)}",
                chunk_index=chunk.index,
                provider=chunk.provider,
            )

            async def chunk_tick(event: dict, *, chunk_index: int = chunk.index) -> None:
                kind = str(event.get("kind") or "gen.poll")
                elapsed = event.get("elapsed")
                await _emit(
                    job_id,
                    "chunk_submit" if kind == "gen.submit" else "chunk_poll",
                    (
                        f"video model accepted clip {chunk_index + 1} of {len(executable)}"
                        if kind == "gen.submit"
                        else f"rendering clip {chunk_index + 1} of {len(executable)}"
                        + (f" · {elapsed}s elapsed" if elapsed is not None else "")
                    ),
                    chunk_index=chunk_index,
                    elapsed=elapsed,
                )

            try:
                result = await execute_global_chunk(
                    project=project,
                    chunk=chunk,  # type: ignore[arg-type]
                    prompt=generation_prompt,
                    reference_subject_path=reference_subject,
                    previous=previous,
                    on_tick=chunk_tick,
                )
            except Exception as exc:
                await _fail(
                    job_id,
                    str(exc).strip() or type(exc).__name__,
                    variant_id=variant_id,
                    chunk_id=chunk.id,
                )
                return
            chunk.output_url = result.output_url
            previous = PreviousChunk(
                context_start=chunk.context_start,
                context_end=chunk.context_end,
                output_url=result.output_url,
            )
            async with AsyncSessionLocal() as db:
                row = await db.get(GenerationChunk, chunk.id)
                if row is not None:
                    payload = dict(row.payload_json or {})
                    payload["execution"] = result.metadata
                    payload["output_url"] = result.output_url
                    row.payload_json = payload
                    row.status = "generated"
                    await db.commit()

        occurrence = SimpleNamespace(
            edit_start=resolution.generation_context.start_ts,
            edit_end=resolution.generation_context.end_ts,
        )
        assembly = await assemble_global_occurrence(
            project=project,
            occurrence=occurrence,  # type: ignore[arg-type]
            chunks=executable,  # type: ignore[arg-type]
        )
        frames = await _sample_variant_frames(assembly.output_url)
        target_frames: list[str] = []
        media_duration = resolution.generation_context.duration
        for index, frame in enumerate(frames):
            crop_path, _ = storage.new_path("keyframes", "png")
            sample_timestamp = (
                resolution.generation_context.start_ts
                + media_duration * (index + 0.5) / len(frames)
            )
            try:
                sample_bbox = target_bbox(
                    {"track_frames": resolution.tracked_frames},
                    sample_timestamp,
                )
            except ValueError:
                sample_bbox = selection.bbox_json
            await ffmpeg.crop_bbox_from_frame(
                frame,
                sample_bbox,
                crop_path,
            )
            target_frames.append(str(crop_path))
        score = await _score_variant_safe(
            frames,
            generation_prompt,
            target_frames=target_frames,
            reference_target_path=str(reference_subject),
        )
        quality = final_semantic_quality(score)

        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            variant = await db.get(Variant, variant_id)
            if job is None or variant is None:
                raise ValueError("chunked edit result record is missing")
            payload = dict(job.payload or {})
            payload.update(
                {
                    "generation_quality_state": quality.action.value,
                    "generation_quality_evidence": list(quality.evidence),
                    "chunk_seams": [seam.metadata() for seam in assembly.seams],
                    "continuity_validation": assembly.continuity,
                }
            )
            job.payload = payload
            job.status = "done"
            variant.status = "done"
            variant.url = assembly.output_url
            variant.description = grounded.get("description") or plan.raw_prompt
            if isinstance(score, dict):
                variant.visual_coherence = score.get("visual_coherence")
                variant.prompt_adherence = score.get("prompt_adherence")
            await db.commit()

        await _emit(
            job_id,
            "done",
            (
                "generation complete with a quality hard-fail"
                if quality.evidence
                else "generation complete"
            ),
            terminal=True,
            variant_url=assembly.output_url,
            generation_quality_state=quality.action.value,
            generation_quality_evidence=list(quality.evidence),
            acceptance_blocked=bool(quality.evidence),
        )
    except Exception as exc:
        log.exception("chunked local generation failed")
        await _fail(
            job_id,
            str(exc).strip() or type(exc).__name__,
            variant_id=variant_id,
        )
