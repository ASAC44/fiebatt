"""Build provider prompts without weakening the user's exact request."""
from __future__ import annotations

from typing import Any


def planned_edit_prompt(user_prompt: str, plan: dict[str, Any]) -> str:
    planned = str(
        plan.get("prompt_for_video_edit")
        or plan.get("prompt_for_runway")
        or plan.get("prompt_for_veo")
        or ""
    ).strip()
    requirement = user_prompt.strip()
    if not planned or planned.casefold() == requirement.casefold():
        return requirement
    return f"Required result: {requirement}\nDetails: {planned}"
