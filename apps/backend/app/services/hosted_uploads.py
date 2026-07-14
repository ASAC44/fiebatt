from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiofiles
import jwt
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models.integration import UploadIntent
from app.models.project import Project
from app.models.segment import Segment
from app.services import ffmpeg, storage


SUPPORTED_TYPES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-m4v": ".m4v",
}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _local_upload_token(intent: UploadIntent) -> str:
    return jwt.encode(
        {
            "sub": intent.user_id,
            "upload_id": intent.id,
            "object_key": intent.object_key,
            "aud": "fiebatt-upload",
            "exp": int(_utc(intent.expires_at).timestamp()),
        },
        get_settings().auth_jwt_secret,
        algorithm="HS256",
    )


async def prepare_upload(
    db: AsyncSession,
    *,
    user_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
) -> dict:
    settings = get_settings()
    if content_type not in SUPPORTED_TYPES:
        raise ValueError("supported video types are MP4, MOV, WebM, and M4V")
    if size_bytes <= 0 or size_bytes > settings.max_upload_bytes:
        raise ValueError(f"upload size must be 1-{settings.max_upload_bytes} bytes")
    safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(filename).stem).strip("-")[:80] or "video"
    key = f"uploads/{user_id}/{uuid.uuid4().hex}-{safe_stem}{SUPPORTED_TYPES[content_type]}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.upload_intent_expiry_seconds)
    intent = UploadIntent(
        user_id=user_id,
        object_key=key,
        filename=filename[:255],
        content_type=content_type,
        size_bytes=size_bytes,
        expires_at=expires_at,
    )
    db.add(intent)
    await db.commit()
    await db.refresh(intent)
    if settings.s3_enabled:
        url = storage.presigned_upload_for_key(
            key,
            content_type=content_type,
            expires_in=settings.upload_intent_expiry_seconds,
        )
    else:
        token = _local_upload_token(intent)
        url = f"{settings.public_api_url.rstrip('/')}/uploads/{intent.id}?token={token}"
    return {
        "upload_id": intent.id,
        "method": "PUT",
        "url": url,
        "headers": {"Content-Type": content_type},
        "expires_at": expires_at.isoformat(),
        "max_size_bytes": settings.max_upload_bytes,
    }


async def receive_local_upload(
    db: AsyncSession,
    *,
    upload_id: str,
    token: str,
    request: Request,
) -> dict:
    """Receive a one-time-style signed PUT when object storage is unavailable."""
    settings = get_settings()
    if settings.s3_enabled:
        raise ValueError("use the signed object-storage URL for this upload")
    try:
        claims = jwt.decode(
            token,
            settings.auth_jwt_secret,
            algorithms=["HS256"],
            audience="fiebatt-upload",
        )
    except jwt.InvalidTokenError as exc:
        raise ValueError("invalid or expired upload token") from exc
    intent = await db.get(UploadIntent, upload_id)
    if (
        intent is None
        or intent.completed
        or claims.get("upload_id") != intent.id
        or claims.get("sub") != intent.user_id
        or claims.get("object_key") != intent.object_key
    ):
        raise ValueError("upload not found")
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != intent.content_type:
        raise ValueError("upload content type does not match the prepared upload")
    declared = request.headers.get("content-length")
    if declared and int(declared) > settings.max_upload_bytes:
        raise ValueError("upload is too large")

    path = settings.storage_path / intent.object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        async with aiofiles.open(path, "wb") as destination:
            async for chunk in request.stream():
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise ValueError("upload is too large")
                await destination.write(chunk)
        if written != intent.size_bytes:
            raise ValueError("uploaded object size does not match the prepared upload")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"upload_id": intent.id, "received_bytes": written}


async def complete_upload(db: AsyncSession, *, user_id: str, upload_id: str) -> dict:
    intent = await db.get(UploadIntent, upload_id)
    if intent is None or intent.user_id != user_id:
        raise ValueError("upload not found")
    if intent.completed:
        raise ValueError("upload has already been completed")
    if _utc(intent.expires_at) <= datetime.now(timezone.utc):
        raise ValueError("upload request expired")
    if get_settings().s3_enabled:
        metadata = await storage.object_metadata(intent.object_key)
        actual_size = int(metadata.get("ContentLength") or 0)
        local_path = await storage.download_key(intent.object_key)
    else:
        local_path = get_settings().storage_path / intent.object_key
        actual_size = local_path.stat().st_size if local_path.exists() else 0
    if actual_size <= 0 or actual_size > get_settings().max_upload_bytes:
        raise ValueError("uploaded object has an invalid size")
    if abs(actual_size - intent.size_bytes) > max(1024, int(intent.size_bytes * 0.01)):
        raise ValueError("uploaded object size does not match the prepared upload")

    try:
        info = await ffmpeg.probe(local_path)
    except ffmpeg.FfmpegError as exc:
        raise ValueError(f"unreadable video: {exc.stderr[:300]}") from exc
    if info["duration"] > get_settings().max_video_seconds:
        raise ValueError(
            f"video is {info['duration']:.1f}s; maximum is {get_settings().max_video_seconds}s"
        )

    session_id = f"user:{user_id}"
    video_url = storage.url_for_key(intent.object_key)
    project = Project(
        session_id=session_id,
        video_path=str(local_path),
        video_url=video_url,
        duration=info["duration"],
        fps=info["fps"],
        width=info["width"],
        height=info["height"],
    )
    db.add(project)
    await db.flush()
    db.add(Segment(
        project_id=project.id,
        start_ts=0.0,
        end_ts=info["duration"],
        source="original",
        url=video_url,
        order_index=0,
        active=True,
    ))
    intent.completed = True
    await db.commit()
    return {
        "project_id": project.id,
        "video_url": video_url,
        "duration": project.duration,
        "fps": project.fps,
        "width": project.width,
        "height": project.height,
    }
