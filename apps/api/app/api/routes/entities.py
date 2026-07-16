from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.deps import get_session
from app.models.entity import Entity
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.schemas.entity import AppearanceOut, EntityOut

router = APIRouter(tags=["entities"])


@router.get("/entities/{entity_id}", response_model=EntityOut)
async def get_entity(
    entity_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    entity = (
        await db.execute(
            select(Entity)
            .where(Entity.id == entity_id)
            .options(selectinload(Entity.appearances))
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")

    proj = await db.get(Project, entity.project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="entity not found")

    return EntityOut(
        entity_id=entity.id,
        description=entity.description,
        category=entity.category,
        reference_crop_url=entity.reference_crop_url,
        appearances=[
            AppearanceOut(
                id=a.id,
                segment_id=a.segment_id,
                start_ts=a.start_ts,
                end_ts=a.end_ts,
                keyframe_url=a.keyframe_url,
                confidence=a.confidence,
            )
            for a in entity.appearances
        ],
    )
