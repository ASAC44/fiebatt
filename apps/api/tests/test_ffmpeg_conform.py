from pathlib import Path

import pytest

from app.services import ffmpeg


SOURCE_META = {
    "duration": 4.04,
    "width": 1280,
    "height": 720,
    "fps": 25.0,
    "has_audio": True,
}


@pytest.mark.asyncio
async def test_small_provider_duration_drift_is_retimed(monkeypatch, tmp_path):
    commands = []

    async def probe(path):
        return {**SOURCE_META, "duration": 3.88} if "generated" in str(path) else SOURCE_META

    async def run(command):
        commands.append(command)

    monkeypatch.setattr(ffmpeg, "probe", probe)
    monkeypatch.setattr(ffmpeg, "_run", run)

    await ffmpeg.conform_generated_edit(
        "generated.mp4",
        "source.mp4",
        4.04,
        tmp_path / "conformed.mp4",
    )

    filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
    assert "setpts=1.04123711*PTS" in filter_complex
    assert "trim=duration=4.040" in filter_complex


@pytest.mark.asyncio
async def test_large_provider_duration_shortfall_remains_invalid(monkeypatch, tmp_path):
    async def probe(path):
        return {**SOURCE_META, "duration": 3.0} if "generated" in str(path) else SOURCE_META

    monkeypatch.setattr(ffmpeg, "probe", probe)

    with pytest.raises(ValueError, match="generated clip is too short"):
        await ffmpeg.conform_generated_edit(
            "generated.mp4",
            "source.mp4",
            4.04,
            tmp_path / "conformed.mp4",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("generated_duration", "expects_retime"),
    [
        (4.04, False),
        (4.20, False),
        (4.00, True),
        (3.88, True),
    ],
)
async def test_normal_provider_duration_variants_are_conformed(
    monkeypatch,
    tmp_path,
    generated_duration,
    expects_retime,
):
    commands = []

    async def probe(path):
        return (
            {**SOURCE_META, "duration": generated_duration}
            if "generated" in str(path)
            else SOURCE_META
        )

    async def run(command):
        commands.append(command)

    monkeypatch.setattr(ffmpeg, "probe", probe)
    monkeypatch.setattr(ffmpeg, "_run", run)

    await ffmpeg.conform_generated_edit(
        "generated.mp4",
        "source.mp4",
        4.04,
        tmp_path / "conformed.mp4",
    )

    filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
    has_speed_adjustment = filter_complex.split("trim=", 1)[0].startswith("[0:v]setpts=")
    assert has_speed_adjustment is expects_retime
