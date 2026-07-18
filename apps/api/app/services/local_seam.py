"""Find source-to-generated hard cuts inside protected context handles."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.services.generation_window import GenerationWindow
from app.services.continuity_validator import ContinuityIssue, ContinuityReport
from app.services.seam_matching import (
    MAX_SEAM_SCORE,
    SeamChoice,
    SeamFrames,
    rank_best_seam,
)


LOCAL_SEAM_SAMPLES = 9


@dataclass(frozen=True, slots=True)
class LocalSeamSelection:
    entry: SeamChoice | None
    exit: SeamChoice | None
    context_start: float
    context_duration: float
    max_score: float = MAX_SEAM_SCORE
    target_weighting: str = "selection_bbox"

    @property
    def passed(self) -> bool:
        return all(
            choice is None or choice.score <= self.max_score
            for choice in (self.entry, self.exit)
        )

    @property
    def media_start(self) -> float:
        return self.entry.timestamp if self.entry is not None else 0.0

    @property
    def media_end(self) -> float:
        return self.exit.timestamp if self.exit is not None else self.context_duration

    @property
    def timeline_start(self) -> float:
        return self.context_start + self.media_start

    @property
    def timeline_end(self) -> float:
        return self.context_start + self.media_end

    @property
    def issues(self) -> tuple[str, ...]:
        output = []
        for boundary, choice in (("entry", self.entry), ("exit", self.exit)):
            if choice is not None and choice.score > self.max_score:
                output.append(
                    f"{boundary}_frame_match_score at {boundary}: "
                    f"measured {choice.score:.3f}, limit {self.max_score:.3f}"
                )
        return tuple(output)

    def metadata(self) -> dict:
        def boundary(choice: SeamChoice | None) -> dict | None:
            if choice is None:
                return None
            return {
                "media_timestamp": choice.timestamp,
                "source_timestamp": self.context_start + choice.timestamp,
                "score": choice.score,
                "samples": choice.samples,
                "safe": choice.score <= self.max_score,
            }

        return {
            "passed": self.passed,
            "max_score": self.max_score,
            "entry": boundary(self.entry),
            "exit": boundary(self.exit),
            "media_start": self.media_start,
            "media_end": self.media_end,
            "timeline_start": self.timeline_start,
            "timeline_end": self.timeline_end,
            "issues": list(self.issues),
            "target_weighting": self.target_weighting,
        }


def _tracked_bbox_resolver(
    tracked_frames: list[dict],
    *,
    context_start: float,
):
    usable = [
        frame
        for frame in tracked_frames
        if frame.get("state") == "tracked" and isinstance(frame.get("bbox"), dict)
    ]
    if not usable:
        return None

    def resolve(local_timestamp: float) -> dict[str, float]:
        absolute_timestamp = context_start + local_timestamp
        nearest = min(
            usable,
            key=lambda frame: abs(
                float(frame.get("timestamp") or 0.0) - absolute_timestamp
            ),
        )
        return {
            key: float(nearest["bbox"].get(key, 0.0))
            for key in ("x", "y", "w", "h")
        }

    return resolve


def select_local_seams(
    *,
    entry_samples: list[SeamFrames],
    exit_samples: list[SeamFrames],
    bbox: dict[str, float],
    window: GenerationWindow,
    tracked_frames: list[dict] | None = None,
) -> LocalSeamSelection:
    bbox_for_timestamp = _tracked_bbox_resolver(
        tracked_frames or [],
        context_start=window.context_start,
    )
    entry = (
        rank_best_seam(
            entry_samples,
            bbox=bbox,
            bbox_for_timestamp=bbox_for_timestamp,
            prefer_late=True,
        )
        if entry_samples
        else None
    )
    exit = (
        rank_best_seam(
            exit_samples,
            bbox=bbox,
            bbox_for_timestamp=bbox_for_timestamp,
        )
        if exit_samples
        else None
    )
    return LocalSeamSelection(
        entry=entry,
        exit=exit,
        context_start=window.context_start,
        context_duration=window.context_duration,
        target_weighting=("tracked_bbox" if bbox_for_timestamp is not None else "selection_bbox"),
    )


def _candidate_times(start: float, end: float, fps: float) -> list[float]:
    step = max(1.0 / max(fps, 1.0), 0.04)
    lower = start + step
    # Every candidate also reads `timestamp + step`. Keep that read strictly
    # before the media duration; seeking to the exact duration has no frame.
    upper = end - 2 * step
    if upper < lower:
        return []
    return np.linspace(lower, upper, LOCAL_SEAM_SAMPLES).tolist()


def _read(capture: cv2.VideoCapture, timestamp: float) -> np.ndarray:
    capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
    ok, frame = capture.read()
    if not ok or frame is None:
        raise ValueError(f"could not read seam frame at {timestamp:.3f}s")
    return frame


def _match_local_context_sync(
    source_path: Path,
    generated_path: Path,
    window: GenerationWindow,
    bbox: dict[str, float],
    tracked_frames: list[dict] | None,
) -> LocalSeamSelection:
    source = cv2.VideoCapture(str(source_path))
    generated = cv2.VideoCapture(str(generated_path))
    if not source.isOpened() or not generated.isOpened():
        source.release()
        generated.release()
        raise ValueError("could not open source or generated video for seam matching")
    try:
        fps = float(source.get(cv2.CAP_PROP_FPS) or generated.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0:
            raise ValueError("seam matching requires a valid frame rate")
        step = max(1.0 / fps, 0.04)
        entry_samples = [
            SeamFrames(
                timestamp=timestamp,
                left_before=_read(source, timestamp - step),
                left_at=_read(source, timestamp),
                right_at=_read(generated, timestamp),
                right_after=_read(generated, timestamp + step),
            )
            for timestamp in _candidate_times(0.0, window.edit_start_offset, fps)
        ]
        exit_samples = [
            SeamFrames(
                timestamp=timestamp,
                left_before=_read(generated, timestamp - step),
                left_at=_read(generated, timestamp),
                right_at=_read(source, timestamp),
                right_after=_read(source, timestamp + step),
            )
            for timestamp in _candidate_times(
                window.edit_end_offset,
                window.context_duration,
                fps,
            )
        ]
    finally:
        source.release()
        generated.release()
    return select_local_seams(
        entry_samples=entry_samples,
        exit_samples=exit_samples,
        bbox=bbox,
        window=window,
        tracked_frames=tracked_frames,
    )


async def match_local_context(
    *,
    source_path: Path,
    generated_path: Path,
    window: GenerationWindow,
    bbox: dict[str, float],
    tracked_frames: list[dict] | None = None,
) -> LocalSeamSelection:
    return await asyncio.to_thread(
        _match_local_context_sync,
        source_path,
        generated_path,
        window,
        bbox,
        tracked_frames,
    )


def continuity_at_selected_seams(
    base: ContinuityReport,
    selection: LocalSeamSelection,
) -> ContinuityReport:
    """Keep media-integrity failures and replace nominal cuts with chosen cuts."""
    issues = [
        issue
        for issue in base.issues
        if issue.code in {"duration_delta_s", "fps_delta_ratio", "frozen_tail"}
    ]
    metrics = dict(base.metrics)
    for boundary, choice in (("entry", selection.entry), ("exit", selection.exit)):
        if choice is None:
            continue
        code = f"{boundary}_frame_match_score"
        metrics[code] = choice.score
        if choice.score > selection.max_score:
            issues.append(
                ContinuityIssue(
                    code,
                    choice.score,
                    selection.max_score,
                    boundary,
                )
            )
    return ContinuityReport(
        passed=not issues,
        metrics=metrics,
        issues=issues,
        sampled_frames=base.sampled_frames
        + sum(choice.samples for choice in (selection.entry, selection.exit) if choice),
    )
