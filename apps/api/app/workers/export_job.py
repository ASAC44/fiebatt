"""Export worker.

Renders the final MP4 by compositing the project's timeline from scratch:
original stretches extracted from the source video + generated variant
clips, each normalized to the project's fps + resolution so the concat
demuxer can stitch them without re-encoding the seams.

This is the *only* place where a full-project re-encode happens. Accept
and propagate just write Segment rows; we do the real ffmpeg work here.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.models.job import Job
from app.models.project import Project
from app.services import color as color_svc, ffmpeg, storage
from app.services.ffmpeg import _run  # type: ignore[attr-defined]
from app.services.ffmpeg import concat_clips
from app.services.timeline_builder import TimelineItem, build_timeline
from app.schemas.timeline import PersistedEDL

log = logging.getLogger("fiebatt.jobs.export")


async def _render_clip(
    src: Path,
    *,
    source_start: float,
    source_end: float,
    volume: float,
    target_w: int,
    target_h: int,
    target_fps: float,
    out: Path,
) -> None:
    """Produce a unit-codec MP4 for one clip span.

    Takes source-file times (not timeline times) — caller is responsible
    for knowing how deep into the source to seek. Every output is h264 +
    aac + exact target w/h/fps so concat_mp4s can glue them without
    re-encoding the seams. The scale filter letterboxes rather than crop
    so aspect-mismatched AI variants don't distort.

    volume: 0.0 silences the clip, 1.0 plays as-is, anything in between
    attenuates. Silenced clips still get a padded silent track so concat
    doesn't choke on missing streams.
    """
    duration = max(0.0, source_end - source_start)
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        # Some uploads (including the reported file) have audio that runs
        # past the final video frame.  Clone the last frame until the exact
        # requested span end so concat never creates an audio-only tail.
        f"tpad=stop_mode=clone:stop_duration={duration:.4f},"
        f"fps={target_fps:.4f}"
    )
    # Keep every rendered part's audio and video exactly the same duration.
    # `apad` only pads an existing audio stream; it does not create one when
    # an AI file has no audio.  Feed generated clips an explicit silent track
    # so concat/xfade never has to reconcile different stream lengths.
    af_parts: list[str] = []
    if volume < 0.999:
        af_parts.append(f"volume={max(0.0, min(1.0, volume)):.3f}")
    af_parts.append("apad")
    af = ",".join(af_parts)

    cmd = ["ffmpeg", "-y", "-ss", f"{source_start:.3f}", "-i", str(src)]
    if volume < 0.999:
        # The generated input may not contain audio at all, so an audio
        # filter cannot manufacture the stream. Add a deterministic silent
        # source and map it instead of relying on provider output.
        cmd.extend([
            "-f", "lavfi", "-t", f"{duration:.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map", "0:v:0", "-map", "1:a:0",
        ])
    else:
        cmd.extend(["-map", "0:v:0", "-map", "0:a:0?", "-af", af])
    cmd.extend([
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        str(out),
    ])
    await _run(cmd)


async def _render_span(
    src: Path,
    *,
    span_start: float,
    span_end: float,
    target_w: int,
    target_h: int,
    target_fps: float,
    out: Path,
    is_generated: bool,
    media_start: float,
    media_end: float,
) -> None:
    """Render one timeline span to a normalized unit-codec MP4.

    Generated clips are rendered from their own file (0→duration) with
    silenced audio — AI-generated audio never sounds like the original
    and would create audible artifacts during the dissolve transition.
    Original spans seek into the source video with full audio.
    """
    if is_generated:
        source_start = media_start
        source_end = media_end
        volume = 0.0
    else:
        source_start = span_start
        source_end = span_end
        volume = 1.0
    await _render_clip(
        src,
        source_start=source_start,
        source_end=source_end,
        volume=volume,
        target_w=target_w,
        target_h=target_h,
        target_fps=target_fps,
        out=out,
    )


async def _render_edl(
    edl: PersistedEDL,
    proj: Project,
) -> Path:
    """Render a saved EDL snapshot — clip-by-clip, honoring splits/trims,
    reorders, and per-clip volume. This is the path taken whenever the
    user has touched the timeline in the studio; the DB's timeline_edl
    column is the source of truth for shape, segments table is only used
    as an index into which variant files exist."""
    scratch_parts: list[Path] = []
    render_id = uuid.uuid4().hex[:12]
    original_source = await storage.materialize_source(proj.video_path, proj.video_url)

    for i, clip in enumerate(edl.clips):
        if clip.source_end - clip.source_start < 0.02:
            # user trimmed almost to zero — skip to avoid zero-length mp4s
            continue
        part_path = storage.path_for("exports", f"_part_{render_id}_{i:04d}.mp4")

        # prefer the local scratch copy of the project's own video when
        # the clip points at it (spares us a re-download per clip). every
        # other source (generated variant, uploaded library asset) goes
        # through path_from_url which caches to scratch.
        src = original_source if clip.url == proj.video_url else None
        if src is None or not src.exists():
            src = await storage.path_from_url(clip.url)

        volume = 0.0 if clip.kind == "generated" else clip.volume
        await _render_clip(
            src,
            source_start=clip.source_start,
            source_end=clip.source_end,
            volume=volume,
            target_w=proj.width or 1280,
            target_h=proj.height or 720,
            target_fps=proj.fps or 24.0,
            out=part_path,
        )
        scratch_parts.append(part_path)

    out_path, _ = storage.new_path("exports", "mp4")
    if not scratch_parts:
        raise RuntimeError("edl rendered to zero clips — nothing to export")
    if len(scratch_parts) == 1:
        scratch_parts[0].rename(out_path)
    else:
        # Do not blend generated/source boundaries. If the generated frame
        # differs from the original, even a very short fade reads as ghosting.
        transitions = [0.0] * (len(scratch_parts) - 1)
        await concat_clips(scratch_parts, out_path, transitions=transitions)
        for p in scratch_parts:
            try:
                p.unlink()
            except OSError:
                pass
    return out_path


async def _render_timeline(
    items: list[TimelineItem],
    proj: Project,
) -> Path:
    """Materialize the ordered timeline items into a single MP4."""
    scratch_parts: list[Path] = []
    render_id = uuid.uuid4().hex[:12]
    original_source = await storage.materialize_source(proj.video_path, proj.video_url)

    for i, item in enumerate(items):
        part_path = storage.path_for("exports", f"_part_{render_id}_{i:04d}.mp4")
        is_generated = item.source == "generated"

        if is_generated:
            src = await storage.path_from_url(item.url)
        else:
            # originals always come from the project's source video
            src = original_source

        await _render_span(
            src,
            span_start=item.start_ts,
            span_end=item.end_ts,
            target_w=proj.width or 1280,
            target_h=proj.height or 720,
            target_fps=proj.fps or 24.0,
            out=part_path,
            is_generated=is_generated,
            media_start=item.media_start_ts,
            media_end=item.media_end_ts,
        )

        # color-match generated clips to the original footage at their
        # trailing boundary so the transition seam is less visible
        if is_generated and item.end_ts < proj.duration - 0.1:
            ref_frame: Path | None = None
            try:
                ref_frame = storage.path_for("exports", f"_ref_{render_id}_{i:04d}.jpg")
                ref_ts = min(item.end_ts + 0.1, proj.duration - 0.1)
                await ffmpeg.extract_frame(original_source, ref_ts, ref_frame)
                matched = storage.path_for("exports", f"_matched_{render_id}_{i:04d}.mp4")
                await color_svc.match_color_histogram(part_path, ref_frame, matched)
                part_path = matched
            except Exception:
                log.exception("color matching failed for generated clip — using ungraded version")
            finally:
                if ref_frame is not None:
                    try:
                        ref_frame.unlink()
                    except OSError:
                        pass

        scratch_parts.append(part_path)

    out_path, _ = storage.new_path("exports", "mp4")

    if len(scratch_parts) == 1:
        scratch_parts[0].rename(out_path)
    else:
        # Do not blend generated/source boundaries. The provider output must
        # match the neighboring frames; export should not add ghost frames.
        transitions = [0.0] * (len(scratch_parts) - 1)
        await concat_clips(scratch_parts, out_path, transitions=transitions)
        # clean up intermediates immediately; they're no longer needed
        for p in scratch_parts:
            try:
                p.unlink()
            except OSError:
                pass

    return out_path


async def run(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        proj = await db.get(Project, job.project_id)
        if proj is None:
            job.status = "error"
            job.error = "project missing"
            await db.commit()
            return
        job.status = "processing"
        await db.commit()

        # Prefer the saved EDL when present — this is what the user saw
        # in the studio, complete with splits/trims/reorders/volume. Fall
        # back to the segment-based reconstruction for legacy reels that
        # were never touched after upload.
        edl_blob = proj.timeline_edl
        edl: PersistedEDL | None = None
        if edl_blob:
            try:
                edl = PersistedEDL.model_validate(edl_blob)
            except Exception:
                log.warning("project %s has malformed timeline_edl; falling back to segments", proj.id)
                edl = None

        items: list[TimelineItem] | None = None
        if edl is None:
            items = await build_timeline(db, proj)

        proj_copy = proj  # keep the loaded instance for ffmpeg params

    try:
        if edl is not None:
            out_path = await _render_edl(edl, proj_copy)
        else:
            assert items is not None
            out_path = await _render_timeline(items, proj_copy)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("export failed")
        async with AsyncSessionLocal() as db:
            j = await db.get(Job, job_id)
            if j:
                j.status = "error"
                j.error = f"export failed: {e}"
                await db.commit()
        return

    try:
        out_url = await storage.publish(out_path, content_type="video/mp4")
    except Exception as e:
        log.exception("export publish failed")
        async with AsyncSessionLocal() as db:
            j = await db.get(Job, job_id)
            if j:
                j.status = "error"
                j.error = f"export publish failed: {e}"
                await db.commit()
        return

    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        if j:
            payload = dict(j.payload or {})
            payload["export_url"] = out_url
            j.payload = payload
            j.status = "done"
            await db.commit()
