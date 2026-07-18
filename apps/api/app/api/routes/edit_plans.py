import time
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.services.provider_capabilities import (
    VIDEO_PROVIDER_CAPABILITIES,
    select_video_provider,
    validate_provider_duration,
)
from app.ai import services as ai
from app.config.settings import get_settings
from app.db.session import get_db
from app.deps import get_session
from app.models.edit_plan import EditPlanRecord, GenerationChunk
from app.models.project import Project
from app.models.selection import SelectionArtifact
from app.models.session import Session as SessionModel
from app.schemas.edit_plan import (
    EditCore,
    EditIntent,
    GenerationContext,
    LocalRangeResolution,
    SemanticEditPlan,
)
from app.schemas.edit_plan_api import (
    EditPlanRequest,
    EditPlanResponse,
    GenerationChunkResponse,
    PlanEstimateResponse,
)
from app.services.edit_scope import plan_prompt_intent
from app.services.local_range import EditWindowLimitError, resolve_local_range
from app.services.global_chunk_planner import (
    PlannedGlobalChunk,
    plan_occurrence_chunks,
    split_evidence_from_track_frames,
)
from app.services import ffmpeg, storage
from app.services.edit_source import materialize_edit_source, source_for_selection


router = APIRouter(prefix="/edit-plans", tags=["edit-plans"])
log = logging.getLogger("fiebatt.edit_plans")


def _response(plan: EditPlanRecord) -> EditPlanResponse:
    intent = EditIntent.model_validate(plan.intent_json)
    resolution = LocalRangeResolution.model_validate(plan.range_json)
    chunks = [
        GenerationChunkResponse(
            id=chunk.id,
            index=chunk.index,
            edit_core=EditCore(start_ts=chunk.edit_start, end_ts=chunk.edit_end),
            generation_context=GenerationContext(
                start_ts=chunk.context_start,
                end_ts=chunk.context_end,
                edit_core=EditCore(
                    start_ts=chunk.edit_start,
                    end_ts=chunk.edit_end,
                ),
            ),
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
    try:
        edit_source = source_for_selection(project, selection)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    source_start = body.source_start_ts if body.source_start_ts is not None else 0.0
    source_end = body.source_end_ts if body.source_end_ts is not None else edit_source.duration
    if source_end > edit_source.duration + 1e-3:
        raise HTTPException(status_code=422, detail="source range exceeds selected clip")
    if not source_start - 1e-3 <= selection.frame_ts <= source_end + 1e-3:
        raise HTTPException(status_code=422, detail="selection is outside the active source clip")

    explicit_core = None
    if body.explicit_start_ts is not None and body.explicit_end_ts is not None:
        if body.explicit_end_ts > edit_source.duration + 1e-3:
            raise HTTPException(status_code=422, detail="explicit range exceeds selected clip")
        if (
            body.explicit_start_ts < source_start - 1e-3
            or body.explicit_end_ts > source_end + 1e-3
        ):
            raise HTTPException(status_code=422, detail="explicit range exceeds active source clip")
        explicit_core = EditCore(
            start_ts=body.explicit_start_ts,
            end_ts=body.explicit_end_ts,
        )
    semantic_warning: str | None = None
    structured_intent = body.structured_intent
    if structured_intent is None:
        planning_frame = ""
        if selection.subject_reference_url:
            try:
                planning_frame = str(
                    await storage.path_from_url(selection.subject_reference_url)
                )
            except Exception:
                log.warning("segmented subject reference unavailable", exc_info=True)
        if not planning_frame:
            try:
                frame_path, _ = storage.new_path("keyframes", "jpg")
                source_path = await materialize_edit_source(project, edit_source)
                await ffmpeg.extract_frame(source_path, selection.frame_ts, frame_path)
                crop_path, _ = storage.new_path("keyframes", "png")
                await ffmpeg.crop_bbox_from_frame(
                    frame_path,
                    selection.bbox_json,
                    crop_path,
                )
                planning_frame = str(crop_path)
            except Exception:
                log.warning("semantic planning frame unavailable", exc_info=True)
        try:
            interpretation = SemanticEditPlan.model_validate(
                await ai.gemini.interpret_edit(
                    body.prompt,
                    selection.bbox_json,
                    planning_frame,
                )
            )
            if interpretation.decision.selection_match == "mismatch":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "selection_target_mismatch",
                        "message": (
                            interpretation.decision.selection_match_reason
                            or "the selected object does not match the requested target"
                        ),
                        "selected_target": interpretation.decision.target_description,
                    },
                )
            structured_intent = interpretation.as_intent(body.prompt)
        except HTTPException:
            raise
        except Exception as exc:
            log.warning("semantic edit interpretation failed; using safe rules", exc_info=True)
            semantic_warning = (
                "semantic prompt interpretation unavailable; used conservative temporal rules "
                f"({type(exc).__name__})"
            )

    if structured_intent is not None:
        updates: dict[str, str] = {}
        if explicit_core is not None:
            updates = {"scope": "explicit_range", "duration_policy": "explicit_range"}
        elif body.requested_scope is not None:
            updates["scope"] = body.requested_scope
            if body.requested_scope in {"all_occurrences", "selected_occurrences"}:
                updates["duration_policy"] = "all_occurrences"
        if updates:
            structured_intent = structured_intent.model_copy(update=updates)

    gate = plan_prompt_intent(
        body.prompt,
        explicit_range=explicit_core is not None,
        requested_scope=body.requested_scope,
        structured_intent=structured_intent,
    )
    if gate.intent.scope in {"all_occurrences", "selected_occurrences"}:
        raise HTTPException(
            status_code=422,
            detail="occurrence discovery is not available in the local planning rollout",
        )

    range_source_path = None
    if selection.source_revision != project.video_url:
        try:
            range_source_path = await materialize_edit_source(project, edit_source)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="selected follow-up video is unavailable; draw the selection again",
            ) from exc

    try:
        resolution = await resolve_local_range(
            project,
            selection,
            gate.intent,
            explicit_core=explicit_core,
            source_start=source_start,
            source_end=source_end,
            source_path=range_source_path,
            source_duration=edit_source.duration,
        )
    except EditWindowLimitError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "edit_window_too_long",
                "message": str(exc),
                "limit_seconds": exc.limit,
                "detected_seconds": round(exc.duration, 3),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if resolution.edit_core.duration > 30.05:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "edit_window_too_long",
                "message": "this edit covers more than 30 seconds; choose a shorter range",
                "limit_seconds": 30.0,
                "detected_seconds": round(resolution.edit_core.duration, 3),
            },
        )
    context = resolution.generation_context
    source_edit_request = body.video_gen_provider in {"auto", "wan", "happyhorse"}
    planned_chunks = []
    if source_edit_request:
        try:
            planned_chunks = plan_occurrence_chunks(
                occurrence_start=resolution.edit_core.start_ts,
                occurrence_end=resolution.edit_core.end_ts,
                project_duration=edit_source.duration,
                requested_provider=body.video_gen_provider,
                split_evidence=split_evidence_from_track_frames(
                    resolution.tracked_frames
                ),
                source_start=context.start_ts,
                source_end=context.end_ts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        provider = planned_chunks[0].provider
    else:
        provider = select_video_provider(
            body.video_gen_provider,
            source_video=True,
            duration=context.duration,
        )
        provider_error = validate_provider_duration(provider, context.duration)
        if provider_error:
            raise HTTPException(status_code=422, detail=provider_error)
    # The chunk compositor currently anchors outer seams against the original
    # upload. Never let it silently replace a follow-up edit with old footage.
    if len(planned_chunks) > 1 and selection.source_revision != project.video_url:
        raise HTTPException(
            status_code=422,
            detail="follow-up edits must fit one 15-second render window",
        )
    capabilities = VIDEO_PROVIDER_CAPABILITIES[provider]
    provider_reason = (
        f"selected {provider} for source-video context"
        if capabilities.source_video_edit
        else f"selected {provider} image conditioning; source-video context is unsupported"
    )
    warnings = list(resolution.warnings)
    if semantic_warning:
        warnings.append(semantic_warning)
    if not get_settings().adaptive_edit_planning:
        warnings.append(
            "adaptive generation rollout is disabled; render will use the legacy fixed window"
        )
    if not capabilities.source_video_edit:
        warnings.append(f"{provider} cannot consume source-video motion context")
    if len(planned_chunks) > 1:
        warnings.append(
            f"edit will render as {len(planned_chunks)} overlapping clips and join at validated seams"
        )

    expected_calls = len(planned_chunks) if planned_chunks else 1
    expected_seconds = (
        sum(chunk.context_duration for chunk in planned_chunks)
        if planned_chunks
        else context.duration
    )
    estimate = {
        "analysis_mode": gate.estimate.analysis_mode,
        "analysis_duration_ms": round(
            (time.perf_counter() - analysis_started) * 1000.0, 3
        ),
        "frames_inspected": resolution.frames_inspected,
        "expected_generation_calls": expected_calls,
        "expected_generated_seconds": expected_seconds,
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
        source_revision=selection.source_revision,
    )
    db.add(plan)
    await db.flush()
    chunks_to_store = planned_chunks or [
        PlannedGlobalChunk(
            index=0,
            edit_start=resolution.edit_core.start_ts,
            edit_end=resolution.edit_core.end_ts,
            context_start=context.start_ts,
            context_end=context.end_ts,
            provider=provider,
            split_reason="occurrence_end",
        )
    ]
    for chunk in chunks_to_store:
        first_chunk = chunk.index == 0
        last_chunk = chunk.index == len(chunks_to_store) - 1
        db.add(
            GenerationChunk(
                plan_id=plan.id,
                index=chunk.index,
                edit_start=chunk.edit_start,
                edit_end=chunk.edit_end,
                context_start=chunk.context_start,
                context_end=chunk.context_end,
                provider=chunk.provider,
                payload_json={
                    "selection_id": selection.id,
                    "subject_reference_url": selection.subject_reference_url,
                    "mask_url": selection.mask_url,
                    "source_revision": selection.source_revision,
                    "split_reason": chunk.split_reason,
                    "track_frames": [
                        frame
                        for frame in resolution.tracked_frames
                        if chunk.context_start
                        <= float(frame.get("timestamp") or 0.0)
                        <= chunk.context_end
                    ],
                    "boundary_contract": {
                        "protect_source_before": first_chunk,
                        "protect_source_after": last_chunk,
                        "handoff_from_previous": not first_chunk,
                        "handoff_to_next": not last_chunk,
                    },
                },
            )
        )
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
