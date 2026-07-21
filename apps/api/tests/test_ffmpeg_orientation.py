import asyncio
import subprocess

from app.services import ffmpeg
from app.services.ffmpeg import _display_dimensions, _stream_rotation


def test_rotation_side_data_controls_display_dimensions():
    stream = {
        "width": 1920,
        "height": 1080,
        "tags": {"rotate": "0"},
        "side_data_list": [{"rotation": -90}],
    }

    rotation = _stream_rotation(stream)

    assert rotation == 270
    assert _display_dimensions(1920, 1080, rotation) == (1080, 1920)


def test_unrotated_and_half_turn_media_keep_encoded_dimensions():
    assert _display_dimensions(720, 1280, 0) == (720, 1280)
    assert _display_dimensions(720, 1280, 180) == (720, 1280)


def test_invalid_rotation_metadata_falls_back_safely():
    assert _stream_rotation({"tags": {"rotate": "unknown"}}) == 0


def test_probe_and_extract_honor_display_rotation(tmp_path):
    landscape = tmp_path / "landscape.mp4"
    rotated = tmp_path / "rotated.mp4"
    extracted = tmp_path / "extracted.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "testsrc2=duration=1:size=320x180:rate=12",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(landscape),
        ],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-display_rotation:v:0", "90", "-i", str(landscape),
            "-c", "copy", str(rotated),
        ],
        capture_output=True,
        check=True,
    )

    metadata = asyncio.run(ffmpeg.probe(rotated))
    asyncio.run(ffmpeg.extract_clip(rotated, 0, 0.8, extracted, with_audio=False))
    extracted_metadata = asyncio.run(ffmpeg.probe(extracted))

    assert (metadata["encoded_width"], metadata["encoded_height"]) == (320, 180)
    assert (metadata["width"], metadata["height"], metadata["rotation"]) == (180, 320, 90)
    assert (extracted_metadata["width"], extracted_metadata["height"]) == (180, 320)
    assert extracted_metadata["rotation"] == 0
