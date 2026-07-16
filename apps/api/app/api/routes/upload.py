from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.session import get_db
from app.deps import get_session
from app.models.project import Project
from app.models.segment import Segment
from app.models.session import Session as SessionModel
from app.schemas.upload import UploadResponse
from app.services import ffmpeg, storage

router = APIRouter(tags=["upload"])


@router.post("/upload", response_model=UploadResponse)
async def upload(
    file: UploadFile = File(...),
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    path, url = await storage.save_upload(file, category="uploads")

    try:
        info = await ffmpeg.probe(path)
    except ffmpeg.FfmpegError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"unreadable video: {e.stderr[:500]}")

    if info["duration"] > settings.max_video_seconds:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"video too long ({info['duration']:.1f}s > {settings.max_video_seconds}s max)",
        )

    project = Project(
        session_id=session.id,
        name=(Path(file.filename or "").stem.strip() or "Untitled video")[:120],
        video_path=str(path),
        video_url=url,
        duration=info["duration"],
        fps=info["fps"],
        width=info["width"],
        height=info["height"],
    )
    db.add(project)
    await db.flush()

    segment = Segment(
        project_id=project.id,
        start_ts=0.0,
        end_ts=info["duration"],
        source="original",
        url=url,
        order_index=0,
        active=True,
    )
    db.add(segment)
    await db.commit()
    await db.refresh(project)

    return UploadResponse(
        project_id=project.id,
        video_url=project.video_url,
        duration=project.duration,
        fps=project.fps,
        width=project.width,
        height=project.height,
    )
