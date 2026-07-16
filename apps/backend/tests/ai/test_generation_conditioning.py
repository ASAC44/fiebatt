from app.ai.services.conditioning import (
    GenerationConditioning,
    boundary_anchor_timestamps,
    route_provider_conditioning,
)


def _conditioning() -> GenerationConditioning:
    return GenerationConditioning(
        subject_reference_path="subject.png",
        subject_reference_timestamp=5.0,
        mask_image_url="https://cdn.example.test/mask.png",
        mask_frame_id=31,
        start_anchor_path="start-full-frame.jpg",
        start_anchor_timestamp=4.0,
        end_anchor_path="end-full-frame.jpg",
        end_anchor_timestamp=8.0,
    )


def test_source_edit_adapters_receive_subject_reference_not_boundary_frame():
    for provider in ("wan", "happyhorse"):
        routed = route_provider_conditioning(
            provider,
            _conditioning(),
            source_video=True,
            duration=4.0,
        )
        assert routed.subject_reference_path == "subject.png"
        assert routed.first_frame_path is None
        assert routed.last_frame_path is None


def test_veo_receives_only_full_boundary_frames():
    routed = route_provider_conditioning(
        "veo",
        _conditioning(),
        source_video=False,
        duration=8.0,
    )
    assert routed.subject_reference_path is None
    assert routed.first_frame_path == "start-full-frame.jpg"
    assert routed.last_frame_path == "end-full-frame.jpg"


def test_veo_omits_unsupported_last_frame_for_shorter_requests():
    routed = route_provider_conditioning(
        "veo",
        _conditioning(),
        source_video=False,
        duration=6.0,
    )
    assert routed.first_frame_path == "start-full-frame.jpg"
    assert routed.last_frame_path is None


def test_mesh_and_image_only_happyhorse_receive_start_boundary_only():
    for provider in ("meshapi_veo", "happyhorse"):
        routed = route_provider_conditioning(
            provider,
            _conditioning(),
            source_video=False,
            duration=5.0,
        )
        assert routed.subject_reference_path is None
        assert routed.first_frame_path == "start-full-frame.jpg"
        assert routed.last_frame_path is None


def test_boundary_timestamps_select_real_first_and_last_source_frames():
    start, end = boundary_anchor_timestamps(4.0, 8.0, 25.0)
    assert start == 4.0
    assert end == 7.96


def test_persisted_metadata_never_contains_worker_file_paths():
    metadata = _conditioning().metadata()
    assert metadata["subject_reference_timestamp"] == 5.0
    assert metadata["start_anchor_timestamp"] == 4.0
    assert metadata["end_anchor_timestamp"] == 8.0
    assert "subject.png" not in str(metadata)
