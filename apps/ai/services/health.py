"""Health + observability endpoints for AI services.

Mount this router in the backend:
  app.include_router(ai_health_router, prefix="/api/ai")

Provides:
  GET /api/ai/health     — full stats dashboard
  GET /api/ai/timeline   — last N calls
  GET /api/ai/stream     — SSE real-time event stream
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ai.services.logger import get_stats, get_timeline, subscribe, unsubscribe
from ai.services.sam import is_available as gpu_available

router = APIRouter(tags=["ai-observability"])


@router.get("/health")
async def ai_health():
    """Full observability dashboard for AI services."""
    stats = get_stats()
    stats["gpu_worker_available"] = await gpu_available()
    return stats


@router.get("/timeline")
async def ai_timeline(last_n: int = 50):
    """Last N AI service calls with timing and status."""
    return {"events": get_timeline(last_n)}


@router.get("/stream")
async def ai_stream():
    """SSE stream of real-time AI service events.

    Each event is a JSON object with: ts, service, operation, duration_s,
    status, cost, total_cost, call_num.

    Connect from the admin dashboard with:
      const es = new EventSource('/api/ai/stream');
      es.onmessage = (e) => handleEvent(JSON.parse(e.data));
    """
    q = subscribe()

    async def event_generator():
        try:
            # send initial stats snapshot
            stats = get_stats()
            yield f"event: init\ndata: {json.dumps(stats)}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # keepalive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
