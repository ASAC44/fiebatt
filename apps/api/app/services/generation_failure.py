"""Turn technical generation failures into stable product states."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenerationFailure:
    code: str
    user_message: str
    technical_message: str
    retryable: bool

    def metadata(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "user_message": self.user_message,
            "technical_message": self.technical_message,
            "retryable": self.retryable,
        }


def classify_generation_failure(error: Exception | str) -> GenerationFailure:
    technical = str(error).strip() or type(error).__name__
    lowered = technical.lower()

    if "timed out" in lowered or "timeout" in lowered:
        return GenerationFailure(
            "provider_timeout",
            "The video model did not finish in time. Your source video and timeline are unchanged.",
            technical,
            True,
        )
    if "429" in lowered or "rate limit" in lowered or "quota" in lowered:
        return GenerationFailure(
            "provider_busy",
            "The video model is temporarily busy. Your source video and timeline are unchanged.",
            technical,
            True,
        )
    if any(token in lowered for token in ("connect", "network", "502", "503", "504")):
        return GenerationFailure(
            "provider_unavailable",
            "The video service could not be reached. Your source video and timeline are unchanged.",
            technical,
            True,
        )
    if "too short" in lowered or "duration" in lowered:
        return GenerationFailure(
            "invalid_provider_duration",
            "The video model returned an incomplete clip, so it was not added to your timeline.",
            technical,
            True,
        )
    if any(token in lowered for token in ("decode", "ffmpeg", "ffprobe", "empty video")):
        return GenerationFailure(
            "invalid_provider_media",
            "The video model returned an unreadable clip, so it was not added to your timeline.",
            technical,
            True,
        )
    if any(token in lowered for token in ("source", "selection", "project missing")):
        return GenerationFailure(
            "source_unavailable",
            "The selected source could not be prepared. Nothing was changed on your timeline.",
            technical,
            False,
        )
    return GenerationFailure(
        "generation_failed",
        "This render could not be completed. Your source video and timeline are unchanged.",
        technical,
        True,
    )
