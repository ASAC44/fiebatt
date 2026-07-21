"""Stable, actionable errors for planning and queueing an edit."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentFailure:
    code: str
    stage: str
    user_message: str
    action: str
    retryable: bool

    def payload(self, *, request_id: str) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "stage": self.stage,
            "user_message": self.user_message,
            # Keep `message` while older web builds are still in circulation.
            "message": self.user_message,
            "action": self.action,
            "retryable": self.retryable,
            "request_id": request_id,
        }


def classify_agent_failure(error: Exception | str, *, stage: str) -> AgentFailure:
    technical = str(error).strip()
    lowered = technical.lower()

    if "selection" in lowered or "bounding box" in lowered:
        return AgentFailure(
            "selection_unavailable",
            "selection",
            "The selected subject is no longer available for this edit.",
            "Pause on the subject and draw the box again.",
            True,
        )
    if "target clip" in lowered or "active clip" in lowered or "source" in lowered:
        return AgentFailure(
            "active_clip_unavailable",
            "selection",
            "The active timeline clip could not be prepared.",
            "Move the playhead onto a visible clip, then retry.",
            True,
        )
    if "30" in lowered and any(word in lowered for word in ("limit", "seconds", "duration")):
        return AgentFailure(
            "edit_too_long",
            "planning",
            "This edit needs more than 30 seconds of generated video.",
            "Choose a shorter occurrence or wait for long-context editing.",
            False,
        )
    if any(token in lowered for token in ("2-15", "segment length", "window")):
        return AgentFailure(
            "invalid_edit_window",
            "planning",
            "The requested edit window could not be prepared safely.",
            "Move the playhead nearer the action and try again.",
            True,
        )
    if any(token in lowered for token in ("429", "rate limit", "quota", "exhausted")):
        return AgentFailure(
            "planner_busy",
            stage,
            "The planning model is temporarily busy.",
            "Retry this request in a moment.",
            True,
        )
    if "timeout" in lowered or "timed out" in lowered:
        return AgentFailure(
            "planner_timeout",
            stage,
            "Planning took too long and no render was started.",
            "Retry the same request.",
            True,
        )
    if "api key" in lowered or "not configured" in lowered:
        return AgentFailure(
            "planner_not_configured",
            stage,
            "Video planning is not configured on this deployment.",
            "Ask the deployment owner to configure the AI service.",
            False,
        )
    return AgentFailure(
        f"{stage}_failed",
        stage,
        "The edit could not be prepared, and no render was started.",
        "Retry once. If it repeats, move the playhead and redraw the selection.",
        True,
    )


def agent_failure_payload(
    error: Exception | str,
    *,
    stage: str,
    request_id: str,
) -> dict[str, str | bool]:
    return classify_agent_failure(error, stage=stage).payload(request_id=request_id)
