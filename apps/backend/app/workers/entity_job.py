"""Entity search worker.

Runs after /api/accept. Extracts keyframes from the project video, asks Gemini
to identify the entity from the reference crop, and finds other occurrences.
Results persist as EntityAppearance rows tied to a single Entity.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from ai import services as ai
from app.db.session import AsyncSessionLocal
from app.models.entity import Entity, EntityAppearance
from app.models.job import Job
from app.models.project import Project
from app.models.segment import Segment
from app.services import ffmpeg, storage

log = logging.getLogger("fiebatt.jobs.entity")

KEYFRAMES_PER_SECOND = 1.0


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and a_end > b_start


async def run(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        payload = job.payload or {}
        project_id = job.project_id
        segment_id = payload.get("segment_id")
        reference_frame_ts = payload.get("reference_frame_ts")
        reference_variant_url = payload.get("reference_variant_url")
        bbox = payload.get("bbox") or None

        proj = await db.get(Project, project_id)
        if proj is None:
            job.status = "error"
            job.error = "project missing"
            await db.commit()
            return
        source_segment = await db.get(Segment, segment_id) if segment_id else None
        job.status = "processing"
        source_video_path = proj.video_path
        source_video_url = proj.video_url
        source_segment_start = source_segment.start_ts if source_segment else None
        source_segment_end = source_segment.end_ts if source_segment else None
        await db.commit()

    # pull the reference frame crop now (was previously done synchronously
    # inside /api/accept; moved here so the HTTP response is instant).
    reference_crop_path: str | None = None
    if reference_frame_ts is not None:
        try:
            src = Path(source_video_path)
            if not src.exists():
                src = await storage.path_from_url(source_video_url)
            frame_path, _ = storage.new_path("keyframes", "jpg")
            await ffmpeg.extract_frame(src, float(reference_frame_ts), frame_path)
            await storage.publish(frame_path, content_type="image/jpeg")
            if bbox:
                crop_path = await ffmpeg.crop_bbox_from_frame(frame_path, bbox)
                await storage.publish(crop_path, content_type="image/png")
                reference_crop_path = str(crop_path)
            else:
                reference_crop_path = str(frame_path)
        except Exception:
            log.exception("reference frame extraction failed; continuing")

    try:
        identity = await ai.gemini.identify_entity(reference_crop_path or "")
    except Exception as e:
        log.exception("identify_entity failed")
        async with AsyncSessionLocal() as db:
            j = await db.get(Job, job_id)
            if j:
                j.status = "error"
                j.error = f"identify failed: {e}"
                await db.commit()
        return

    # create the Entity row
    async with AsyncSessionLocal() as db:
        entity = Entity(
            project_id=project_id,
            source_segment_id=segment_id,
            description=identity["description"],
            category=identity.get("category"),
            attributes_json=identity.get("attributes") or {},
            reference_crop_url=storage.url_for_path(Path(reference_crop_path))
            if reference_crop_path and Path(reference_crop_path).exists()
            else None,
            reference_variant_url=reference_variant_url,
        )
        db.add(entity)
        await db.flush()
        entity_id = entity.id

        # stash the entity_id on the job.payload so the accept route can surface it
        j = await db.get(Job, job_id)
        if j:
            payload2 = dict(j.payload or {})
            payload2["entity_id"] = entity_id
            j.payload = payload2
        await db.commit()

    # sample keyframes (cached by project — avoids re-extraction on re-edit)
    try:
        from app.config.settings import get_settings as _get_bs

        cache_dir = Path(_get_bs().storage_path) / "keyframes" / proj.id
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_pattern = cache_dir / "frame_%04d.jpg"
        existing = sorted(cache_dir.glob("frame_*.jpg"))
        if existing:
            keyframes = existing
            log.info("reusing %d cached keyframes for project %s", len(existing), proj.id)
        else:
            keyframes = await ffmpeg.extract_keyframes(
                proj.video_path, KEYFRAMES_PER_SECOND, cached_pattern
            )
        keyframe_paths: list[str] = []
        keyframe_url_by_path: dict[str, str] = {}
        for p in keyframes:
            url = await storage.publish(p, content_type="image/jpeg")
            path_key = str(p)
            keyframe_paths.append(path_key)
            keyframe_url_by_path[path_key] = url
    except Exception as e:
        log.exception("keyframe extraction failed")
        async with AsyncSessionLocal() as db:
            j = await db.get(Job, job_id)
            if j:
                j.status = "error"
                j.error = f"keyframes failed: {e}"
                await db.commit()
        return

    try:
        # The VLM adapter reads image bytes from disk.  Published URLs are for
        # the browser/UI only; passing presigned https URLs here makes the
        # adapter try to open `https:/...` as a local file.
        hits = await ai.gemini.find_entity_in_keyframes(identity, keyframe_paths)
    except Exception as e:
        log.exception("find_entity_in_keyframes failed")
        async with AsyncSessionLocal() as db:
            j = await db.get(Job, job_id)
            if j:
                j.status = "error"
                j.error = f"search failed: {e}"
                await db.commit()
        return

    async with AsyncSessionLocal() as db:
        for h in hits:
            if (
                source_segment_start is not None
                and source_segment_end is not None
                and _overlaps(
                    float(h["start_ts"]),
                    float(h["end_ts"]),
                    source_segment_start,
                    source_segment_end,
                )
            ):
                continue
            app = EntityAppearance(
                entity_id=entity_id,
                segment_id=None,
                start_ts=h["start_ts"],
                end_ts=h["end_ts"],
                keyframe_url=keyframe_url_by_path.get(
                    str(h.get("keyframe_url") or ""),
                    h.get("keyframe_url"),
                ),
                confidence=h.get("confidence", 0.0),
            )
            db.add(app)

        j = await db.get(Job, job_id)
        if j:
            j.status = "done"
        await db.commit()
