from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ai import services as ai
from app.db.session import get_db
from app.deps import get_session
from app.models.job import Variant, Job
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.schemas.narrate import NarrateRequest, NarrateResponse
from app.services import storage

router = APIRouter(tags=["narrate"])

# in-memory narration cache: {variant_id -> audio_url}. Cheap and effective
# for a demo — repeated before/after reveals don't re-hit ElevenLabs.
_cache: dict[str, str] = {}


@router.post("/narrate", response_model=NarrateResponse)
async def narrate(
    body: NarrateRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    variant = await db.get(Variant, body.variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="variant not found")
    job = await db.get(Job, variant.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="variant not found")
    proj = await db.get(Project, job.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="variant not found")

    cached = _cache.get(variant.id)
    if cached:
        return NarrateResponse(audio_url=cached)

    text = body.description or variant.description or "A new variant is ready."
    try:
        audio_bytes = await ai.elevenlabs.narrate(text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"narration failed: {e}")

    _, url = await storage.write_bytes("narration", "mp3", audio_bytes)
    _cache[variant.id] = url
    return NarrateResponse(audio_url=url)
