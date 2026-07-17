from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.services.provider_capabilities import validate_provider_duration
from app.config.settings import get_settings
from app.db.session import get_db
from app.deps import get_runner, get_session
from app.models.edit_plan import EditPlanRecord
from app.models.job import Job
from app.models.project import Project
from app.models.selection import SelectionArtifact
from app.models.session import Session as SessionModel
from app.schemas.edit_plan import LocalRangeResolution
from app.schemas.generate import GenerateRequest, GenerateResponse
from app.services.accepted_generation import resolve_committed_timeline_range
from app.services.edit_source import source_for_selection
from app.workers import generate_job, local_chunk_job

router = APIRouter(tags=["generate"])


MIN_SEG_LEN = 2.0
MAX_SEG_LEN = 15.0


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
    runner = Depends(get_runner),
):
    proj = await db.get(Project, body.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")

    plan: EditPlanRecord | None = None
    resolution: LocalRangeResolution | None = None
    if body.plan_id:
        plan = (
            await db.execute(
                select(EditPlanRecord)
                .where(EditPlanRecord.id == body.plan_id)
                .options(selectinload(EditPlanRecord.chunks))
            )
        ).scalar_one_or_none()
        if plan is None or plan.project_id != proj.id:
            raise HTTPException(status_code=404, detail="edit plan not found")
        selection = await db.get(SelectionArtifact, plan.selection_id)
        if selection is None or selection.project_id != proj.id:
            raise HTTPException(status_code=422, detail="edit plan selection is unavailable")
        try:
            edit_source = source_for_selection(proj, selection)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    use_plan_range = plan is not None and get_settings().adaptive_edit_planning
    if use_plan_range:
        assert plan is not None
        selection = await db.get(SelectionArtifact, plan.selection_id)
        if selection is None or selection.project_id != proj.id:
            raise HTTPException(status_code=422, detail="edit plan selection is unavailable")
        resolution = LocalRangeResolution.model_validate(plan.range_json)
        start_ts = resolution.edit_core.start_ts
        end_ts = resolution.edit_core.end_ts
        bbox = selection.bbox_json
        prompt = plan.raw_prompt
        reference_frame_ts = selection.frame_ts
        video_gen_provider = plan.provider
    else:
        legacy = (
            body.start_ts,
            body.end_ts,
            body.bbox,
            body.prompt,
            body.reference_frame_ts,
        )
        if any(value is None for value in legacy):
            raise HTTPException(
                status_code=422,
                detail="legacy generation fields are required while adaptive planning is disabled",
            )
        start_ts = float(body.start_ts)
        end_ts = float(body.end_ts)
        assert body.bbox is not None
        bbox = body.bbox.model_dump()
        prompt = str(body.prompt)
        reference_frame_ts = float(body.reference_frame_ts)
        video_gen_provider = body.video_gen_provider or "auto"

    chunked_plan = bool(plan is not None and len(plan.chunks) > 1)
    length = end_ts - start_ts
    if length > 30.05:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "edit_window_too_long",
                "message": "this edit covers more than 30 seconds; choose a shorter range",
                "limit_seconds": 30.0,
                "detected_seconds": round(length, 3),
            },
        )
    if length < MIN_SEG_LEN or (length > MAX_SEG_LEN and not chunked_plan):
        raise HTTPException(
            status_code=422,
            detail=(
                f"segment length must be {MIN_SEG_LEN:g}-{MAX_SEG_LEN:g}s "
                f"(got {length:.2f}s)"
            ),
        )
    source_duration = edit_source.duration if plan is not None else proj.duration
    if end_ts > source_duration + 1e-3:
        raise HTTPException(status_code=422, detail="end_ts past selected clip duration")
    generation_length = (
        resolution.generation_context.duration
        if use_plan_range and resolution is not None
        else length
    )
    if generation_length > MAX_SEG_LEN and not chunked_plan:
        raise HTTPException(
            status_code=422,
            detail=(
                "generation context must be at most "
                f"{MAX_SEG_LEN:g}s (got {generation_length:.2f}s)"
            ),
        )
    if chunked_plan:
        for chunk in plan.chunks:
            chunk_duration = chunk.context_end - chunk.context_start
            provider_error = validate_provider_duration(chunk.provider, chunk_duration)
            if provider_error:
                raise HTTPException(status_code=422, detail=provider_error)
    elif video_gen_provider != "auto":
        provider_error = validate_provider_duration(video_gen_provider, generation_length)
        if provider_error:
            raise HTTPException(status_code=422, detail=provider_error)
    # bbox sanity: x+w and y+h in [0,1]
    if bbox["x"] + bbox["w"] > 1.0001 or bbox["y"] + bbox["h"] > 1.0001:
        raise HTTPException(status_code=422, detail="bbox extends outside the frame")
    try:
        committed_start, committed_end = resolve_committed_timeline_range(
            proj.timeline_edl,
            target_clip_id=body.target_clip_id,
            source_start=start_ts,
            source_end=end_ts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job = Job(
        project_id=proj.id,
        kind="generate",
        status="pending",
        start_ts=start_ts,
        end_ts=end_ts,
        bbox_json=bbox,
        prompt=prompt,
        reference_frame_ts=reference_frame_ts,
        payload={
            "video_gen_provider": video_gen_provider,
            "user_prompt": body.user_prompt or prompt,
            "plan_id": plan.id if plan else None,
            "planned_context": plan.range_json if plan else None,
            "planned_intent": plan.intent_json if plan else None,
            "adaptive_context_enabled": use_plan_range,
            "target_clip_id": body.target_clip_id,
            "source_revision": plan.source_revision if plan else proj.video_url,
            "plan_scope": plan.scope if plan else "legacy",
            "analysis_duration_ms": (
                (plan.estimate_json or {}).get("analysis_duration_ms", 0.0)
                if plan
                else 0.0
            ),
            "analysis_frames": (
                (plan.estimate_json or {}).get("frames_inspected", 0)
                if plan
                else 0
            ),
            "chunk_count": len(plan.chunks) if plan else 1,
            "fixed_window_baseline_seconds": (
                float(body.end_ts) - float(body.start_ts)
                if body.start_ts is not None and body.end_ts is not None
                else (float(proj.duration) if proj.duration <= 5.0 else 3.0)
            ),
            "committed_timeline_range": {
                "start": committed_start,
                "end": committed_end,
            },
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    worker = local_chunk_job.run if chunked_plan else generate_job.run
    runner.submit(job.id, lambda: worker(job.id))

    return GenerateResponse(job_id=job.id)
