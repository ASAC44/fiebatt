"""Structured logging + timing + observability for all AI services.

Every service call gets logged with: service name, operation, duration, success/error,
and cost estimate (API calls add up fast at a hackathon).

Also tracks latency histograms, error rates, and a full call timeline so you can
debug the entire pipeline during a live demo.
"""

import time
import json
import logging
import asyncio
import functools
from typing import Any, Callable
from pathlib import Path
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("iris.ai")

# rough cost estimates per API call (for tracking burn rate)
COST_ESTIMATES = {
    "gemini:edit_plan": 0.01,
    "gemini:identify_entity": 0.005,
    "gemini:search_keyframes": 0.005,
    "gemini:score_variant": 0.005,
    "gemini:narration_script": 0.002,
    "veo:generate_variant": 0.05,
    "happyhorse:generate_variant": 0.03,
    "elevenlabs:narrate": 0.01,
    "gpu:sam_segment": 0.0,
    "gpu:clip_embed": 0.0,
    "gpu:clip_batch": 0.0,
}

# --- state ---

_total_cost = 0.0
_call_counts: dict[str, int] = {}
_error_counts: dict[str, int] = {}
_latencies: dict[str, list[float]] = defaultdict(list)
_timeline: list[dict] = []
_session_start = time.monotonic()
_sse_subscribers: list[asyncio.Queue] = []


def _broadcast(event: dict) -> None:
    """Push an event to all SSE subscribers. Non-blocking, best-effort."""
    dead: list[asyncio.Queue] = []
    for q in _sse_subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _sse_subscribers.remove(q)


def subscribe() -> asyncio.Queue:
    """Create a new SSE subscriber queue. Caller iterates it for events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _sse_subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    """Remove an SSE subscriber."""
    if q in _sse_subscribers:
        _sse_subscribers.remove(q)


def tracked(service: str, operation: str):
    """Decorator that logs timing, errors, and estimated cost for a service call."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            global _total_cost
            key = f"{service}:{operation}"
            _call_counts[key] = _call_counts.get(key, 0) + 1
            call_num = _call_counts[key]

            logger.info(f"[{key}] starting (call #{call_num})")
            start = time.monotonic()

            try:
                result = await func(*args, **kwargs)
                elapsed = time.monotonic() - start
                cost = COST_ESTIMATES.get(key, 0.0)
                _total_cost += cost
                _latencies[key].append(elapsed)

                entry = {
                    "ts": round(time.monotonic() - _session_start, 2),
                    "service": service,
                    "operation": operation,
                    "duration_s": round(elapsed, 2),
                    "status": "ok",
                    "cost": cost,
                    "total_cost": round(_total_cost, 4),
                    "call_num": call_num,
                }
                _timeline.append(entry)
                _broadcast(entry)

                logger.info(
                    f"[{key}] done in {elapsed:.2f}s "
                    f"(est. ${cost:.3f}, total ${_total_cost:.3f})"
                )
                return result

            except Exception as e:
                elapsed = time.monotonic() - start
                _error_counts[key] = _error_counts.get(key, 0) + 1
                _latencies[key].append(elapsed)

                entry = {
                    "ts": round(time.monotonic() - _session_start, 2),
                    "service": service,
                    "operation": operation,
                    "duration_s": round(elapsed, 2),
                    "status": "error",
                    "error": str(e)[:200],
                    "total_cost": round(_total_cost, 4),
                    "call_num": call_num,
                }
                _timeline.append(entry)
                _broadcast(entry)

                logger.error(f"[{key}] failed after {elapsed:.2f}s: {e}")
                raise

        return wrapper
    return decorator


def get_stats() -> dict:
    """Return current session stats for the /health endpoint."""
    stats: dict[str, Any] = {}

    for key in sorted(set(list(_call_counts.keys()) + list(_error_counts.keys()))):
        calls = _call_counts.get(key, 0)
        errors = _error_counts.get(key, 0)
        lats = _latencies.get(key, [])

        stats[key] = {
            "calls": calls,
            "errors": errors,
            "error_rate": round(errors / calls, 3) if calls > 0 else 0,
            "avg_latency_s": round(sum(lats) / len(lats), 2) if lats else 0,
            "min_latency_s": round(min(lats), 2) if lats else 0,
            "max_latency_s": round(max(lats), 2) if lats else 0,
            "p50_latency_s": round(sorted(lats)[len(lats) // 2], 2) if lats else 0,
        }

    return {
        "uptime_s": round(time.monotonic() - _session_start, 1),
        "total_estimated_cost": round(_total_cost, 4),
        "total_calls": sum(_call_counts.values()),
        "total_errors": sum(_error_counts.values()),
        "services": stats,
    }


def get_timeline(last_n: int = 50) -> list[dict]:
    """Return the last N entries from the call timeline."""
    return _timeline[-last_n:]


def export_timeline_jsonl(path: str) -> None:
    """Dump the full timeline to a JSONL file for post-mortem analysis."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        for entry in _timeline:
            f.write(json.dumps(entry) + "\n")


def reset() -> None:
    """Reset all stats. Useful for testing."""
    global _total_cost, _session_start
    _total_cost = 0.0
    _call_counts.clear()
    _error_counts.clear()
    _latencies.clear()
    _timeline.clear()
    _session_start = time.monotonic()
