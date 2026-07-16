from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.workers import generate_job

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
    if body.plan_id:
        plan = await db.get(EditPlanRecord, body.plan_id)
        if plan is None or plan.project_id != proj.id:
            raise HTTPException(status_code=404, detail="edit plan not found")
        if plan.source_revision != proj.video_url:
            raise HTTPException(status_code=409, detail="edit plan is stale for current source")

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

    length = end_ts - start_ts
    if length < MIN_SEG_LEN or length > MAX_SEG_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"segment length must be {MIN_SEG_LEN}-{MAX_SEG_LEN}s (got {length:.2f}s)",
        )
    if end_ts > proj.duration + 1e-3:
        raise HTTPException(status_code=422, detail="end_ts past project duration")
    if video_gen_provider != "auto":
        provider_error = validate_provider_duration(video_gen_provider, length)
        if provider_error:
            raise HTTPException(status_code=422, detail=provider_error)
    # bbox sanity: x+w and y+h in [0,1]
    if bbox["x"] + bbox["w"] > 1.0001 or bbox["y"] + bbox["h"] > 1.0001:
        raise HTTPException(status_code=422, detail="bbox extends outside the frame")

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
            "plan_id": plan.id if plan else None,
            "planned_context": plan.range_json if plan else None,
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    runner.submit(job.id, lambda: generate_job.run(job.id))

    return GenerateResponse(job_id=job.id)
