from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.services import global_seam
from app.services.continuity_validator import (
    ContinuityIssue,
    ContinuityReport,
)


def _frame(value: int) -> np.ndarray:
    return np.full((32, 48, 3), value, dtype=np.uint8)


def _sample(timestamp: float, left: int, right: int) -> global_seam.SeamFrames:
    return global_seam.SeamFrames(
        timestamp=timestamp,
        left_before=_frame(left),
        left_at=_frame(left),
        right_at=_frame(right),
        right_after=_frame(right),
    )


BBOX = {"x": 0.25, "y": 0.2, "w": 0.3, "h": 0.6}


def test_seam_selection_chooses_lowest_error_frame():
    choice = global_seam.select_best_seam(
        [
            _sample(4.5, 20, 100),
            _sample(5.0, 40, 41),
            _sample(5.5, 60, 100),
        ],
        bbox=BBOX,
    )

    assert choice.timestamp == pytest.approx(5.0)
    assert choice.score < 0.01
    assert choice.samples == 3


def test_seam_selection_rejects_visually_unrelated_overlap():
    with pytest.raises(ValueError, match="failed seam validation"):
        global_seam.select_best_seam(
            [_sample(5.0, 0, 255)],
            bbox=BBOX,
        )


@pytest.mark.asyncio
async def test_failed_overlap_targets_right_chunk_for_retry(monkeypatch, tmp_path):
    left = SimpleNamespace(context_start=0.0, context_end=6.0, index=0)
    right = SimpleNamespace(
        context_start=4.0,
        context_end=10.0,
        index=1,
        payload_json={
            "track_frames": [
                {
                    "timestamp": 5.0,
                    "state": "tracked",
                    "bbox": BBOX,
                }
            ]
        },
    )

    monkeypatch.setattr(
        global_seam,
        "_read_frame",
        lambda path, timestamp: _frame(0 if "left" in path.name else 255),
    )
    with pytest.raises(global_seam.GlobalSeamError) as error:
        await global_seam.choose_chunk_seam(
            left,
            right,
            tmp_path / "left.mp4",
            tmp_path / "right.mp4",
            fps=10.0,
        )

    assert error.value.retry_chunk_index == 1


def test_outer_report_keeps_only_requested_boundary_issues():
    report = ContinuityReport(
        passed=False,
        metrics={},
        issues=[
            ContinuityIssue("entry_jump", 1.0, 0.5, "pre"),
            ContinuityIssue("exit_jump", 1.0, 0.5, "post"),
            ContinuityIssue("duration_delta_s", 1.0, 0.1, None),
        ],
    )

    filtered = global_seam._outer_report(report, {"pre"})

    assert [issue.code for issue in filtered.issues] == [
        "entry_jump",
        "duration_delta_s",
    ]


@pytest.mark.asyncio
async def test_assembly_trims_at_selected_seam_without_crossfade(
    monkeypatch,
    tmp_path,
):
    chunks = [
        SimpleNamespace(
            index=0,
            context_start=0.0,
            context_end=6.0,
            output_url="/chunk-0.mp4",
        ),
        SimpleNamespace(
            index=1,
            context_start=4.0,
            context_end=10.0,
            output_url="/chunk-1.mp4",
        ),
    ]
    occurrence = SimpleNamespace(edit_start=0.5, edit_end=9.5)
    project = SimpleNamespace(
        fps=30.0,
        video_path=str(tmp_path / "source.mp4"),
        video_url="/source.mp4",
    )
    extracts = []
    concatenated = []
    reserved = iter(
        [
            (tmp_path / "span-0.mp4", "/span-0"),
            (tmp_path / "span-1.mp4", "/span-1"),
            (tmp_path / "assembled-video.mp4", "/assembled-video"),
            (tmp_path / "source-audio.mp4", "/source-audio"),
            (tmp_path / "assembled.mp4", "/assembled"),
        ]
    )

    async def path_from_url(url):
        return tmp_path / url.removeprefix("/")

    async def validate(**kwargs):
        return {"entry": {"passed": True}, "exit": {"passed": True}}

    async def choose(*args, **kwargs):
        return global_seam.SeamChoice(5.25, 0.01, 9)

    async def extract(source, start, end, output, *, with_audio):
        extracts.append((Path(source), start, end, Path(output), with_audio))
        return Path(output)

    async def concat(paths, output):
        concatenated.append((list(paths), Path(output)))
        return Path(output)

    async def materialize_source(_video_path, _video_url):
        return tmp_path / "source.mp4"

    conformed = []

    async def conform(generated, source, duration, output):
        conformed.append((Path(generated), Path(source), duration, Path(output)))
        return Path(output)

    async def publish(path, *, content_type):
        return "/media/assembled.mp4"

    monkeypatch.setattr(global_seam.storage, "path_from_url", path_from_url)
    monkeypatch.setattr(global_seam, "_validate_outer_boundaries", validate)
    monkeypatch.setattr(global_seam, "choose_chunk_seam", choose)
    monkeypatch.setattr(global_seam.storage, "new_path", lambda *args: next(reserved))
    monkeypatch.setattr(global_seam.ffmpeg, "extract_clip", extract)
    monkeypatch.setattr(global_seam.ffmpeg, "concat_video_clips", concat)
    monkeypatch.setattr(global_seam.ffmpeg, "conform_generated_edit", conform)
    monkeypatch.setattr(global_seam.storage, "materialize_source", materialize_source)
    monkeypatch.setattr(global_seam.storage, "publish", publish)

    result = await global_seam.assemble_global_occurrence(
        project=project,
        occurrence=occurrence,
        chunks=chunks,
    )

    assert result.output_url == "/media/assembled.mp4"
    assert result.seams[0].timestamp == pytest.approx(5.25)
    assert extracts[0][1:3] == pytest.approx((0.5, 5.25))
    assert extracts[1][1:3] == pytest.approx((1.25, 5.5))
    assert extracts[2][1:3] == pytest.approx((0.5, 9.5))
    assert extracts[2][4] is True
    assert len(concatenated) == 1
    assert conformed[0][2] == pytest.approx(9.0)
