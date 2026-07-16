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


def test_structured_intent_is_reused_without_reclassification():
    intent = EditIntent(
        raw_prompt="make this person jump everywhere",
        scope="local",
        change_type="motion",
        estimated_action_seconds=4.25,
    )
    result = plan_prompt_intent(intent.raw_prompt, structured_intent=intent)

    assert result.intent is intent
    assert result.reason == "reused structured intent"
    assert result.estimate.analysis_mode == "lazy_local"


def test_local_accept_does_not_imply_global_discovery():
    assert should_discover_occurrences(scope="local", explicitly_requested=False) is False
    assert should_discover_occurrences(scope="local", explicitly_requested=True) is True
    assert should_discover_occurrences(scope="all_occurrences", explicitly_requested=False) is True
