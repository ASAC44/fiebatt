"""Video-provider capabilities and deterministic routing rules."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoProviderCapabilities:
    source_video_edit: bool
    allowed_durations: tuple[int, ...] | None
    max_duration: int
    first_last_frames: bool = False
    reference_images: bool = False


VIDEO_PROVIDER_CAPABILITIES: dict[str, VideoProviderCapabilities] = {
    "wan": VideoProviderCapabilities(True, None, 10, reference_images=True),
    "happyhorse": VideoProviderCapabilities(True, None, 15, reference_images=True),
    "veo": VideoProviderCapabilities(
        False, (4, 6, 8), 8, first_last_frames=True, reference_images=True
    ),
    "meshapi_veo": VideoProviderCapabilities(False, None, 8, reference_images=True),
}


def normalize_video_provider(value: str | None, *, default: str = "auto") -> str:
    provider = (value or default).strip().lower()
    aliases = {"mesh-veo": "meshapi_veo", "mesh_veo": "meshapi_veo"}
    provider = aliases.get(provider, provider)
    if provider == "auto" or provider in VIDEO_PROVIDER_CAPABILITIES:
        return provider
    return default


def select_video_provider(
    requested: str | None,
    *,
    source_video: bool,
    duration: float | None = None,
) -> str:
    provider = normalize_video_provider(requested)
    if provider != "auto":
        return provider
    # Uploaded-footage edits need temporal context. Veo is image-conditioned in
    # this adapter and is reserved for explicit generation requests.
    if source_video:
        # Wan 2.7 video-edit accepts at most ten seconds. HappyHorse retains a
        # source-video path for the remaining API-supported 10–15 second range.
        return "happyhorse" if duration is not None and duration > 10.05 else "wan"
    return "veo"


def validate_provider_duration(provider: str, duration: float) -> str | None:
    capabilities = VIDEO_PROVIDER_CAPABILITIES.get(provider)
    if capabilities is None:
        return None
    if duration > capabilities.max_duration + 0.05:
        return f"{provider} supports edit durations up to {capabilities.max_duration} seconds"
    if capabilities.allowed_durations is None:
        return None
    rounded = round(duration)
    if abs(duration - rounded) <= 0.05 and rounded in capabilities.allowed_durations:
        return None
    allowed = ", ".join(str(item) for item in capabilities.allowed_durations)
    return f"{provider} requires an edit duration of exactly {allowed} seconds"
