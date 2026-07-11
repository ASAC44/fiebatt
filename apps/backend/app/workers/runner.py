"""In-process asyncio job runner.

For hackathon scope, we don't want the ops surface of Redis/Celery. The runner
keeps a registry of background tasks and exposes submit/shutdown. Workers
themselves open their own DB sessions (never reuse the request's) so task
lifetimes are independent of the request that spawned them.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger("iris.jobs")


class JobRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(self, job_id: str, factory: Callable[[], Awaitable[None]]) -> asyncio.Task:
        """Start a background coroutine keyed by job_id.

        `factory` is a callable returning a fresh coroutine (not a coroutine
        object) so cancel/retry paths can rebuild the task cleanly.
        """
        if job_id in self._tasks and not self._tasks[job_id].done():
            return self._tasks[job_id]

        async def _wrapped():
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s crashed", job_id)
            finally:
                self._tasks.pop(job_id, None)

        task = asyncio.create_task(_wrapped(), name=f"job:{job_id}")
        self._tasks[job_id] = task
        return task

    def status(self, job_id: str) -> str:
        t = self._tasks.get(job_id)
        if t is None:
            return "unknown"
        if t.done():
            return "done"
        return "running"

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
