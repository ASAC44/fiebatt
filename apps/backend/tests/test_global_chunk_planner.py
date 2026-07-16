import pytest

from app.ai.services.provider_capabilities import VIDEO_PROVIDER_CAPABILITIES
from app.services.global_chunk_planner import (
    SplitEvidence,
    plan_occurrence_chunks,
)


def _assert_valid_coverage(chunks, start: float, end: float) -> None:
    assert chunks[0].edit_start == pytest.approx(start)
    assert chunks[-1].edit_end == pytest.approx(end)
    for chunk in chunks:
        capabilities = VIDEO_PROVIDER_CAPABILITIES[chunk.provider]
        assert chunk.edit_end > chunk.edit_start
        assert chunk.context_start <= chunk.edit_start
        assert chunk.context_end >= chunk.edit_end
        assert chunk.context_duration <= capabilities.max_total_duration + 0.05
    for left, right in zip(chunks, chunks[1:], strict=False):
        assert left.edit_end == pytest.approx(right.edit_start)
        assert left.context_end >= right.context_start


def test_short_occurrence_uses_one_wan_chunk():
    chunks = plan_occurrence_chunks(
        occurrence_start=4.0,
        occurrence_end=7.0,
        project_duration=20.0,
    )

    assert len(chunks) == 1
    assert chunks[0].provider == "wan"
    assert chunks[0].context_start == pytest.approx(3.25)
    assert chunks[0].context_end == pytest.approx(7.75)


def test_medium_occurrence_uses_one_happyhorse_chunk():
    chunks = plan_occurrence_chunks(
        occurrence_start=4.0,
        occurrence_end=16.0,
        project_duration=20.0,
    )

    assert len(chunks) == 1
    assert chunks[0].provider == "happyhorse"
    assert chunks[0].context_duration == pytest.approx(13.5)


def test_long_occurrence_is_split_without_core_gaps_or_duplicates():
    chunks = plan_occurrence_chunks(
        occurrence_start=5.0,
        occurrence_end=35.0,
        project_duration=40.0,
    )

    assert len(chunks) == 3
    assert {chunk.provider for chunk in chunks} == {"happyhorse"}
    _assert_valid_coverage(chunks, 5.0, 35.0)


def test_boundary_space_is_used_before_adding_an_extra_chunk():
    chunks = plan_occurrence_chunks(
        occurrence_start=0.0,
        occurrence_end=26.0,
        project_duration=40.0,
        requested_provider="wan",
    )

    assert len(chunks) == 3
    _assert_valid_coverage(chunks, 0.0, 26.0)


def test_split_prefers_strong_visual_evidence_near_provider_limit():
    chunks = plan_occurrence_chunks(
        occurrence_start=5.0,
        occurrence_end=35.0,
        project_duration=40.0,
        split_evidence=[
            SplitEvidence(18.4, "stable_motion", 1.0),
            SplitEvidence(17.0, "shot_cut", 1.0),
        ],
    )

    assert chunks[0].edit_end == pytest.approx(17.0)
    assert chunks[0].split_reason == "shot_cut"
    _assert_valid_coverage(chunks, 5.0, 35.0)


def test_short_edge_occurrence_expands_context_to_provider_minimum():
    chunks = plan_occurrence_chunks(
        occurrence_start=0.0,
        occurrence_end=0.5,
        project_duration=10.0,
    )

    assert len(chunks) == 1
    assert chunks[0].context_duration == pytest.approx(2.0)


@pytest.mark.parametrize("provider", ["veo", "meshapi_veo"])
def test_image_conditioned_provider_is_rejected(provider: str):
    with pytest.raises(ValueError, match="cannot preserve source-video motion"):
        plan_occurrence_chunks(
            occurrence_start=2.0,
            occurrence_end=6.0,
            project_duration=10.0,
            requested_provider=provider,
        )


@pytest.mark.parametrize("duration", [0.5, 2.0, 9.0, 14.0, 15.0, 30.0, 60.0, 90.0])
def test_plans_remain_valid_across_occurrence_lengths(duration: float):
    start = 5.0
    end = start + duration
    chunks = plan_occurrence_chunks(
        occurrence_start=start,
        occurrence_end=end,
        project_duration=end + 5.0,
    )

    _assert_valid_coverage(chunks, start, end)

