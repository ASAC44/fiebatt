import pytest

from ai.services.veo import _build_config, _resolve_duration


@pytest.mark.parametrize("duration", [4, 6, 8])
def test_veo_accepts_documented_durations(duration):
    assert _resolve_duration(duration) == duration


@pytest.mark.parametrize("duration", [3, 5, 7, 9])
def test_veo_rejects_unsupported_durations(duration):
    with pytest.raises(ValueError, match="duration must be one of"):
        _resolve_duration(duration)


def test_veo_reference_images_require_eight_seconds():
    with pytest.raises(ValueError, match="reference-image"):
        _build_config(
            duration=4,
            aspect_ratio="16:9",
            resolution="720P",
            reference_images=[object()],  # validation happens before SDK serialization
        )


def test_veo_uses_api_resolution_spelling():
    config = _build_config(duration=4, aspect_ratio="16:9", resolution="720P")
    assert config.duration_seconds == 4
    assert config.resolution == "720p"
