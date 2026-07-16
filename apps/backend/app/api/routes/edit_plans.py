import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.services.provider_capabilities import (
    VIDEO_PROVIDER_CAPABILITIES,
    select_video_provider,
    validate_provider_duration,
)
from app.config.settings import get_settings
from app.db.session import get_db
from app.deps import get_session
from app.models.edit_plan import EditPlanRecord, GenerationChunk
from app.models.project import Project
from app.models.selection import SelectionArtifact
from app.models.session import Session as SessionModel
from app.schemas.edit_plan import EditCore, EditIntent, LocalRangeResolution
from app.schemas.edit_plan_api import (
    EditPlanRequest,
    EditPlanResponse,
    GenerationChunkResponse,
    PlanEstimateResponse,
)
from app.services.edit_scope import plan_prompt_intent
from app.services.local_range import resolve_local_range


router = APIRouter(prefix="/edit-plans", tags=["edit-plans"])


def _response(plan: EditPlanRecord) -> EditPlanResponse:
    intent = EditIntent.model_validate(plan.intent_json)
    resolution = LocalRangeResolution.model_validate(plan.range_json)
    chunks = [
        GenerationChunkResponse(
            id=chunk.id,
            index=chunk.index,
            edit_core=EditCore(start_ts=chunk.edit_start, end_ts=chunk.edit_end),
            generation_context=resolution.generation_context,
            provider=chunk.provider,
            status=chunk.status,
        )
        for chunk in plan.chunks
    ]
    return EditPlanResponse(
        plan_id=plan.id,
        project_id=plan.project_id,
        selection_id=plan.selection_id,
        scope=plan.scope,  # type: ignore[arg-type]
        intent=intent,
        edit_core=resolution.edit_core,
        generation_context=resolution.generation_context,
        occurrence_start=resolution.occurrence_start,
        occurrence_end=resolution.occurrence_end,
        provider=plan.provider,
        provider_reason=plan.provider_reason,
        estimate=PlanEstimateResponse.model_validate(plan.estimate_json),
        confidence=resolution.confidence,
        warnings=list(plan.warnings_json or []),
        chunks=chunks,
        status=plan.status,
        adaptive_generation_enabled=get_settings().adaptive_edit_planning,
    )


@router.post("", response_model=EditPlanResponse, status_code=201)
async def create_edit_plan(
    body: EditPlanRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    analysis_started = time.perf_counter()
    project = await db.get(Project, body.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")
    selection = await db.get(SelectionArtifact, body.selection_id)
    if selection is None or selection.project_id != project.id:
        raise HTTPException(status_code=404, detail="selection not found")
    if selection.source_revision != project.video_url:
        raise HTTPException(status_code=409, detail="selection is stale for current source")

    explicit_core = None
    if body.explicit_start_ts is not None and body.explicit_end_ts is not None:
        if body.explicit_end_ts > project.duration + 1e-3:
            raise HTTPException(status_code=422, detail="explicit range exceeds project")
        explicit_core = EditCore(
            start_ts=body.explicit_start_ts,
            end_ts=body.explicit_end_ts,
        )
    gate = plan_prompt_intent(
        body.prompt,
        explicit_range=explicit_core is not None,
        requested_scope=body.requested_scope,
        structured_intent=body.structured_intent,
    )
    if gate.intent.scope in {"all_occurrences", "selected_occurrences"}:
        raise HTTPException(
            status_code=422,
            detail="occurrence discovery is not available in the local planning rollout",
        )

    try:
        resolution = await resolve_local_range(
            project, selection, gate.intent, explicit_core=explicit_core
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    context_duration = resolution.generation_context.duration
    provider = select_video_provider(
        body.video_gen_provider,
        source_video=True,
        duration=context_duration,
    )
    provider_error = validate_provider_duration(provider, context_duration)
    if provider_error:
        raise HTTPException(status_code=422, detail=provider_error)
    capabilities = VIDEO_PROVIDER_CAPABILITIES[provider]
    provider_reason = (
        f"selected {provider} for source-video context"
        if capabilities.source_video_edit
        else f"selected {provider} image conditioning; source-video context is unsupported"
    )
    warnings = list(resolution.warnings)
    if not get_settings().adaptive_edit_planning:
        warnings.append(
            "adaptive generation rollout is disabled; render will use the legacy fixed window"
        )
    if not capabilities.source_video_edit:
        warnings.append(f"{provider} cannot consume source-video motion context")

    estimate = {
        "analysis_mode": gate.estimate.analysis_mode,
        "analysis_duration_ms": round(
            (time.perf_counter() - analysis_started) * 1000.0, 3
        ),
        "frames_inspected": resolution.frames_inspected,
        "expected_generation_calls": 1,
        "expected_generated_seconds": context_duration,
        "requires_global_discovery": gate.estimate.requires_global_discovery,
    }
    plan = EditPlanRecord(
        project_id=project.id,
        selection_id=selection.id,
        raw_prompt=body.prompt,
        scope=gate.intent.scope,
        intent_json=gate.intent.model_dump(mode="json"),
        range_json=resolution.model_dump(mode="json"),
        estimate_json=estimate,
        provider=provider,
        provider_reason=provider_reason,
        warnings_json=warnings,
        source_revision=project.video_url,
    )
    db.add(plan)
    await db.flush()
    context = resolution.generation_context
    chunk = GenerationChunk(
        plan_id=plan.id,
        index=0,
        edit_start=resolution.edit_core.start_ts,
        edit_end=resolution.edit_core.end_ts,
        context_start=context.start_ts,
        context_end=context.end_ts,
        provider=provider,
        payload_json={
            "selection_id": selection.id,
            "subject_reference_url": selection.subject_reference_url,
            "mask_url": selection.mask_url,
            "source_revision": selection.source_revision,
        },
    )
    db.add(chunk)
    await db.commit()
    loaded = (
        await db.execute(
            select(EditPlanRecord)
            .where(EditPlanRecord.id == plan.id)
            .options(selectinload(EditPlanRecord.chunks))
        )
    ).scalar_one()
    return _response(loaded)


@router.get("/{plan_id}", response_model=EditPlanResponse)
async def get_edit_plan(
    plan_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    plan = (
        await db.execute(
            select(EditPlanRecord)
            .where(EditPlanRecord.id == plan_id)
            .options(selectinload(EditPlanRecord.chunks))
        )
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="edit plan not found")
    project = await db.get(Project, plan.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="edit plan not found")
    return _response(plan)
