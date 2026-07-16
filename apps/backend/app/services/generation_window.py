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


def protected_context_prompt(prompt: str, window: GenerationWindow) -> str:
    """Tell source-video editors exactly which padded seconds may change."""
    if not window.adaptive:
        return prompt
    return (
        "PADDED SOURCE EDIT CONTRACT: The supplied video includes protected "
        "motion context before and after the requested edit. Modify only seconds "
        f"{window.edit_start_offset:.3f} through {window.edit_end_offset:.3f} "
        "relative to the supplied clip. Preserve frames, subject motion, camera "
        f"motion, background, and audio in the first {window.pre_handle:.3f} "
        f"seconds and final {window.post_handle:.3f} seconds. Use those handles "
        "only to enter and leave the edit continuously; do not introduce a cut, "
        "fade, freeze, or transition.\n\n"
        + prompt
    )
