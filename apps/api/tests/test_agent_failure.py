import pytest

from app.services.agent_failure import classify_agent_failure


@pytest.mark.parametrize(
    ("error", "stage", "code", "retryable"),
    [
        ("selection not found", "planning", "selection_unavailable", True),
        ("active clip is outside source", "planning", "active_clip_unavailable", True),
        ("edit exceeds 30 seconds limit", "planning", "edit_too_long", False),
        ("segment length must be 2-15s", "planning", "invalid_edit_window", True),
        ("HTTP 429 quota exhausted", "planning", "planner_busy", True),
        ("request timed out", "queueing", "planner_timeout", True),
        ("No AI API key configured", "planning", "planner_not_configured", False),
    ],
)
def test_agent_failures_are_specific_and_actionable(error, stage, code, retryable):
    failure = classify_agent_failure(error, stage=stage)

    assert failure.code == code
    assert failure.retryable is retryable
    assert failure.user_message
    assert failure.action
    assert error not in failure.user_message
