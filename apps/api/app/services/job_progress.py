"""Persist the latest generation stage so progress survives reconnects."""
from __future__ import annotations

import logging
import time
from typing import Any, Literal

from app.db.session import AsyncSessionLocal
from app.models.job import Job


ProgressStatus = Literal["running", "done", "failed"]
log = logging.getLogger("fiebatt.jobs.progress")


async def persist_job_progress(
    job_id: str,
    *,
    stage: str,
    message: str,
    status: ProgressStatus = "running",
    data: dict[str, Any] | None = None,
    session_factory=None,
) -> None:
    factory = session_factory or AsyncSessionLocal
    try:
        async with factory() as db:
            job = await db.get(Job, job_id)
            if job is None:
                return
            payload = dict(job.payload or {})
            payload["progress_state"] = {
                "stage": stage,
                "message": message,
                "status": status,
                "updated_at": time.time(),
                "data": data or {},
            }
            job.payload = payload
            await db.commit()
    except Exception:
        # Status telemetry improves the UI but must never abort the render it
        # describes. The normal job poll remains the fallback source of truth.
        log.warning("could not persist progress for job=%s stage=%s", job_id, stage, exc_info=True)
