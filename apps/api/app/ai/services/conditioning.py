"""Explicit generation-conditioning contracts and provider routing."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenerationConditioning:
    """Keep target identity inputs separate from timeline boundary inputs."""

    subject_reference_path: str | None = None
    subject_reference_timestamp: float | None = None
    mask_image_url: str | None = None
    mask_frame_id: int = 1
    start_anchor_path: str | None = None
    start_anchor_timestamp: float | None = None
    end_anchor_path: str | None = None
    end_anchor_timestamp: float | None = None

    def __post_init__(self) -> None:
        if self.mask_frame_id < 1:
            raise ValueError("mask_frame_id must be at least one")
        timestamps = (
            self.subject_reference_timestamp,
            self.start_anchor_timestamp,
            self.end_anchor_timestamp,
        )
        if any(value is not None and value < 0.0 for value in timestamps):
            raise ValueError("conditioning timestamps cannot be negative")
        if (
            self.start_anchor_timestamp is not None
            and self.end_anchor_timestamp is not None
            and self.end_anchor_timestamp < self.start_anchor_timestamp
        ):
            raise ValueError("end anchor cannot precede start anchor")

    def metadata(self) -> dict[str, object]:
        """Persist safe evidence without leaking local worker paths."""
        return {
            "subject_reference_timestamp": self.subject_reference_timestamp,
            "subject_reference_available": self.subject_reference_path is not None,
            "mask_available": self.mask_image_url is not None,
            "mask_frame_id": self.mask_frame_id,
            "start_anchor_timestamp": self.start_anchor_timestamp,
            "start_anchor_available": self.start_anchor_path is not None,
            "end_anchor_timestamp": self.end_anchor_timestamp,
            "end_anchor_available": self.end_anchor_path is not None,
        }


@dataclass(frozen=True, slots=True)
class ProviderConditioning:
    """Inputs mapped to the semantic slots exposed by a provider adapter."""

    subject_reference_path: str | None = None
    first_frame_path: str | None = None
    last_frame_path: str | None = None


def route_provider_conditioning(
    provider: str,
    conditioning: GenerationConditioning,
    *,
    source_video: bool,
    duration: float | None = None,
) -> ProviderConditioning:
    """Never place an isolated subject crop in a boundary-frame slot."""
    if provider in {"wan", "happyhorse"} and source_video:
        return ProviderConditioning(
            subject_reference_path=conditioning.subject_reference_path,
        )
    if provider == "wan":
        return ProviderConditioning(
            first_frame_path=conditioning.start_anchor_path,
            last_frame_path=conditioning.end_anchor_path,
        )
    if provider == "veo":
        return ProviderConditioning(
            first_frame_path=conditioning.start_anchor_path,
            last_frame_path=(
                conditioning.end_anchor_path
                if duration is not None and abs(duration - 8.0) <= 0.05
                else None
            ),
        )
    if provider in {"meshapi_veo", "happyhorse"}:
        return ProviderConditioning(first_frame_path=conditioning.start_anchor_path)
    return ProviderConditioning()


def boundary_anchor_timestamps(
    start_ts: float,
    end_ts: float,
    fps: float,
) -> tuple[float, float]:
    """Return source timestamps for the first and final frames of a context."""
    if start_ts < 0.0 or end_ts <= start_ts:
        raise ValueError("generation context must have positive duration")
    frame_seconds = 1.0 / max(1.0, fps)
    return start_ts, max(start_ts, end_ts - frame_seconds)
