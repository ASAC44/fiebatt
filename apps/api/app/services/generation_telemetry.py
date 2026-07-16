"""Stable, JSON-safe telemetry for comparing adaptive and legacy local edits."""
from __future__ import annotations

from typing import Any

from app.services.continuity_validator import ContinuityReport
from app.services.generation_window import GenerationWindow


def build_local_flow_telemetry(
    *,
    payload: dict[str, Any],
    window: GenerationWindow,
    continuity: ContinuityReport | None,
    quality_state: str,
    attempts: int,
    generated_seconds: float,
    provider_attempts: list[str],
    selected_provider: str,
) -> dict[str, Any]:
    """Summarize cost, latency, routing, and seam outcome without media data."""
    baseline_seconds = float(
        payload.get("fixed_window_baseline_seconds") or window.core_duration
    )
    composites = payload.get("localized_compositing")
    latest_composite = composites[-1] if isinstance(composites, list) and composites else {}
    seam_metrics = continuity.metrics if continuity is not None else {}
    return {
        "schema_version": 1,
        "flow_mode": "adaptive_planned" if window.adaptive else "legacy_fixed_window",
        "scope": payload.get("plan_scope") or ("local" if window.adaptive else "legacy"),
        "analysis_duration_ms": float(payload.get("analysis_duration_ms") or 0.0),
        "analysis_frames": int(payload.get("analysis_frames") or 0),
        "core_seconds": round(window.core_duration, 3),
        "context_seconds": round(window.context_duration, 3),
        "context_overhead_seconds": round(
            window.context_duration - window.core_duration, 3
        ),
        "fixed_window_baseline_seconds": round(baseline_seconds, 3),
        "generated_seconds": round(generated_seconds, 3),
        "generated_over_baseline_seconds": round(
            generated_seconds - baseline_seconds, 3
        ),
        "provider": selected_provider,
        "provider_attempts": list(provider_attempts),
        "retries": max(0, attempts - 1),
        "quality_state": quality_state,
        "continuity_passed": continuity.passed if continuity is not None else None,
        "seam_issue_count": len(continuity.issues) if continuity is not None else None,
        "seam_scores": dict(seam_metrics),
        "localized_composite_applied": bool(latest_composite.get("applied")),
    }
