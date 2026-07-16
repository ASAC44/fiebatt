import pytest
from pydantic import ValidationError

from app.ai.services.provider_capabilities import VIDEO_PROVIDER_CAPABILITIES
from app.schemas.common import BBox
from app.schemas.edit_plan import EditCore, EditIntent, GenerationContext, LegacyRange


def test_context_keeps_core_distinct_from_handles():
    context = GenerationContext(
        start_ts=7.5,
        end_ts=13.5,
        edit_core=EditCore(start_ts=8.5, end_ts=12.0),
    )

    assert context.duration == pytest.approx(6.0)
    assert context.edit_core.duration == pytest.approx(3.5)


def test_context_rejects_core_outside_handles():
    with pytest.raises(ValidationError, match="must contain edit core"):
        GenerationContext(
            start_ts=8.0,
            end_ts=10.0,
            edit_core=EditCore(start_ts=7.5, end_ts=9.5),
        )


def test_legacy_range_maps_to_zero_handle_context():
    legacy = LegacyRange(
        start_ts=2.0,
        end_ts=5.0,
        reference_frame_ts=3.5,
        bbox=BBox(x=0.1, y=0.1, w=0.4, h=0.7),
    )

    context = legacy.as_generation_context()
    assert context.start_ts == context.edit_core.start_ts == 2.0
    assert context.end_ts == context.edit_core.end_ts == 5.0


def test_intent_defaults_ambiguous_scope_to_local():
    intent = EditIntent(raw_prompt="make this person jump", change_type="motion")
    assert intent.scope == "local"


def test_provider_capabilities_distinguish_mask_and_endpoint_limits():
    wan = VIDEO_PROVIDER_CAPABILITIES["wan"]
    veo = VIDEO_PROVIDER_CAPABILITIES["veo"]

    assert wan.source_video_edit is True
    assert wan.max_mask_duration == 5
    assert wan.max_total_duration == 10
    assert veo.supports_last_frame(8.0) is True
    assert veo.supports_last_frame(4.0) is False
