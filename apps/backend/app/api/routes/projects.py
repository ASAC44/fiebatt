from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.deps import get_session
from app.models.entity import Entity
from app.models.project import Project
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.project import EntitySummary, ProjectOut, SegmentOut
from app.services import storage

router = APIRouter(tags=["projects"])


class MeResponse(BaseModel):
    session_id: str
    user_id: str | None
    email: str | None
    signed_in: bool


class ProjectListItem(BaseModel):
    project_id: str
    video_url: str
    duration: float
    fps: float
    width: int
    height: int
    created_at: str


@router.get("/me", response_model=MeResponse)
async def me(session: SessionModel = Depends(get_session)):
    """Return the current session + whether it's tied to a google user."""
    return MeResponse(
        session_id=session.id,
        user_id=session.user_id,
        email=session.email,
        signed_in=session.user_id is not None,
    )


@router.get("/projects", response_model=list[ProjectListItem])
async def list_projects(
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    """Every project this session owns, newest first."""
    rows = (
        await db.execute(
            select(Project)
            .where(Project.session_id == session.id)
            .order_by(Project.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    # re-sign each stored URL. what's in the DB is a presigned GET that
    # expires after settings.presign_expiry, so old library items would
    # otherwise 403 in the browser. normalize_url_like handles raw keys,
    # s3 URLs, /media URLs, and stale presigned URLs.
    return [
        ProjectListItem(
            project_id=p.id,
            video_url=storage.normalize_url_like(p.video_url, fallback=p.video_url),
            duration=p.duration,
            fps=p.fps,
            width=p.width,
            height=p.height,
            created_at=p.created_at.isoformat(),
        )
        for p in rows
    ]


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    """Delete a project and all its segments, jobs, entities, conversations."""
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")
    await db.delete(proj)
    await db.commit()
    return {"status": "deleted", "project_id": project_id}


async def _load_owned_project(
    project_id: str,
    session: SessionModel,
    db: AsyncSession,
) -> Project:
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")
    return proj


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    proj = await _load_owned_project(project_id, session, db)

    segs = (
        await db.execute(
            select(Segment)
            .where(Segment.project_id == project_id, Segment.active == True)  # noqa: E712
            .order_by(Segment.order_index, Segment.start_ts)
        )
    ).scalars().all()

    ents = (
        await db.execute(
            select(Entity)
            .where(Entity.project_id == project_id)
            .options(selectinload(Entity.appearances))
        )
    ).scalars().all()

    # URLs stored in the DB are presigned GETs that expire after
    # settings.presign_expiry. re-sign them on read so reels opened past the
    # window still play. normalize_url_like handles raw keys, stale presigned
    # URLs, /media URLs, and local paths.
    return ProjectOut(
        project_id=proj.id,
        video_url=storage.normalize_url_like(proj.video_url, fallback=proj.video_url),
        duration=proj.duration,
        fps=proj.fps,
        width=proj.width,
        height=proj.height,
        segments=[
            SegmentOut(
                id=s.id,
                start_ts=s.start_ts,
                end_ts=s.end_ts,
                source=s.source,  # type: ignore[arg-type]
                url=storage.normalize_url_like(s.url, fallback=s.url),
                variant_id=s.variant_id,
                order_index=s.order_index,
            )
            for s in segs
        ],
        entities=[
            EntitySummary(
                id=e.id,
                description=e.description,
                category=e.category,
                appearance_count=len(e.appearances),
            )
            for e in ents
        ],
    )
