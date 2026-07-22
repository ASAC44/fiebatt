"""Resolve the authoritative core and padded context used by a generation job."""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas.edit_plan import LocalRangeResolution


@dataclass(frozen=True, slots=True)
class GenerationWindow:
    core_start: float
    core_end: float
    context_start: float
    context_end: float
    adaptive: bool = False

    @property
    def core_duration(self) -> float:
        return self.core_end - self.core_start

    @property
    def context_duration(self) -> float:
        return self.context_end - self.context_start

    @property
    def edit_start_offset(self) -> float:
        return self.core_start - self.context_start

    @property
    def edit_end_offset(self) -> float:
        return self.core_end - self.context_start

    @property
    def pre_handle(self) -> float:
        return self.edit_start_offset

    @property
    def post_handle(self) -> float:
        return self.context_end - self.core_end

    def metadata(self) -> dict[str, float | bool]:
        return {
            "adaptive": self.adaptive,
            "core_start": self.core_start,
            "core_end": self.core_end,
            "context_start": self.context_start,
            "context_end": self.context_end,
            "edit_start_offset": self.edit_start_offset,
            "edit_end_offset": self.edit_end_offset,
            "pre_handle": self.pre_handle,
            "post_handle": self.post_handle,
        }


def resolve_generation_window(
    core_start: float,
    core_end: float,
    *,
    payload: dict,
    project_duration: float,
) -> GenerationWindow:
    """Use padded context only when the API explicitly enabled it."""
    if core_start < 0.0 or core_end <= core_start:
        raise ValueError("generation core must have positive duration")
    if core_end > project_duration + 1e-3:
        raise ValueError("generation core exceeds project duration")
    if not bool(payload.get("adaptive_context_enabled")):
        return GenerationWindow(
            core_start=core_start,
            core_end=core_end,
            context_start=core_start,
            context_end=core_end,
            adaptive=False,
        )

    raw_resolution = payload.get("planned_context")
    if not isinstance(raw_resolution, dict):
        raise ValueError("adaptive generation requires planned context")
    resolution = LocalRangeResolution.model_validate(raw_resolution)
    if (
        abs(resolution.edit_core.start_ts - core_start) > 0.05
        or abs(resolution.edit_core.end_ts - core_end) > 0.05
    ):
        raise ValueError("planned edit core does not match generation job")

    context_start = max(0.0, min(resolution.generation_context.start_ts, core_start))
    context_end = min(
        project_duration,
        max(resolution.generation_context.end_ts, core_end),
    )
    if context_end <= context_start:
        raise ValueError("planned generation context has no duration")
    return GenerationWindow(
        core_start=core_start,
        core_end=core_end,
        context_start=context_start,
        context_end=context_end,
        adaptive=True,
    )


def protected_context_prompt(
    prompt: str,
    window: GenerationWindow,
    *,
    temporal_behavior: str = "temporary",
    effect_extent: str = "subject",
) -> str:
    """Describe editable time and continuity handles without suppressing the edit."""
    if not window.adaptive or (window.pre_handle < 0.05 and window.post_handle < 0.05):
        return prompt
    if temporal_behavior == "persistent_state":
        ending_contract = (
            "Keep the requested state continuous through every frame where the "
            "selected target remains visible. Do not revert it to the old state "
            "before the target leaves the occurrence."
        )
    elif temporal_behavior == "future_changing_motion":
        ending_contract = (
            "Continue the new motion naturally. Do not snap the subject back to "
            "its old pose, position, or trajectory merely to match the source. "
            "Use the outgoing handle to preserve camera and surrounding motion."
        )
    else:
        ending_contract = (
            "After completing the temporary action, recover pose and velocity "
            "naturally toward the outgoing source motion."
        )

    effect_contract = {
        "surface": (
            "Keep the visible change on the requested surface and preserve nearby "
            "parts of the subject."
        ),
        "motion_path": (
            "The selected subject may move through the space required by the "
            "action; preserve unrelated subjects and the scene around that path."
        ),
        "new_object_path": (
            "Allow the requested new object to emerge from the selected anchor "
            "and move through the necessary nearby space; preserve unrelated scene content."
        ),
        "scene": "Change the scene only as explicitly requested.",
    }.get(
        effect_extent,
        "Allow the selected subject's required silhouette change while preserving unrelated content.",
    )

    if effect_extent == "motion_path" and window.pre_handle >= 0.05:
        entrance_contract = (
            f"For the first {window.pre_handle:.3f} seconds, continue only the "
            "incoming source motion and keep the subject in its incoming action "
            "state. Do not begin the requested action inside that handle. At the "
            "editable boundary, show its natural preparation and onset before its "
            "peak. The first edited motion must be preparation, not the peak pose."
        )
    else:
        entrance_contract = (
            "Do not begin the requested change inside the incoming handle; begin "
            "it continuously after that handle."
        )

    return (
        "SOURCE-CONTINUITY EDIT: The requested change must be clearly completed "
        "inside seconds "
        f"{window.edit_start_offset:.3f} through {window.edit_end_offset:.3f} "
        f"relative to the supplied clip. Use the first {window.pre_handle:.3f} "
        f"seconds and final {window.post_handle:.3f} seconds as continuity reference "
        "handles. Preserve their subjects, colours, lighting, camera, background, "
        "and direction of motion as closely as possible. Start from the incoming "
        "pose and velocity, then perform the requested change clearly and completely. "
        f"{entrance_contract} "
        f"{ending_contract} {effect_contract} "
        "The handles guide a continuous entrance and exit; they must not prevent or "
        "weaken the requested edit. Do not use a "
        "cut, fade, dissolve, freeze, or sudden appearance or disappearance.\n\n"
        + prompt
    )
