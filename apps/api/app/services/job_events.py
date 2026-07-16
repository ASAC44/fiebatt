"""In-process pub/sub for per-job SSE log streams.

The generate worker publishes structured "thought process" events as it
moves through its stages (plan, generate, score, ...). The SSE route
in app.api.routes.jobs subscribes to these events and relays them to the
browser as text/event-stream.

Kept deliberately simple: a dict of job_id -> bounded history + a set of
asyncio.Queue subscribers. Survives only in the worker process, so if the
frontend re-opens the stream mid-run it replays whatever is still in
history, then blocks on new events.

Events are plain JSON-serialisable dicts. Convention used by the worker:
    { "ts": <unix seconds>, "stage": "plan_done", "msg": "...", "data": {...} }

Terminal events should set ``terminal=True`` so subscribers know to close.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

log = logging.getLogger("fiebatt.jobs.events")

# cap replay history so long-running jobs don't balloon memory.
_MAX_HISTORY = 500
# drop stale job state this long after the last event.
_GC_AFTER_SEC = 30 * 60


@dataclass
class _JobStream:
    history: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=_MAX_HISTORY)
    )
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)
    terminated: bool = False
    last_event_at: float = field(default_factory=time.time)


_streams: dict[str, _JobStream] = {}
_lock = asyncio.Lock()


def _gc_locked() -> None:
    now = time.time()
    stale = [
        jid
        for jid, s in _streams.items()
        if s.terminated
        and not s.subscribers
        and now - s.last_event_at > _GC_AFTER_SEC
    ]
    for jid in stale:
        _streams.pop(jid, None)


async def publish(job_id: str, event: dict[str, Any]) -> None:
    """Record an event for job_id and broadcast to active subscribers."""
    if not job_id:
        return
    event = {"ts": event.get("ts", time.time()), **event}
    async with _lock:
        stream = _streams.setdefault(job_id, _JobStream())
        stream.history.append(event)
        stream.last_event_at = event["ts"]
        if event.get("terminal"):
            stream.terminated = True
        subscribers = list(stream.subscribers)
        _gc_locked()

    for q in subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("event queue full for job %s — dropping event", job_id)

    if event.get("terminal"):
        # wake any subscribers still waiting so they can close the stream.
        async with _lock:
            for q in list(stream.subscribers):
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass


def publish_sync(job_id: str, event: dict[str, Any]) -> None:
    """Fire-and-forget publish from sync code. Schedules on the running loop."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    loop.create_task(publish(job_id, event))


async def subscribe(job_id: str) -> AsyncIterator[dict[str, Any]]:
    """Yield historical events, then new ones until a terminal event arrives.

    If the job already terminated, the iterator drains history and exits
    without blocking — so late subscribers still get the full story.
    """
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
    async with _lock:
        stream = _streams.setdefault(job_id, _JobStream())
        history_snapshot = list(stream.history)
        already_terminated = stream.terminated
        stream.subscribers.add(q)

    try:
        for ev in history_snapshot:
            yield ev

        if already_terminated:
            # nothing new will ever land — let the client close.
            return

        while True:
            ev = await q.get()
            if ev is None:
                return
            yield ev
            if ev.get("terminal"):
                return
    finally:
        async with _lock:
            stream = _streams.get(job_id)
            if stream is not None:
                stream.subscribers.discard(q)
                _gc_locked()


def snapshot(job_id: str) -> list[dict[str, Any]]:
    """Return the current history for a job (used by non-streaming clients)."""
    stream = _streams.get(job_id)
    if stream is None:
        return []
    return list(stream.history)
