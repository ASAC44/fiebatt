from app.schemas.edit_plan import SemanticEditPlan


def test_persistent_plan_repairs_zero_action_duration_without_losing_semantics():
    plan = SemanticEditPlan.model_validate(
        {
            "decision": {
                "scope": "local",
                "change_type": "replacement",
                "duration_policy": "continuous_occurrence",
                "temporal_behavior": "persistent_state",
                "estimated_action_seconds": 0,
                "reasoning": "The replacement should persist while the shirt is visible.",
            },
            "variants": [
                {
                    "intent": "replace selected shirt text",
                    "description": "Replace the shirt text with Hello World",
                    "region_emphasis": "shirt text only",
                    "prompt_for_video_edit": "Replace the shirt text with Hello World.",
                }
            ],
        }
    )

    assert plan.decision.estimated_action_seconds == 3.0
    assert plan.decision.duration_policy == "continuous_occurrence"
