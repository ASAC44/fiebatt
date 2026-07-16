from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.project import Project
from app.models.segment import Segment


def is_trackable_bbox(bbox_json: dict | None) -> bool:
    bbox = bbox_json or {}
    if not isinstance(bbox, dict):
        return False
    width = float(bbox.get("w", 0.0))
    height = float(bbox.get("h", 0.0))
    return bool(bbox) and width < 0.98 and height < 0.98 and width * height > 0.0


async def enqueue_entity_discovery(
    db: AsyncSession,
    *,
    project: Project,
    segment: Segment,
    source_job: Job,
    reference_variant_url: str,
) -> tuple[Job | None, bool]:
    """Create one idempotent full-reel discovery job for an accepted segment."""
    if not is_trackable_bbox(source_job.bbox_json):
        return None, False

    existing_jobs = (
        await db.execute(
            select(Job).where(
                Job.project_id == project.id,
                Job.kind == "entity",
                Job.status.in_(("pending", "processing", "done")),
            )
        )
    ).scalars().all()
    existing = next(
        (
            job
            for job in existing_jobs
            if isinstance(job.payload, dict)
            and job.payload.get("segment_id") == segment.id
        ),
        None,
    )
    if existing is not None:
        return existing, True

    job = Job(
        project_id=project.id,
        kind="entity",
        status="pending",
        payload={
            "segment_id": segment.id,
            "reference_frame_ts": source_job.reference_frame_ts,
            "reference_variant_url": reference_variant_url,
            "bbox": source_job.bbox_json,
            "source_start_ts": source_job.start_ts,
            "source_end_ts": source_job.end_ts,
        },
    )
    db.add(job)
    await db.flush()
    return job, False
