import json
from pathlib import Path

from app.schemas.edit_plan import EditIntent
from app.services.edit_scope import plan_prompt_intent, should_discover_occurrences


def test_prompt_scope_fixtures():
    cases = json.loads(
        (Path(__file__).parent / "fixtures" / "prompt_scope_cases.json").read_text()
    )

    for case in cases:
        result = plan_prompt_intent(case["prompt"])
        assert result.intent.scope == case["scope"]
        assert result.intent.change_type == case["change_type"]
        assert result.estimate.analysis_mode == case["analysis_mode"]
        assert result.estimate.requires_global_discovery is case["global"]


def test_explicit_range_has_priority_over_global_words():
    result = plan_prompt_intent("change it everywhere", explicit_range=True)
    assert result.intent.scope == "explicit_range"


def test_structured_jump_repairs_motion_extent_without_changing_scope():
    intent = EditIntent(
        raw_prompt="make this person jump everywhere",
        scope="local",
        change_type="motion",
        estimated_action_seconds=4.25,
    )
    result = plan_prompt_intent(intent.raw_prompt, structured_intent=intent)

    assert result.intent.scope == "local"
    assert result.intent.effect_extent == "motion_path"
    assert result.reason == "motion extent safety fallback"
    assert result.estimate.analysis_mode == "lazy_local"


def test_structured_jump_duration_reserves_preparation_and_recovery():
    intent = EditIntent(
        raw_prompt="make the car jump for two seconds",
        scope="local",
        change_type="motion",
        duration_policy="bounded_action",
        effect_extent="motion_path",
        estimated_action_seconds=2.0,
    )

    result = plan_prompt_intent(intent.raw_prompt, structured_intent=intent)

    assert result.intent.estimated_action_seconds == 3.5
    assert result.reason == "bounded motion duration safety fallback"


def test_repeated_jump_reserves_time_for_each_repetition():
    intent = EditIntent(
        raw_prompt="make the man jump up and down a few times",
        scope="local",
        change_type="motion",
        duration_policy="bounded_action",
        effect_extent="motion_path",
        estimated_action_seconds=2.5,
    )

    result = plan_prompt_intent(intent.raw_prompt, structured_intent=intent)

    assert result.intent.estimated_action_seconds == 5.5


def test_local_accept_does_not_imply_global_discovery():
    assert should_discover_occurrences(scope="local", explicitly_requested=False) is False
    assert should_discover_occurrences(scope="local", explicitly_requested=True) is True
    assert should_discover_occurrences(scope="all_occurrences", explicitly_requested=False) is True


def test_state_change_defaults_to_current_continuous_occurrence():
    result = plan_prompt_intent("make this ball pink")

    assert result.intent.scope == "local"
    assert result.intent.duration_policy == "continuous_occurrence"
    assert result.intent.temporal_behavior == "persistent_state"
    assert result.intent.effect_extent == "surface"


def test_action_defaults_to_bounded_local_window():
    result = plan_prompt_intent("make this ball bounce")

    assert result.intent.scope == "local"
    assert result.intent.duration_policy == "bounded_action"
    assert result.intent.temporal_behavior == "temporary"
    assert result.intent.effect_extent == "motion_path"


def test_feature_edit_uses_surface_extent():
    result = plan_prompt_intent("give this cat human-like eyes")

    assert result.intent.change_type == "appearance"
    assert result.intent.duration_policy == "continuous_occurrence"
    assert result.intent.effect_extent == "surface"


def test_object_transformation_can_change_complete_silhouette():
    result = plan_prompt_intent("turn this banana into an apple")

    assert result.intent.change_type == "replacement"
    assert result.intent.duration_policy == "continuous_occurrence"
    assert result.intent.effect_extent == "subject"


def test_trajectory_change_covers_the_current_occurrence():
    result = plan_prompt_intent("make this man run")

    assert result.intent.scope == "local"
    assert result.intent.duration_policy == "trajectory_continuation"
    assert result.intent.temporal_behavior == "future_changing_motion"
    assert result.estimate.analysis_mode == "complete_local_occurrence"


def test_created_event_defaults_to_bounded_local_window():
    result = plan_prompt_intent("open this window and make a balloon come out")

    assert result.intent.scope == "local"
    assert result.intent.duration_policy == "bounded_action"
    assert result.intent.temporal_behavior == "temporary"
    assert result.intent.requires_recovery_motion is True
    assert result.intent.estimated_action_seconds == 4.0
    assert result.intent.effect_extent == "new_object_path"
    assert result.estimate.analysis_mode == "lazy_local"


def test_created_event_repairs_overbroad_structured_intent():
    intent = EditIntent(
        raw_prompt="open this window and make a balloon come out",
        scope="local",
        change_type="motion",
        duration_policy="trajectory_continuation",
        temporal_behavior="future_changing_motion",
        estimated_action_seconds=4.0,
    )

    result = plan_prompt_intent(intent.raw_prompt, structured_intent=intent)

    assert result.intent.duration_policy == "bounded_action"
    assert result.intent.temporal_behavior == "temporary"
    assert result.intent.requires_recovery_motion is True
    assert result.reason == "bounded created-event safety fallback"


def test_created_event_repair_also_reserves_complete_action_time():
    intent = EditIntent(
        raw_prompt="open this window and make a balloon come out",
        scope="local",
        change_type="motion",
        duration_policy="trajectory_continuation",
        temporal_behavior="future_changing_motion",
        estimated_action_seconds=2.0,
    )

    result = plan_prompt_intent(intent.raw_prompt, structured_intent=intent)

    assert result.intent.duration_policy == "bounded_action"
    assert result.intent.estimated_action_seconds == 4.0


def test_explicit_created_event_continuation_stays_continuous():
    intent = EditIntent(
        raw_prompt="make a balloon come out and keep flying",
        scope="local",
        change_type="motion",
        duration_policy="trajectory_continuation",
        temporal_behavior="future_changing_motion",
        estimated_action_seconds=5.0,
    )

    result = plan_prompt_intent(intent.raw_prompt, structured_intent=intent)

    assert result.intent is intent
    assert result.intent.duration_policy == "trajectory_continuation"
    assert result.intent.temporal_behavior == "future_changing_motion"
