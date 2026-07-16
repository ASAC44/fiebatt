from app.services.continuity_validator import ContinuityReport
from app.services.generation_telemetry import build_local_flow_telemetry
from app.services.generation_window import GenerationWindow


def test_adaptive_telemetry_compares_cost_with_fixed_window_baseline():
    window = GenerationWindow(8.25, 11.75, 7.5, 12.5, adaptive=True)
    continuity = ContinuityReport(
        passed=True,
        metrics={
            "entry_subject_motion_jump": 0.12,
            "exit_subject_motion_jump": 0.18,
        },
        sampled_frames=16,
    )

    telemetry = build_local_flow_telemetry(
        payload={
            "plan_scope": "local",
            "analysis_duration_ms": 42.5,
            "analysis_frames": 29,
            "fixed_window_baseline_seconds": 3.0,
            "localized_compositing": [{"applied": True}],
        },
        window=window,
        continuity=continuity,
        quality_state="pass",
        attempts=2,
        generated_seconds=10.0,
        provider_attempts=["wan", "happyhorse"],
        selected_provider="happyhorse",
    )

    assert telemetry["flow_mode"] == "adaptive_planned"
    assert telemetry["scope"] == "local"
    assert telemetry["context_overhead_seconds"] == 1.5
    assert telemetry["generated_over_baseline_seconds"] == 7.0
    assert telemetry["retries"] == 1
    assert telemetry["continuity_passed"] is True
    assert telemetry["localized_composite_applied"] is True


def test_legacy_telemetry_has_no_context_overhead():
    window = GenerationWindow(8.5, 11.5, 8.5, 11.5, adaptive=False)

    telemetry = build_local_flow_telemetry(
        payload={},
        window=window,
        continuity=None,
        quality_state="pass",
        attempts=1,
        generated_seconds=3.0,
        provider_attempts=["wan"],
        selected_provider="wan",
    )

    assert telemetry["flow_mode"] == "legacy_fixed_window"
    assert telemetry["context_overhead_seconds"] == 0.0
    assert telemetry["generated_over_baseline_seconds"] == 0.0
    assert telemetry["continuity_passed"] is None
