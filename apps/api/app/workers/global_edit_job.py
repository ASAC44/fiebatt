"""Generate confirmed global occurrences without mutating the timeline."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models.job import Variant
from app.models.project import Project
from app.models.propagation import (
    GlobalEditPlan,
    GlobalGenerationChunk,
    GlobalOccurrencePlan,
    PropagationJob,
    PropagationResult,
)
from app.services.global_chunk_execution import (
    PreviousChunk,
    execute_global_chunk,
    prepare_reference_subject,
)
from app.services.global_chunk_sequence import (
    MAX_CHUNK_ATTEMPTS,
    ChunkExecution,
    ChunkState,
    run_chunk_sequence,
)
from app.services.global_seam import GlobalSeamError, assemble_global_occurrence


log = logging.getLogger("fiebatt.jobs.global_edit")
OCCURRENCE_CONCURRENCY = 2


async def _mark_plan_failed(job_id: str, plan_id: str, error: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(PropagationJob, job_id)
        plan = await db.get(GlobalEditPlan, plan_id)
        if job is not None:
            job.status = "error"
            job.error = error[:500]
        if plan is not None:
            plan.status = "error"
        await db.commit()


async def _run_occurrence(
    *,
    occurrence_plan_id: str,
    result_id: str,
    project: Project,
    prompt: str,
    source_revision: str,
    reference_subject_path: Path,
) -> bool:
    async with AsyncSessionLocal() as db:
        occurrence = (
            await db.execute(
                select(GlobalOccurrencePlan)
                .where(GlobalOccurrencePlan.id == occurrence_plan_id)
                .options(selectinload(GlobalOccurrencePlan.chunks))
            )
        ).scalar_one_or_none()
        if occurrence is None:
            return False
        chunks = list(occurrence.chunks)
        occurrence.status = "processing"
        result = await db.get(PropagationResult, result_id)
        if result is not None:
            result.status = "processing"
            result.error = None
        await db.commit()

    by_id = {chunk.id: chunk for chunk in chunks}
    by_index = {chunk.index: chunk for chunk in chunks}
    states = [
        ChunkState(
            id=chunk.id,
            index=chunk.index,
            status=chunk.status,
            input_revision=chunk.input_revision,
            output_url=chunk.output_url,
        )
        for chunk in chunks
    ]

    async def mark_started(chunk_state: ChunkState, input_revision: str) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(GlobalGenerationChunk, chunk_state.id)
            if row is not None:
                if row.attempts >= MAX_CHUNK_ATTEMPTS:
                    raise ValueError(
                        f"chunk retry limit reached after {MAX_CHUNK_ATTEMPTS} attempts"
                    )
                row.status = "processing"
                row.input_revision = input_revision
                row.output_url = None
                row.execution_json = {}
                row.attempts += 1
                row.error = None
                await db.commit()

    async def mark_succeeded(
        chunk_state: ChunkState,
        input_revision: str,
        execution: ChunkExecution,
    ) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(GlobalGenerationChunk, chunk_state.id)
            if row is not None:
                row.status = "generated"
                row.input_revision = input_revision
                row.output_url = execution.output_url
                row.execution_json = execution.metadata
                row.error = None
                await db.commit()

    async def mark_failed(chunk_state: ChunkState, error: str) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(GlobalGenerationChunk, chunk_state.id)
            if row is not None:
                row.status = "error"
                row.error = error[:500]
            occurrence_row = await db.get(GlobalOccurrencePlan, occurrence_plan_id)
            if occurrence_row is not None:
                occurrence_row.status = "error"
            result_row = await db.get(PropagationResult, result_id)
            if result_row is not None:
                result_row.status = "error"
                result_row.error = error[:500]
            await db.commit()

    async def execute(
        chunk_state: ChunkState,
        previous_output: str | None,
    ) -> ChunkExecution:
        chunk = by_id[chunk_state.id]
        previous: PreviousChunk | None = None
        if chunk.index > 0:
            previous_chunk = by_index[chunk.index - 1]
            if previous_output is None:
                raise ValueError("global chunk dependency has no output")
            previous = PreviousChunk(
                context_start=previous_chunk.context_start,
                context_end=previous_chunk.context_end,
                output_url=previous_output,
            )
        return await execute_global_chunk(
            project=project,
            chunk=chunk,
            prompt=prompt,
            reference_subject_path=reference_subject_path,
            previous=previous,
        )

    outcome = await run_chunk_sequence(
        states,
        source_revision=source_revision,
        execute=execute,
        mark_started=mark_started,
        mark_succeeded=mark_succeeded,
        mark_failed=mark_failed,
    )
    if not outcome.completed:
        return False
    for chunk, output_url in zip(
        sorted(chunks, key=lambda item: item.index),
        outcome.output_urls,
        strict=True,
    ):
        chunk.output_url = output_url
    try:
        assembly = await assemble_global_occurrence(
            project=project,
            occurrence=occurrence,
            chunks=chunks,
        )
    except Exception as exc:
        error = str(exc).strip() or type(exc).__name__
        log.exception("global occurrence assembly failed")
        async with AsyncSessionLocal() as db:
            if isinstance(exc, GlobalSeamError):
                affected = (
                    await db.execute(
                        select(GlobalGenerationChunk).where(
                            GlobalGenerationChunk.occurrence_plan_id
                            == occurrence_plan_id,
                            GlobalGenerationChunk.index >= exc.retry_chunk_index,
                        )
                    )
                ).scalars().all()
                for chunk in affected:
                    chunk.status = "planned"
                    chunk.input_revision = None
                    chunk.output_url = None
                    chunk.execution_json = {}
                    chunk.error = error[:500]
            occurrence_row = await db.get(GlobalOccurrencePlan, occurrence_plan_id)
            result_row = await db.get(PropagationResult, result_id)
            if occurrence_row is not None:
                occurrence_row.status = "error"
                occurrence_row.output_url = None
                occurrence_row.seams_json = []
                occurrence_row.continuity_json = {}
                occurrence_row.error = error[:500]
            if result_row is not None:
                result_row.status = "error"
                result_row.variant_url = None
                result_row.error = error[:500]
            await db.commit()
        return False
    async with AsyncSessionLocal() as db:
        occurrence = await db.get(GlobalOccurrencePlan, occurrence_plan_id)
        result = await db.get(PropagationResult, result_id)
        if occurrence is not None:
            occurrence.status = "done"
            occurrence.output_url = assembly.output_url
            occurrence.seams_json = [seam.metadata() for seam in assembly.seams]
            occurrence.continuity_json = assembly.continuity
            occurrence.error = None
        if result is not None:
            result.status = "done"
            result.variant_url = assembly.output_url
            result.error = None
        await db.commit()
    return True


async def _run(job_id: str, plan_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(PropagationJob, job_id)
        plan = await db.get(GlobalEditPlan, plan_id)
        if job is None or plan is None:
            return
        project = await db.get(Project, job.project_id)
        variant = await db.get(Variant, plan.reference_variant_id)
        if project is None or variant is None or not variant.url:
            await _mark_plan_failed(
                job_id,
                plan_id,
                "global edit source or accepted reference is missing",
            )
            return
        job.status = "processing"
        job.error = None
        plan.status = "running"
        occurrence_plans = (
            await db.execute(
                select(GlobalOccurrencePlan)
                .where(GlobalOccurrencePlan.global_plan_id == plan.id)
                .order_by(GlobalOccurrencePlan.index)
            )
        ).scalars().all()
        results = (
            await db.execute(
                select(PropagationResult).where(
                    PropagationResult.propagation_job_id == job_id
                )
            )
        ).scalars().all()
        result_by_appearance = {result.appearance_id: result.id for result in results}
        prompt = plan.prompt
        source_revision = plan.source_revision
        reference_json = dict(plan.reference_json or {})
        reference_url = variant.url
        await db.commit()

    try:
        reference_subject_path = await prepare_reference_subject(
            reference_video_url=reference_url,
            reference_json=reference_json,
        )
    except Exception as exc:
        log.exception("global reference preparation failed")
        await _mark_plan_failed(job_id, plan_id, f"reference failed: {exc}")
        return

    semaphore = asyncio.Semaphore(OCCURRENCE_CONCURRENCY)

    async def run_occurrence(occurrence: GlobalOccurrencePlan) -> bool:
        result_id = result_by_appearance.get(occurrence.appearance_id)
        if result_id is None:
            return False
        async with semaphore:
            return await _run_occurrence(
                occurrence_plan_id=occurrence.id,
                result_id=result_id,
                project=project,
                prompt=prompt,
                source_revision=source_revision,
                reference_subject_path=reference_subject_path,
            )

    outcomes = await asyncio.gather(
        *(run_occurrence(occurrence) for occurrence in occurrence_plans),
        return_exceptions=True,
    )
    failed = any(outcome is not True for outcome in outcomes)
    async with AsyncSessionLocal() as db:
        job = await db.get(PropagationJob, job_id)
        plan = await db.get(GlobalEditPlan, plan_id)
        if job is None or plan is None:
            return
        if failed:
            job.status = "error"
            job.error = "one or more global occurrences failed"
            plan.status = "error"
        else:
            job.status = "done"
            plan.status = "done"
        await db.commit()


async def run(job_id: str, plan_id: str) -> None:
    await _run(job_id, plan_id)
