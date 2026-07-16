"""Propagation worker.

Given a PropagationJob, fan out 1 Runway call per EntityAppearance using the
accepted source variant as style reference. Writes PropagationResult rows as
each fan-out task lands. If auto_apply is true, also stitch + insert Segments.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from app.ai import services as ai
from app.db.session import AsyncSessionLocal
from app.models.entity import Entity, EntityAppearance
from app.models.project import Project
from app.models.propagation import PropagationJob, PropagationResult
from app.models.segment import Segment
from app.services import ffmpeg, storage

log = logging.getLogger("fiebatt.jobs.propagate")


async def _run_one(
    result_id: str,
    project_id: str,
    appearance_id: str,
    source_variant_url: str,
    prompt: str,
    auto_apply: bool,
) -> None:
    async with AsyncSessionLocal() as db:
        res = await db.get(PropagationResult, result_id)
        if res is None:
            return
        res.status = "processing"
        await db.commit()

        appearance = await db.get(EntityAppearance, appearance_id)
        proj = await db.get(Project, project_id)
        if appearance is None or proj is None:
            res.status = "error"
            res.error = "missing appearance or project"
            await db.commit()
            return
        start_ts = appearance.start_ts
        end_ts = appearance.end_ts

    # extract the clip for this appearance
    clip_path, _ = storage.new_path("clips", "mp4")
    try:
        await ffmpeg.extract_clip(proj.video_path, start_ts, end_ts, clip_path)
    except Exception as e:
        log.exception("propagate extract failed")
        async with AsyncSessionLocal() as db:
            res = await db.get(PropagationResult, result_id)
            if res:
                res.status = "error"
                res.error = f"extract failed: {e}"
                await db.commit()
        return
    clip_url = await storage.publish(clip_path, content_type="video/mp4")

    plan = {
        "description": f"propagate: {prompt}",
        "tone": "",
        "color_grading": "match source",
        "region_emphasis": "match reference",
        "prompt_for_runway": prompt,
    }

    try:
        variant = await ai.runway.generate(
            str(clip_path), plan, style_ref=source_variant_url, resolution="480P"
        )
    except Exception as e:
        log.exception("propagate runway failed")
        async with AsyncSessionLocal() as db:
            res = await db.get(PropagationResult, result_id)
            if res:
                res.status = "error"
                res.error = f"generate failed: {e}"
                await db.commit()
        return

    raw_url = variant.get("url") or ""
    if raw_url in (str(clip_path), Path(clip_path).as_posix()):
        variant_url = clip_url
    else:
        variant_url = storage.normalize_url_like(raw_url, fallback=clip_url)
    segment_id: str | None = None

    if auto_apply:
        try:
            variant_path = await storage.path_from_url(variant_url)
            normalized, _ = storage.new_path("variants", "mp4")
            await ffmpeg.normalize_fps(variant_path, proj.fps, normalized)
            normalized_url = await storage.publish(normalized, content_type="video/mp4")

            async with AsyncSessionLocal() as db:
                live_proj = await db.get(Project, project_id)
                overlapping = (
                    await db.execute(
                        select(Segment).where(
                            Segment.project_id == project_id,
                            Segment.active == True,  # noqa: E712
                            Segment.source == "generated",
                            Segment.start_ts < end_ts,
                            Segment.end_ts > start_ts,
                        )
                    )
                ).scalars().all()
                for seg in overlapping:
                    seg.active = False
                stitched_path, _ = storage.new_path("stitched", "mp4")
                # Keep propagated edits frame-accurate as well. Blending the
                # AI and source frames creates the same ghosted box artifact
                # as the export path's old dissolve.
                await ffmpeg.simple_replace(
                    base=live_proj.video_path,
                    replacement=normalized,
                    at_ts=start_ts,
                    duration=end_ts - start_ts,
                    out=stitched_path,
                )
                stitched_url = await storage.publish(stitched_path, content_type="video/mp4")
                live_proj.video_url = stitched_url

                seg = Segment(
                    project_id=project_id,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    source="generated",
                    url=normalized_url,
                    order_index=int(start_ts * 1000),
                    active=True,
                )
                db.add(seg)
                await db.flush()
                segment_id = seg.id
                await db.commit()
        except Exception as e:
            log.exception("auto-apply stitch failed")
            async with AsyncSessionLocal() as db:
                res = await db.get(PropagationResult, result_id)
                if res:
                    res.status = "error"
                    res.error = f"apply failed: {e}"
                    await db.commit()
            return

    async with AsyncSessionLocal() as db:
        res = await db.get(PropagationResult, result_id)
        if res:
            res.status = "done"
            res.variant_url = variant_url
            res.applied = auto_apply and segment_id is not None
            res.segment_id = segment_id
            await db.commit()


async def run(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        pjob = await db.get(PropagationJob, job_id)
        if pjob is None:
            return
        pjob.status = "processing"
        await db.commit()

        results = (
            await db.execute(
                select(PropagationResult).where(
                    PropagationResult.propagation_job_id == job_id
                )
            )
        ).scalars().all()

        project_id = pjob.project_id
        source_variant_url = pjob.source_variant_url
        prompt = pjob.prompt
        auto_apply = pjob.auto_apply

    await asyncio.gather(
        *[
            _run_one(
                result_id=r.id,
                project_id=project_id,
                appearance_id=r.appearance_id,
                source_variant_url=source_variant_url,
                prompt=prompt,
                auto_apply=auto_apply,
            )
            for r in results
        ],
        return_exceptions=True,
    )

    async with AsyncSessionLocal() as db:
        pjob = await db.get(PropagationJob, job_id)
        if pjob is None:
            return
        final = (
            await db.execute(
                select(PropagationResult).where(
                    PropagationResult.propagation_job_id == job_id
                )
            )
        ).scalars().all()
        errored = [r for r in final if r.status == "error"]
        if len(errored) == len(final):
            pjob.status = "error"
            pjob.error = "all propagations failed"
        else:
            pjob.status = "done"
        await db.commit()
