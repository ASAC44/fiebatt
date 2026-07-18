"""Validate global-edit boundaries and assemble overlapping generated chunks."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.models.project import Project
from app.models.propagation import GlobalGenerationChunk, GlobalOccurrencePlan
from app.services import ffmpeg, storage
from app.services.continuity_validator import (
    ContinuityReport,
    validate_generated_continuity,
)
from app.services.generation_window import GenerationWindow
from app.services.global_chunk_execution import target_bbox


from app.services.seam_matching import (
    SeamChoice,
    SeamFrames,
    seam_score,
    select_best_seam,
)


SEAM_SAMPLES = 9


class GlobalSeamError(ValueError):
    def __init__(self, message: str, *, retry_chunk_index: int):
        super().__init__(message)
        self.retry_chunk_index = retry_chunk_index


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    output_url: str
    seams: tuple[SeamChoice, ...]
    continuity: dict


def _read_frame(path: Path, timestamp: float) -> np.ndarray:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"could not open generated chunk: {path}")
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok or frame is None:
        raise ValueError(f"could not read generated chunk frame at {timestamp:.3f}s")
    return frame


async def choose_chunk_seam(
    left: GlobalGenerationChunk,
    right: GlobalGenerationChunk,
    left_path: Path,
    right_path: Path,
    *,
    fps: float,
) -> SeamChoice:
    overlap_start = max(left.context_start, right.context_start)
    overlap_end = min(left.context_end, right.context_end)
    frame_time = 1.0 / max(fps, 1.0)
    sample_start = overlap_start + frame_time
    sample_end = overlap_end - frame_time
    if sample_end <= sample_start:
        raise ValueError("global chunk overlap is shorter than two frames")
    timestamps = np.linspace(sample_start, sample_end, SEAM_SAMPLES).tolist()
    samples = await asyncio.gather(
        *(
            asyncio.to_thread(
                lambda timestamp=timestamp: SeamFrames(
                    timestamp=timestamp,
                    left_before=_read_frame(
                        left_path,
                        timestamp - frame_time - left.context_start,
                    ),
                    left_at=_read_frame(left_path, timestamp - left.context_start),
                    right_at=_read_frame(right_path, timestamp - right.context_start),
                    right_after=_read_frame(
                        right_path,
                        timestamp + frame_time - right.context_start,
                    ),
                )
            )
            for timestamp in timestamps
        )
    )
    bbox = target_bbox(
        right.payload_json or {},
        (overlap_start + overlap_end) / 2,
    )
    try:
        return select_best_seam(list(samples), bbox=bbox)
    except ValueError as exc:
        raise GlobalSeamError(
            str(exc),
            retry_chunk_index=right.index,
        ) from exc


def _outer_report(report: ContinuityReport, boundaries: set[str]) -> ContinuityReport:
    issues = [
        issue
        for issue in report.issues
        if issue.boundary is None or issue.boundary in boundaries
    ]
    return ContinuityReport(
        passed=not issues,
        metrics=report.metrics,
        issues=issues,
        sampled_frames=report.sampled_frames,
    )


async def _validate_outer_boundaries(
    *,
    project: Project,
    chunks: list[GlobalGenerationChunk],
    paths: list[Path],
) -> dict:
    reports: dict[str, dict] = {}
    checks = [(0, {"pre"})]
    if len(chunks) == 1:
        checks[0][1].add("post")
    else:
        checks.append((len(chunks) - 1, {"post"}))
    original_source = await storage.materialize_source(
        project.video_path,
        project.video_url,
    )
    for index, boundaries in checks:
        chunk = chunks[index]
        source_path, _ = storage.new_path("clips", "mp4")
        await ffmpeg.extract_clip(
            original_source,
            chunk.context_start,
            chunk.context_end,
            source_path,
            with_audio=False,
        )
        bbox = target_bbox(
            chunk.payload_json or {},
            (chunk.edit_start + chunk.edit_end) / 2,
        )
        report = await validate_generated_continuity(
            source_path=source_path,
            generated_path=paths[index],
            window=GenerationWindow(
                core_start=chunk.edit_start,
                core_end=chunk.edit_end,
                context_start=chunk.context_start,
                context_end=chunk.context_end,
                adaptive=True,
            ),
            bbox=bbox,
        )
        filtered = _outer_report(report, boundaries)
        reports["entry" if "pre" in boundaries else "exit"] = filtered.metadata()
        if not filtered.passed:
            evidence = filtered.correction_evidence()
            raise GlobalSeamError(
                f"global occurrence outer continuity failed: {evidence}",
                retry_chunk_index=index,
            )
    return reports


async def assemble_global_occurrence(
    *,
    project: Project,
    occurrence: GlobalOccurrencePlan,
    chunks: list[GlobalGenerationChunk],
) -> AssemblyResult:
    ordered = sorted(chunks, key=lambda chunk: chunk.index)
    if not ordered or any(not chunk.output_url for chunk in ordered):
        raise ValueError("global occurrence has incomplete generated chunks")
    paths = [await storage.path_from_url(chunk.output_url or "") for chunk in ordered]
    continuity = await _validate_outer_boundaries(
        project=project,
        chunks=ordered,
        paths=paths,
    )
    seams = tuple(
        [
            await choose_chunk_seam(
                left,
                right,
                paths[index],
                paths[index + 1],
                fps=float(project.fps or 1.0),
            )
            for index, (left, right) in enumerate(
                zip(ordered, ordered[1:], strict=False)
            )
        ]
    )
    boundaries = [occurrence.edit_start, *(seam.timestamp for seam in seams), occurrence.edit_end]
    spans: list[Path] = []
    for index, chunk in enumerate(ordered):
        span_path, _ = storage.new_path("clips", "mp4")
        await ffmpeg.extract_clip(
            paths[index],
            boundaries[index] - chunk.context_start,
            boundaries[index + 1] - chunk.context_start,
            span_path,
            with_audio=False,
        )
        spans.append(span_path)
    video_path, _ = storage.new_path("variants", "mp4")
    await ffmpeg.concat_video_clips(spans, video_path)

    # Each chunk is intentionally video-only while we choose visual seams.
    # Restore one continuous original audio span after assembly, rather than
    # using provider audio or adding silence between chunk boundaries.
    original_source = await storage.materialize_source(
        project.video_path,
        project.video_url,
    )
    source_audio_path, _ = storage.new_path("clips", "mp4")
    await ffmpeg.extract_clip(
        original_source,
        occurrence.edit_start,
        occurrence.edit_end,
        source_audio_path,
        with_audio=True,
    )
    output_path, _ = storage.new_path("variants", "mp4")
    await ffmpeg.conform_generated_edit(
        video_path,
        source_audio_path,
        occurrence.edit_end - occurrence.edit_start,
        output_path,
    )
    output_url = await storage.publish(output_path, content_type="video/mp4")
    return AssemblyResult(output_url, seams, continuity)
