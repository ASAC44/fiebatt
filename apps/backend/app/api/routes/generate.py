from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ai.services.provider_capabilities import validate_provider_duration
from app.db.session import get_db
from app.deps import get_runner, get_session
from app.models.job import Job
from app.models.project import Project
from app.models.session import Session as SessionModel
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

    length = body.end_ts - body.start_ts
    if length < MIN_SEG_LEN or length > MAX_SEG_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"segment length must be {MIN_SEG_LEN}-{MAX_SEG_LEN}s (got {length:.2f}s)",
        )
    if body.end_ts > proj.duration + 1e-3:
        raise HTTPException(status_code=422, detail="end_ts past project duration")
    if body.video_gen_provider and body.video_gen_provider != "auto":
        provider_error = validate_provider_duration(body.video_gen_provider, length)
        if provider_error:
            raise HTTPException(status_code=422, detail=provider_error)
    # bbox sanity: x+w and y+h in [0,1]
    if body.bbox.x + body.bbox.w > 1.0001 or body.bbox.y + body.bbox.h > 1.0001:
        raise HTTPException(status_code=422, detail="bbox extends outside the frame")

    job = Job(
        project_id=proj.id,
        kind="generate",
        status="pending",
        start_ts=body.start_ts,
        end_ts=body.end_ts,
        bbox_json=body.bbox.model_dump(),
        prompt=body.prompt,
        reference_frame_ts=body.reference_frame_ts,
        payload={"video_gen_provider": body.video_gen_provider or "auto"},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    runner.submit(job.id, lambda: generate_job.run(job.id))

    return GenerateResponse(job_id=job.id)
