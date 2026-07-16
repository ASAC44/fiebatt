from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.settings import get_settings
from app.db.session import get_db
from app.deps import get_session
from app.models.entity import Entity, EntityAppearance
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.propagation import (
    GlobalEditPlan,
    GlobalGenerationChunk,
    GlobalOccurrencePlan,
    PropagationResult,
)
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.propagate import (
    GlobalEditApplyOut,
    GlobalEditPlanOut,
    GlobalEditPlanRequest,
    PlannedChunkOut,
    PlannedOccurrenceOut,
)
from app.schemas.timeline import PersistedEDL
from app.services.accepted_generation import splice_generated_clip_into_edl
from app.services.global_chunk_planner import (
    plan_occurrence_chunks,
    split_evidence_from_track_frames,
)
from app.services.global_timeline import (
    ensure_source_aligned_timeline,
    timeline_revision,
)
from app.services.timeline_response import build_timeline_response


router = APIRouter(prefix="/global-edit-plans", tags=["global-edit-plans"])


async def _plan_response(
    db: AsyncSession,
    plan: GlobalEditPlan,
) -> GlobalEditPlanOut:
    plan = (
        await db.execute(
            select(GlobalEditPlan)
            .where(GlobalEditPlan.id == plan.id)
            .options(
                selectinload(GlobalEditPlan.occurrence_plans).selectinload(
                    GlobalOccurrencePlan.chunks
                )
            )
        )
    ).scalar_one()
    appearances = (
        await db.execute(
            select(EntityAppearance)
            .where(EntityAppearance.id.in_(plan.occurrence_ids_json))
            .order_by(EntityAppearance.start_ts)
        )
    ).scalars().all()
    appearance_by_id = {appearance.id: appearance for appearance in appearances}
    return GlobalEditPlanOut(
        plan_id=plan.id,
        project_id=plan.project_id,
        entity_id=plan.entity_id,
        reference_segment_id=plan.reference_segment_id,
        scope=plan.scope,
        requested_provider=plan.requested_provider,
        prompt=plan.prompt,
        occurrences=[
            PlannedOccurrenceOut(
                appearance_id=occurrence_plan.appearance_id,
                start_ts=appearance_by_id[occurrence_plan.appearance_id].start_ts,
                end_ts=appearance_by_id[occurrence_plan.appearance_id].end_ts,
                confidence=appearance_by_id[occurrence_plan.appearance_id].confidence,
                status=occurrence_plan.status,
                output_url=occurrence_plan.output_url,
                error=occurrence_plan.error,
                chunks=[
                    PlannedChunkOut(
                        chunk_id=chunk.id,
                        index=chunk.index,
                        edit_start=chunk.edit_start,
                        edit_end=chunk.edit_end,
                        context_start=chunk.context_start,
                        context_end=chunk.context_end,
                        provider=chunk.provider,
                        split_reason=chunk.split_reason,
                        status=chunk.status,
                        attempts=chunk.attempts,
                        output_url=chunk.output_url,
                        error=chunk.error,
                    )
                    for chunk in occurrence_plan.chunks
                ],
            )
            for occurrence_plan in plan.occurrence_plans
        ],
        estimate=plan.estimate_json,
        status=plan.status,
    )


@router.post("", response_model=GlobalEditPlanOut, status_code=201)
async def create_global_edit_plan(
    body: GlobalEditPlanRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    if not get_settings().global_edit_planning:
        raise HTTPException(status_code=409, detail="global edit planning is disabled")
    entity = (
        await db.execute(
            select(Entity)
            .where(Entity.id == body.entity_id)
            .options(
                selectinload(Entity.appearances),
                selectinload(Entity.occurrence_tracks),
            )
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")
    project = await db.get(Project, entity.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="entity not found")
    try:
        await ensure_source_aligned_timeline(db, project)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    segment = await db.get(Segment, body.reference_segment_id)
    if (
        segment is None
        or segment.project_id != project.id
        or segment.source != "generated"
        or not segment.active
        or not segment.variant_id
        or entity.source_segment_id != segment.id
    ):
        raise HTTPException(status_code=422, detail="accepted reference segment is invalid")
    variant = await db.get(Variant, segment.variant_id)
    source_job = await db.get(Job, variant.job_id) if variant is not None else None
    if variant is None or not variant.url or source_job is None or not source_job.prompt:
        raise HTTPException(status_code=422, detail="accepted reference variant is unavailable")
    current_tracks = [
        track
        for track in entity.occurrence_tracks
        if track.status == "confirmed" and track.source_revision == project.video_url
    ]
    if not current_tracks:
        raise HTTPException(status_code=422, detail="entity has no confirmed occurrence tracks")

    appearance_by_id = {appearance.id: appearance for appearance in entity.appearances}
    if body.scope == "selected_occurrences":
        requested_ids = list(dict.fromkeys(body.occurrence_ids))
        if not requested_ids:
            raise HTTPException(status_code=422, detail="select at least one occurrence")
        if any(appearance_id not in appearance_by_id for appearance_id in requested_ids):
            raise HTTPException(status_code=422, detail="selected occurrence does not belong to entity")
        selected = [appearance_by_id[appearance_id] for appearance_id in requested_ids]
    else:
        selected = list(entity.appearances)
    if not selected:
        raise HTTPException(status_code=422, detail="entity has no confirmed occurrences")
    settings = get_settings()
    if len(selected) > settings.global_edit_max_occurrences:
        raise HTTPException(
            status_code=422,
            detail=(
                f"select at most {settings.global_edit_max_occurrences} appearances "
                "for one global edit"
            ),
        )

    source_payload = source_job.payload or {}
    accepted_ranges = source_payload.get("accepted_ranges")
    accepted_range = (
        accepted_ranges.get(segment.id, {})
        if isinstance(accepted_ranges, dict)
        else {}
    )
    reference_media_start = float(accepted_range.get("media_start", 0.0))
    reference_media_end = float(
        accepted_range.get("media_end", segment.end_ts - segment.start_ts)
    )
    execution_window = source_payload.get("execution_window")
    context_start = (
        float(execution_window.get("context_start", segment.start_ts))
        if isinstance(execution_window, dict)
        else segment.start_ts
    )
    reference_media_timestamp = min(
        reference_media_end,
        max(
            reference_media_start,
            float(source_job.reference_frame_ts or segment.start_ts) - context_start,
        ),
    )
    plan = GlobalEditPlan(
        project_id=project.id,
        entity_id=entity.id,
        reference_segment_id=segment.id,
        reference_variant_id=variant.id,
        scope=body.scope,
        requested_provider=body.video_gen_provider,
        occurrence_ids_json=[appearance.id for appearance in selected],
        estimate_json={},
        reference_json={
            "media_start": reference_media_start,
            "media_end": reference_media_end,
            "media_timestamp": reference_media_timestamp,
            "bbox": source_job.bbox_json or {},
        },
        prompt=source_job.prompt,
        source_revision=project.video_url,
        timeline_revision=timeline_revision(project),
        status="ready",
    )
    db.add(plan)
    await db.flush()

    generation_calls = 0
    generated_seconds = 0.0
    for index, appearance in enumerate(sorted(selected, key=lambda item: item.start_ts)):
        matching_tracks = [
            track
            for track in current_tracks
            if track.end_ts > appearance.start_ts and track.start_ts < appearance.end_ts
        ]
        if not matching_tracks:
            raise HTTPException(
                status_code=422,
                detail=f"occurrence {appearance.id} has no current confirmed track",
            )
        track_frames = sorted(
            (
                frame
                for track in matching_tracks
                for frame in (track.frames_json or [])
            ),
            key=lambda frame: float(frame.get("timestamp") or 0.0),
        )
        try:
            chunks = plan_occurrence_chunks(
                occurrence_start=appearance.start_ts,
                occurrence_end=appearance.end_ts,
                project_duration=project.duration,
                requested_provider=body.video_gen_provider,
                split_evidence=split_evidence_from_track_frames(track_frames),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        occurrence_plan = GlobalOccurrencePlan(
            global_plan_id=plan.id,
            appearance_id=appearance.id,
            index=index,
            edit_start=appearance.start_ts,
            edit_end=appearance.end_ts,
            estimate_json={
                "generation_calls": len(chunks),
                "generated_seconds": round(
                    sum(chunk.context_duration for chunk in chunks), 3
                ),
            },
            status="planned",
        )
        db.add(occurrence_plan)
        await db.flush()
        for chunk in chunks:
            first_chunk = chunk.index == 0
            last_chunk = chunk.index == len(chunks) - 1
            chunk_frames = [
                frame
                for frame in track_frames
                if chunk.context_start
                <= float(frame.get("timestamp") or 0.0)
                <= chunk.context_end
            ]
            db.add(
                GlobalGenerationChunk(
                    occurrence_plan_id=occurrence_plan.id,
                    index=chunk.index,
                    edit_start=chunk.edit_start,
                    edit_end=chunk.edit_end,
                    context_start=chunk.context_start,
                    context_end=chunk.context_end,
                    provider=chunk.provider,
                    split_reason=chunk.split_reason,
                    payload_json={
                        "track_ids": [track.id for track in matching_tracks],
                        "track_frames": chunk_frames,
                        "boundary_contract": {
                            "protect_source_before": first_chunk,
                            "protect_source_after": last_chunk,
                            "handoff_from_previous": not first_chunk,
                            "handoff_to_next": not last_chunk,
                        },
                    },
                    status="planned",
                )
            )
        generation_calls += len(chunks)
        generated_seconds += sum(chunk.context_duration for chunk in chunks)

    plan.estimate_json = {
        "occurrence_count": len(selected),
        "expected_generation_calls": generation_calls,
        "expected_generated_seconds": round(generated_seconds, 3),
        "mean_track_confidence": round(
            sum(appearance.confidence for appearance in selected) / len(selected), 3
        ),
        "reference_accepted": True,
    }
    if generation_calls > settings.global_edit_max_generation_calls:
        raise HTTPException(
            status_code=422,
            detail=(
                f"plan needs {generation_calls} generation calls; limit is "
                f"{settings.global_edit_max_generation_calls}"
            ),
        )
    if generated_seconds > settings.global_edit_max_generated_seconds:
        raise HTTPException(
            status_code=422,
            detail=(
                f"plan needs {generated_seconds:.1f} generated seconds; limit is "
                f"{settings.global_edit_max_generated_seconds:.1f}"
            ),
        )
    await db.commit()
    await db.refresh(plan)
    return await _plan_response(db, plan)


@router.get("/{plan_id}", response_model=GlobalEditPlanOut)
async def get_global_edit_plan(
    plan_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(GlobalEditPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="global edit plan not found")
    project = await db.get(Project, plan.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="global edit plan not found")
    return await _plan_response(db, plan)


@router.post("/{plan_id}/apply", response_model=GlobalEditApplyOut)
async def apply_global_edit_plan(
    plan_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    plan = (
        await db.execute(
            select(GlobalEditPlan)
            .where(GlobalEditPlan.id == plan_id)
            .options(selectinload(GlobalEditPlan.occurrence_plans))
        )
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="global edit plan not found")
    project = await db.get(Project, plan.project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="global edit plan not found")
    if plan.status not in {"done", "applied"} or not plan.propagation_job_id:
        raise HTTPException(status_code=422, detail="global edit results are not ready")

    results = (
        await db.execute(
            select(PropagationResult).where(
                PropagationResult.propagation_job_id == plan.propagation_job_id
            )
        )
    ).scalars().all()
    result_by_appearance = {result.appearance_id: result for result in results}
    if len(result_by_appearance) != len(plan.occurrence_plans):
        raise HTTPException(status_code=409, detail="global edit results are incomplete")

    if plan.status != "applied":
        if timeline_revision(project) != plan.timeline_revision:
            raise HTTPException(status_code=409, detail="timeline changed after global planning")
        try:
            await ensure_source_aligned_timeline(db, project)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    edl = (
        PersistedEDL.model_validate(project.timeline_edl)
        if project.timeline_edl and plan.status != "applied"
        else None
    )
    segment_ids: list[str] = []
    for occurrence in sorted(plan.occurrence_plans, key=lambda item: item.index):
        result = result_by_appearance[occurrence.appearance_id]
        if result.status != "done" or not result.variant_url:
            raise HTTPException(status_code=422, detail="global edit result is not ready")
        if result.applied and result.segment_id:
            segment_ids.append(result.segment_id)
            continue

        overlapping = (
            await db.execute(
                select(Segment).where(
                    Segment.project_id == project.id,
                    Segment.active == True,  # noqa: E712
                    Segment.source == "generated",
                    Segment.start_ts < occurrence.edit_end,
                    Segment.end_ts > occurrence.edit_start,
                )
            )
        ).scalars().all()
        for segment in overlapping:
            segment.active = False
        segment = Segment(
            project_id=project.id,
            start_ts=occurrence.edit_start,
            end_ts=occurrence.edit_end,
            source="generated",
            url=result.variant_url,
            order_index=int(occurrence.edit_start * 1000),
            active=True,
        )
        db.add(segment)
        await db.flush()
        result.segment_id = segment.id
        result.applied = True
        occurrence.status = "applied"
        segment_ids.append(segment.id)
        if edl is not None:
            duration = occurrence.edit_end - occurrence.edit_start
            edl = splice_generated_clip_into_edl(
                edl,
                project_id=project.id,
                project_fps=project.fps,
                segment_id=segment.id,
                asset_id=result.id,
                url=result.variant_url,
                timeline_start=occurrence.edit_start,
                timeline_end=occurrence.edit_end,
                media_start=0.0,
                media_end=duration,
                media_duration=duration,
            )

    if edl is not None:
        project.timeline_edl = edl.model_dump(mode="json")
    plan.status = "applied"
    await db.commit()
    await db.refresh(project)
    return GlobalEditApplyOut(
        plan_id=plan.id,
        segment_ids=segment_ids,
        timeline=await build_timeline_response(db, project),
    )
