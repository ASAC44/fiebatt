"""Agent tool execution layer.

Each tool handler mirrors the logic from the corresponding API route
but operates directly on the DB session instead of going through HTTP.
This keeps the agent's tool calls in-process and avoids auth round-trips.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.settings import get_settings as _get_backend_settings
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.segment import Segment
from app.models.timeline_snapshot import TimelineSnapshot
from app.services import color as color_service
from app.services import ffmpeg, storage
from app.services.timeline_builder import build_timeline
from google import genai
from google.genai import types

log = logging.getLogger("fiebatt.agent_tools")

MIN_SEG_LEN = 2.0
MAX_SEG_LEN = 15.0
MAX_BATCH_EDITS = 10
GEMINI_MODEL = "gemini-2.5-flash"
FRAME_COUNT = 5

# ---- dispatcher ----

TOOL_HANDLERS: dict[str, Any] = {}


def _register(name: str):
    """Decorator to register a tool handler by name."""
    def decorator(fn):
        TOOL_HANDLERS[name] = fn
        return fn
    return decorator


async def execute_tool(
    tool_name: str,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Dispatch a tool call to the appropriate handler.

    Args:
        tool_name: Name of the tool to execute.
        args: Tool arguments from the Gemini function call.
        db: Active async DB session.
        session_id: Current user session ID for ownership checks.
        runner: Optional JobRunner for submitting background tasks.

    Returns:
        Dict with the tool execution result.

    Raises:
        ValueError: If the tool name is not recognized.
    """
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        raise ValueError(f"unknown tool: {tool_name}")
    return await handler(args=args, db=db, session_id=session_id, runner=runner)


# ---- ownership check helper ----

async def _get_project_or_error(
    db: AsyncSession,
    project_id: str,
    session_id: str,
) -> Project:
    """Fetch a project and verify session ownership.

    Project access is scoped to the resolved session id.
    """
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session_id:
        raise ValueError(f"project not found or access denied: {project_id}")
    return proj


async def _get_segment_or_error(
    db: AsyncSession,
    project_id: str,
    segment_id: str,
) -> Segment:
    segment = await db.get(Segment, segment_id)
    if segment is None or segment.project_id != project_id:
        raise ValueError(f"segment not found: {segment_id}")
    return segment


async def _get_owned_segment_or_error(
    db: AsyncSession,
    segment_id: str,
    session_id: str,
) -> tuple[Segment, Project]:
    row = (
        await db.execute(
            select(Segment, Project)
            .join(Project, Project.id == Segment.project_id)
            .where(
                Segment.id == segment_id,
                Project.session_id == session_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise ValueError(f"segment not found: {segment_id}")
    segment, project = row
    return segment, project


async def _get_variant_for_session_or_error(
    db: AsyncSession,
    variant_id: str,
    session_id: str,
) -> Variant:
    variant = (
        await db.execute(
            select(Variant)
            .where(Variant.id == variant_id)
            .options(
                selectinload(Variant.job).selectinload(Job.project),
            )
        )
    ).scalar_one_or_none()
    if variant is None or variant.job is None or variant.job.project is None:
        raise ValueError(f"variant not found: {variant_id}")
    if variant.job.project.session_id != session_id:
        raise ValueError(f"variant not found: {variant_id}")
    return variant


def _segment_dict(segment: Segment) -> dict[str, Any]:
    return {
        "id": segment.id,
        "project_id": segment.project_id,
        "start_ts": segment.start_ts,
        "end_ts": segment.end_ts,
        "source": segment.source,
        "url": storage.normalize_url_like(segment.url, fallback=segment.url),
        "variant_id": segment.variant_id,
        "order_index": segment.order_index,
        "active": segment.active,
    }


def _snapshot_payload(segment: Segment) -> dict[str, Any]:
    return {
        "project_id": segment.project_id,
        "start_ts": segment.start_ts,
        "end_ts": segment.end_ts,
        "source": segment.source,
        "url": segment.url,
        "variant_id": segment.variant_id,
        "order_index": segment.order_index,
        "active": segment.active,
    }


def _validate_preview_timestamp(ts: float, duration: float) -> None:
    if ts < 0:
        raise ValueError("ts must be >= 0")
    if ts > duration + 1e-3:
        raise ValueError("ts past project duration")


def _validate_preview_bounds(start: float, end: float, duration: float) -> None:
    if end <= start:
        raise ValueError("end must be greater than start")
    if start < 0:
        raise ValueError("start must be >= 0")
    if end > duration + 1e-3:
        raise ValueError("end past project duration")


def _validate_segment_length(start_ts: float, end_ts: float) -> None:
    length = end_ts - start_ts
    if length < MIN_SEG_LEN or length > MAX_SEG_LEN:
        raise ValueError(
            f"segment length must be {MIN_SEG_LEN}-{MAX_SEG_LEN}s (got {length:.2f}s)"
        )


def _validate_bbox_bounds(bbox: dict[str, Any]) -> None:
    x = float(bbox.get("x", 0.0))
    y = float(bbox.get("y", 0.0))
    w = float(bbox.get("w", 0.0))
    h = float(bbox.get("h", 0.0))
    if x + w > 1.0001 or y + h > 1.0001:
        raise ValueError("bbox extends outside the frame")


def _find_timeline_item(items: list[Any], ts: float) -> Any:
    for item in items:
        if item.start_ts - 1e-3 <= ts < item.end_ts - 1e-3:
            return item
    if items and abs(ts - items[-1].end_ts) <= 1e-3:
        return items[-1]
    raise ValueError("timeline item not found")


async def _resolve_timeline_source_path(
    proj: Project,
    item: Any,
) -> Path:
    if item.source == "generated":
        return await storage.path_from_url(item.url)

    src = Path(proj.video_path)
    if src.exists():
        return src
    return await storage.path_from_url(proj.video_url)


async def _extract_preview_frame_result(
    proj: Project,
    items: list[Any],
    ts: float,
) -> dict[str, Any]:
    item = _find_timeline_item(items, ts)
    src = await _resolve_timeline_source_path(proj, item)
    if item.source == "generated":
        frame_ts = max(0.0, ts - item.start_ts)
        frame_ts = min(frame_ts, max(0.0, item.duration - 1e-3))
    else:
        frame_ts = min(ts, max(0.0, proj.duration - 1e-3))

    frame_path, _ = storage.new_path("previews", "jpg")
    await ffmpeg.extract_frame(src, frame_ts, frame_path)
    frame_url = await storage.publish(frame_path, content_type="image/jpeg")
    return {"ts": ts, "url": frame_url}


async def _deactivate_overlapping_generated_segments(
    db: AsyncSession,
    segment: Segment,
) -> None:
    overlapping = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == segment.project_id,
                Segment.active == True,  # noqa: E712
                Segment.source == "generated",
                Segment.start_ts < segment.end_ts,
                Segment.end_ts > segment.start_ts,
            )
        )
    ).scalars().all()
    for existing in overlapping:
        existing.active = False


def _get_gemini_client() -> genai.Client:
    from app.config.settings import get_settings
    settings = get_settings()
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")
    return genai.Client(api_key=settings.gemini_api_key)


def _image_part(path: Path) -> types.Part:
    suffix = path.suffix.lower()
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "image/png")
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type)


def _score_prompt(compare_to: str, prompt: str) -> str:
    if compare_to == "original":
        comparison_instruction = (
            "you are also given reference frames from the original source segment. "
            "judge whether the variant preserves scene structure and improves the edit "
            "without introducing new artifacts or continuity breaks."
        )
    else:
        comparison_instruction = (
            "judge the variant directly against the user prompt without assuming access "
            "to the original source segment."
        )

    return (
        "you are a video quality rater for short generated clips. "
        f"{comparison_instruction} "
        "return strict json only using this schema: "
        "{"
        '"visual_coherence":{"score":float,"issues":[str]},'
        '"prompt_adherence":{"score":float,"misses":[str]},'
        '"temporal_consistency":{"score":float,"flicker_detected":bool},'
        '"edge_quality":{"score":float,"issues":[str]},'
        '"overall":float,'
        '"recommendation":"accept"|"remix"|"reject"'
        "}. "
        "all scores must be floats from 0.0 to 10.0. "
        "keep issue and miss lists concise and concrete. "
        f"user prompt: {prompt}"
    )


def _continuity_prompt(boundary_ts: float) -> str:
    return (
        "you are judging continuity across a video edit boundary. "
        f"the first image is the last frame before the cut near t={boundary_ts:.3f}s. "
        "the second image is the first frame after the cut. "
        "return strict json only using this schema: "
        '{'
        '"score":float,'
        '"issues":[{"type":str,"severity":float}]'
        "}. "
        "scores and severities must be floats from 0.0 to 10.0. "
        "only include issues for meaningful continuity problems like jump cuts, "
        "subject mismatch, lighting change, color shift, framing mismatch, or object pop."
    )


async def _generate_json(
    *,
    client: genai.Client,
    prompt_text: str,
    frame_paths: list[Path],
) -> dict[str, Any]:
    contents = [types.Part.from_text(text=prompt_text)]
    contents.extend(_image_part(path) for path in frame_paths)

    response = await client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(response.text)


async def _sample_video_frames(
    *,
    src: Path,
    temp_dir: Path,
    prefix: str,
    num_frames: int,
    start_ts: float = 0.0,
    end_ts: float | None = None,
) -> list[Path]:
    probe_data = await ffmpeg.probe(src)
    duration = float(probe_data.get("duration", 0.0))
    sample_start = max(0.0, start_ts)
    sample_end = duration if end_ts is None else min(float(end_ts), duration)
    if sample_end <= sample_start:
        sample_end = max(sample_start + 1e-3, duration)

    span = max(sample_end - sample_start, 1e-3)
    frame_paths: list[Path] = []
    for index in range(num_frames):
        ts = sample_start + ((index + 0.5) * span / num_frames)
        ts = min(max(sample_start, ts), max(sample_start, sample_end - 1e-3))
        out = temp_dir / f"{prefix}_{index:02d}.png"
        await ffmpeg.extract_frame(src, ts, out)
        frame_paths.append(out)
    return frame_paths


async def _boundary_frame(
    *,
    proj: Project,
    item: Any,
    is_end: bool,
    output_path: Path,
) -> Path:
    clip_path = await _resolve_timeline_source_path(proj, item)
    if not clip_path.exists():
        raise ValueError("timeline source video not found")

    epsilon = min(0.04, max(item.duration / 10.0, 0.001))
    if item.source == "original":
        ts = max(0.0, item.end_ts - epsilon) if is_end else max(0.0, item.start_ts)
    else:
        ts = max(0.0, item.duration - epsilon) if is_end else 0.0
    return await ffmpeg.extract_frame(clip_path, ts, output_path)


# ---- tool handlers ----

@_register("analyze_video")
async def _analyze_video(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Stub: analyze video content by sampling frames.

    The real multi-agent analysis pipeline will replace this later.
    For now, return project metadata as a placeholder.
    """
    project_id: str = args["project_id"]
    fps: float = args.get("fps", 1.0)

    proj = await _get_project_or_error(db, project_id, session_id)

    return {
        "status": "done",
        "project_id": proj.id,
        "duration": proj.duration,
        "fps_sampled": fps,
        "frame_count": int(proj.duration * fps),
        "analysis": {
            "description": (
                "Video analysis is a placeholder. The full multi-agent pipeline "
                "will extract frames, identify scenes, objects, entities, mood, "
                "and lighting in a future update."
            ),
            "scenes": [],
            "entities": [],
        },
    }


@_register("identify_region")
async def _identify_region(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Identify what entity is inside a bounding box at a given timestamp.

    Mirrors the logic of POST /api/identify.
    """
    from app.ai.services import gemini
    from app.config.settings import get_settings

    project_id: str = args["project_id"]
    frame_ts: float = args["frame_ts"]
    bbox: dict[str, float] = args["bbox"]

    proj = await _get_project_or_error(db, project_id, session_id)

    if frame_ts > proj.duration + 1e-3:
        raise ValueError(f"frame_ts {frame_ts} exceeds project duration {proj.duration}")

    if bbox.get("x", 0) + bbox.get("w", 0) > 1.0001 or bbox.get("y", 0) + bbox.get("h", 0) > 1.0001:
        raise ValueError("bbox extends outside the frame")

    settings = get_settings()
    frames_dir = settings.storage_path / "identify_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    frame_path = str(frames_dir / f"{proj.id}_{frame_ts:.3f}.png")
    try:
        await ffmpeg.extract_frame(proj.video_path, frame_ts, frame_path)
    except Exception as exc:
        raise ValueError(f"frame extraction failed: {exc}") from exc

    try:
        crop_path = await ffmpeg.crop_bbox_from_frame(frame_path, bbox)
    except Exception as exc:
        raise ValueError(f"bbox crop failed: {exc}") from exc

    try:
        entity = await gemini.identify_entity(crop_path)
    except Exception as exc:
        raise ValueError(f"entity identification failed: {exc}") from exc

    # cleanup temp files
    for path in (frame_path, crop_path):
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

    return {
        "description": entity.get("description", ""),
        "category": entity.get("category", ""),
        "attributes": entity.get("attributes", {}),
    }


@_register("generate_edit")
async def _generate_edit(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Create a generate job — mirrors POST /api/generate logic."""
    from app.workers import generate_job as generate_worker

    project_id: str = args["project_id"]
    start_ts: float = args["start_ts"]
    end_ts: float = args["end_ts"]
    bbox: dict[str, float] = args["bbox"]
    prompt: str = args["prompt"]
    reference_frame_ts: float = args.get("reference_frame_ts", start_ts)
    video_gen_provider = args.get("video_gen_provider")

    proj = await _get_project_or_error(db, project_id, session_id)

    length = end_ts - start_ts
    if length < MIN_SEG_LEN or length > MAX_SEG_LEN:
        raise ValueError(f"segment length must be {MIN_SEG_LEN}-{MAX_SEG_LEN}s (got {length:.2f}s)")
    if end_ts > proj.duration + 1e-3:
        raise ValueError("end_ts past project duration")
    if video_gen_provider and video_gen_provider != "auto":
        from app.ai.services.provider_capabilities import validate_provider_duration

        provider_error = validate_provider_duration(video_gen_provider, length)
        if provider_error:
            raise ValueError(provider_error)
    if bbox.get("x", 0) + bbox.get("w", 0) > 1.0001 or bbox.get("y", 0) + bbox.get("h", 0) > 1.0001:
        raise ValueError("bbox extends outside the frame")

    job = Job(
        project_id=proj.id,
        kind="generate",
        status="pending",
        start_ts=start_ts,
        end_ts=end_ts,
        bbox_json=bbox,
        prompt=prompt,
        reference_frame_ts=reference_frame_ts,
        payload={"video_gen_provider": video_gen_provider or "auto"},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    if runner is not None:
        runner.submit(job.id, lambda: generate_worker.run(job.id))

    return {
        "job_id": job.id,
        "status": "pending",
        "message": f"Generation job created for segment {start_ts:.1f}s–{end_ts:.1f}s",
    }


@_register("get_job_status")
async def _get_job_status(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Fetch job status + variants — mirrors GET /api/jobs/{job_id}."""
    job_id: str = args["job_id"]

    job = (
        await db.execute(
            select(Job).where(Job.id == job_id).options(selectinload(Job.variants))
        )
    ).scalar_one_or_none()
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    proj = await db.get(Project, job.project_id)
    if proj is None or proj.session_id != session_id:
        raise ValueError(f"job not found: {job_id}")

    variants = [
        {
            "id": v.id,
            "index": v.index,
            "status": v.status,
            "url": v.url,
            "description": v.description,
            "visual_coherence": v.visual_coherence,
            "prompt_adherence": v.prompt_adherence,
            "error": v.error,
        }
        for v in sorted(job.variants, key=lambda v: v.index)
    ]

    return {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "error": job.error,
        "variants": variants,
    }


@_register("wait_for_job")
async def _wait_for_job(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Poll a job row until it finishes or a timeout elapses.

    The agent calls this right after ``generate_edit`` so a single tool
    turn blocks through the whole render. Without it Gemini would return
    to the user saying "I kicked it off" and the variants would never
    surface unless the user nudged the chat again.

    We poll every second up to ``timeout_s`` (default 180s). Returns the
    same shape as ``get_job_status`` so Gemini can read variants directly.
    """
    from app.db.session import AsyncSessionLocal

    job_id: str = args["job_id"]
    timeout_s: float = float(args.get("timeout_s", 180.0))
    poll_interval_s: float = 1.0

    log.info(
        "[wait_for_job] START job_id=%s timeout_s=%.1f session=%s",
        job_id, timeout_s, session_id[:8] + "…" if session_id else "?",
    )

    elapsed = 0.0
    last_status_logged: str | None = None
    while elapsed < timeout_s:
        # Poll using a *fresh* session every iteration. The outer `db`
        # session is shared with the agent stream loop; if we expire it
        # to re-read the job row, other objects (e.g. the Conversation
        # we persist at the end of the stream) get expired too and
        # blow up with MissingGreenlet when something touches them
        # after the async boundary.
        async with AsyncSessionLocal() as poll_db:
            job = (
                await poll_db.execute(
                    select(Job)
                    .where(Job.id == job_id)
                    .options(selectinload(Job.variants))
                )
            ).scalar_one_or_none()
            if job is None:
                log.error("[wait_for_job] job_id=%s NOT FOUND in db", job_id)
                raise ValueError(f"job not found: {job_id}")

            proj = await poll_db.get(Project, job.project_id)
            if proj is None or proj.session_id != session_id:
                log.error(
                    "[wait_for_job] job_id=%s session mismatch: proj=%s job.session=%s req.session=%s",
                    job_id,
                    bool(proj),
                    proj.session_id if proj else None,
                    session_id,
                )
                raise ValueError(f"job not found: {job_id}")

            # Only log when something actually changes so we don't spam
            # a line every single second for a 3min render.
            v_count = len(job.variants)
            v_done = sum(1 for v in job.variants if v.status == "done")
            v_err = sum(1 for v in job.variants if v.status == "error")
            v_url = sum(1 for v in job.variants if v.url)
            snapshot = f"{job.status}|{v_count}v|{v_done}done|{v_err}err|{v_url}url"
            if snapshot != last_status_logged:
                log.info(
                    "[wait_for_job] job_id=%s t=%.0fs %s",
                    job_id, elapsed, snapshot,
                )
                last_status_logged = snapshot

            if job.status in ("done", "error", "failed", "cancelled"):
                variants = [
                    {
                        "id": v.id,
                        "index": v.index,
                        "status": v.status,
                        "url": v.url,
                        "description": v.description,
                        "visual_coherence": v.visual_coherence,
                        "prompt_adherence": v.prompt_adherence,
                        "error": v.error,
                    }
                    for v in sorted(job.variants, key=lambda v: v.index)
                ]
                # Summarize the useful variant count so callers (and the
                # agent loop) can distinguish a successful render from
                # one that left nothing usable behind (e.g. generation quota exhausted).
                ready_urls = [v for v in variants if v.get("url")]
                variant_errors = [
                    v.get("error") for v in variants if v.get("status") == "error" and v.get("error")
                ]
                log.info(
                    "[wait_for_job] DONE job_id=%s status=%s waited=%.1fs ready=%d/%d errors=%d",
                    job_id,
                    job.status,
                    elapsed,
                    len(ready_urls),
                    len(variants),
                    len(variant_errors),
                )
                if variant_errors:
                    for i, err in enumerate(variant_errors):
                        log.warning(
                            "[wait_for_job]   variant[%d] error: %s", i, err,
                        )
                return {
                    "job_id": job.id,
                    "kind": job.kind,
                    "status": job.status,
                    "error": job.error,
                    "variants": variants,
                    "variants_ready": len(ready_urls),
                    "variant_errors": variant_errors,
                    "waited_s": elapsed,
                }

        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s

    log.warning(
        "[wait_for_job] TIMEOUT job_id=%s after %.0fs", job_id, elapsed,
    )
    return {
        "job_id": job_id,
        "status": "timeout",
        "waited_s": elapsed,
        "message": f"Job did not finish within {timeout_s}s.",
    }


@_register("accept_variant")
async def _accept_variant(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Accept a variant and apply it to the timeline — mirrors POST /api/accept."""
    from app.workers import entity_job

    job_id: str = args["job_id"]
    variant_index: int = args.get("variant_index", 0)
    discover_occurrences: bool = bool(args.get("discover_occurrences", False))

    job = (
        await db.execute(
            select(Job).where(Job.id == job_id).options(selectinload(Job.variants))
        )
    ).scalar_one_or_none()
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    proj = await db.get(Project, job.project_id)
    if proj is None or proj.session_id != session_id:
        raise ValueError(f"job not found: {job_id}")

    variant: Variant | None = next(
        (v for v in job.variants if v.index == variant_index), None
    )
    if variant is None or variant.status != "done" or not variant.url:
        raise ValueError("variant not ready")

    if job.start_ts is None or job.end_ts is None:
        raise ValueError("job has no segment range")

    # deactivate overlapping generated segments
    overlapping = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == proj.id,
                Segment.active == True,  # noqa: E712
                Segment.source == "generated",
                Segment.start_ts < job.end_ts,
                Segment.end_ts > job.start_ts,
            )
        )
    ).scalars().all()
    for s in overlapping:
        s.active = False

    seg = Segment(
        project_id=proj.id,
        start_ts=job.start_ts,
        end_ts=job.end_ts,
        source="generated",
        url=variant.url,
        variant_id=variant.id,
        order_index=int(job.start_ts * 1000),
        active=True,
    )
    db.add(seg)
    await db.commit()
    await db.refresh(seg)

    entity_job_id: str | None = None
    if discover_occurrences:
        ent_job = Job(
            project_id=proj.id,
            kind="entity",
            status="pending",
            payload={
                "segment_id": seg.id,
                "reference_frame_ts": job.reference_frame_ts,
                "reference_variant_url": variant.url,
                "bbox": job.bbox_json,
            },
        )
        db.add(ent_job)
        await db.commit()
        await db.refresh(ent_job)
        entity_job_id = ent_job.id

        if runner is not None:
            runner.submit(ent_job.id, lambda: entity_job.run(ent_job.id))

    return {
        "segment_id": seg.id,
        "entity_job_id": entity_job_id,
        "message": f"Variant {variant_index} accepted and applied to timeline",
    }


@_register("get_timeline")
async def _get_timeline(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Fetch the project timeline — mirrors GET /api/timeline/{project_id}."""
    project_id: str = args["project_id"]

    proj = await _get_project_or_error(db, project_id, session_id)
    items = await build_timeline(db, proj)

    return {
        "project_id": proj.id,
        "duration": proj.duration,
        "segments": [
            {
                "start_ts": it.start_ts,
                "end_ts": it.end_ts,
                "source": it.source,
                "url": storage.normalize_url_like(it.url, fallback=it.url),
                "audio": it.audio,
                "segment_id": it.id,
            }
            for it in items
        ],
    }


@_register("export_video")
async def _export_video(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Create an export job — mirrors POST /api/export."""
    from app.workers import export_job as export_worker

    project_id: str = args["project_id"]

    proj = await _get_project_or_error(db, project_id, session_id)

    job = Job(
        project_id=proj.id,
        kind="export",
        status="pending",
        payload={"format": "mp4"},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    if runner is not None:
        runner.submit(job.id, lambda: export_worker.run(job.id))

    return {
        "export_job_id": job.id,
        "status": "pending",
        "message": "Export job created — check status with get_export_status",
    }


@_register("get_export_status")
async def _get_export_status(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Fetch export job status + URLs — mirrors GET /api/export/{export_job_id}."""
    export_job_id: str = args["export_job_id"]

    job = await db.get(Job, export_job_id)
    if job is None or job.kind != "export":
        raise ValueError(f"export job not found: {export_job_id}")

    proj = await db.get(Project, job.project_id)
    if proj is None or proj.session_id != session_id:
        raise ValueError(f"export job not found: {export_job_id}")

    payload = job.payload or {}
    export_url = payload.get("export_url")
    download_url: str | None = None
    if export_url:
        export_url = storage.normalize_url_like(export_url, fallback=export_url)
        key = storage.key_from_url(export_url)
        if key:
            download_url = storage.download_url_for_key(
                key,
                filename=f"fiebatt-{job.project_id[:8]}.mp4",
            )
        else:
            download_url = export_url

    return {
        "export_job_id": job.id,
        "status": job.status,
        "export_url": export_url,
        "download_url": download_url,
        "error": job.error,
    }


@_register("preview_frame")
async def _preview_frame(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Extract and publish a single preview frame."""
    project_id: str = args["project_id"]
    ts: float = float(args["ts"])

    proj = await _get_project_or_error(db, project_id, session_id)
    _validate_preview_timestamp(ts, proj.duration)
    items = await build_timeline(db, proj)
    return await _extract_preview_frame_result(proj, items, ts)


@_register("preview_strip")
async def _preview_strip(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Extract a strip of preview frames across a range."""
    project_id: str = args["project_id"]
    start: float = float(args["start"])
    end: float = float(args["end"])
    fps_sampled: float = float(args.get("fps", 1.0))

    if fps_sampled <= 0:
        raise ValueError("fps must be > 0")

    proj = await _get_project_or_error(db, project_id, session_id)
    _validate_preview_bounds(start, end, proj.duration)
    items = await build_timeline(db, proj)

    step = 1.0 / fps_sampled
    frame_timestamps: list[float] = []
    ts = start
    while ts < end - 1e-6:
        frame_timestamps.append(round(ts, 6))
        ts += step
    if not frame_timestamps or frame_timestamps[-1] < end - 1e-6:
        frame_timestamps.append(round(end, 6))

    frames = [
        await _extract_preview_frame_result(proj, items, frame_ts)
        for frame_ts in frame_timestamps
    ]
    return {"frames": frames}


@_register("split_segment")
async def _split_segment(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Split a segment into left and right pieces."""
    project_id: str = args["project_id"]
    segment_id: str = args["segment_id"]
    split_ts: float = float(args["split_ts"])

    await _get_project_or_error(db, project_id, session_id)
    segment = await _get_segment_or_error(db, project_id, segment_id)
    if not (segment.start_ts < split_ts < segment.end_ts):
        raise ValueError("split_ts must be within the segment bounds")

    left = Segment(
        project_id=segment.project_id,
        start_ts=segment.start_ts,
        end_ts=split_ts,
        source=segment.source,
        url=segment.url,
        variant_id=segment.variant_id,
        order_index=segment.order_index,
        active=True,
    )
    right = Segment(
        project_id=segment.project_id,
        start_ts=split_ts,
        end_ts=segment.end_ts,
        source=segment.source,
        url=segment.url,
        variant_id=segment.variant_id,
        order_index=segment.order_index + 1,
        active=True,
    )
    segment.active = False

    db.add_all([left, right])
    await db.commit()
    await db.refresh(left)
    await db.refresh(right)

    return {
        "left": _segment_dict(left),
        "right": _segment_dict(right),
    }


@_register("trim_segment")
async def _trim_segment(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Trim a segment within its current bounds."""
    project_id: str = args["project_id"]
    segment_id: str = args["segment_id"]
    new_start_ts: float = float(args["new_start_ts"])
    new_end_ts: float = float(args["new_end_ts"])

    await _get_project_or_error(db, project_id, session_id)
    segment = await _get_segment_or_error(db, project_id, segment_id)

    if new_start_ts >= new_end_ts:
        raise ValueError("invalid segment range")
    if new_start_ts < segment.start_ts or new_end_ts > segment.end_ts:
        raise ValueError("trim range must stay within the original segment bounds")

    segment.start_ts = new_start_ts
    segment.end_ts = new_end_ts
    await db.commit()
    await db.refresh(segment)
    return _segment_dict(segment)


@_register("delete_segment")
async def _delete_segment(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Soft-delete a segment."""
    project_id: str = args["project_id"]
    segment_id: str = args["segment_id"]

    await _get_project_or_error(db, project_id, session_id)
    segment = await _get_segment_or_error(db, project_id, segment_id)
    segment.active = False
    await db.commit()
    return {"deleted": True}


@_register("color_grade")
async def _color_grade(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Apply grade adjustments to a segment and create a generated replacement."""
    segment_id: str = args["segment_id"]
    adjustments = {
        key: args[key]
        for key in (
            "brightness",
            "contrast",
            "saturation",
            "temperature",
            "gamma",
            "hue_shift",
        )
        if key in args and args[key] is not None
    }

    segment, _project = await _get_owned_segment_or_error(db, segment_id, session_id)
    input_path = await storage.path_from_url(segment.url)
    output_path, _ = storage.new_path("graded", "mp4")

    await color_service.apply_grade(
        input_path=input_path,
        output_path=output_path,
        adjustments=adjustments,
    )
    graded_url = await storage.publish(output_path)

    await _deactivate_overlapping_generated_segments(db, segment)

    graded_segment = Segment(
        project_id=segment.project_id,
        start_ts=segment.start_ts,
        end_ts=segment.end_ts,
        source="generated",
        url=graded_url,
        order_index=segment.order_index,
        active=True,
    )
    db.add(graded_segment)
    await db.commit()
    await db.refresh(graded_segment)

    return {
        "segment_id": graded_segment.id,
        "graded_url": graded_url,
    }


@_register("grade_preview")
async def _grade_preview(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Render a graded preview frame for a segment."""
    segment_id: str = args["segment_id"]
    adjustments = {
        key: args[key]
        for key in (
            "brightness",
            "contrast",
            "saturation",
            "temperature",
            "gamma",
            "hue_shift",
        )
        if key in args and args[key] is not None
    }

    segment, _project = await _get_owned_segment_or_error(db, segment_id, session_id)
    input_path = await storage.path_from_url(segment.url)
    probe_data = await ffmpeg.probe(input_path)
    frame_ts = max(float(probe_data.get("duration", 0.0)) / 2.0, 0.0)

    frame_path, _ = storage.new_path("previews", "jpg")
    preview_path, _ = storage.new_path("previews", "jpg")

    await ffmpeg.extract_frame(input_path, frame_ts, frame_path)
    await color_service.apply_grade_to_frame(
        input_path=frame_path,
        output_path=preview_path,
        adjustments=adjustments,
    )
    preview_frame_url = await storage.publish(preview_path)
    return {"preview_frame_url": preview_frame_url}


@_register("score_variant")
async def _score_variant(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Get detailed Gemini-based scoring for a variant."""
    variant_id: str = args["variant_id"]
    compare_to = str(args.get("compare_to", "prompt"))
    if compare_to not in {"prompt", "original"}:
        raise ValueError("compare_to must be 'prompt' or 'original'")

    variant = await _get_variant_for_session_or_error(db, variant_id, session_id)
    if not variant.url:
        raise ValueError("variant has no video url")
    if not variant.job.prompt:
        raise ValueError("variant job has no prompt")

    variant_video_path = await storage.path_from_url(variant.url)
    if not variant_video_path.exists():
        raise ValueError("variant video not found")

    client = _get_gemini_client()
    with tempfile.TemporaryDirectory(prefix="fiebatt-agent-score-") as temp_root:
        temp_dir = Path(temp_root)
        frame_paths = await _sample_video_frames(
            src=variant_video_path,
            temp_dir=temp_dir,
            prefix="variant",
            num_frames=FRAME_COUNT,
        )

        prompt_text = _score_prompt(compare_to, variant.job.prompt)
        if compare_to == "original":
            if variant.job.start_ts is None or variant.job.end_ts is None:
                raise ValueError("job has no segment range")
            original_source = Path(variant.job.project.video_path)
            if not original_source.exists():
                original_source = await storage.path_from_url(variant.job.project.video_url)
            if not original_source.exists():
                raise ValueError("original project video not found")

            original_paths = await _sample_video_frames(
                src=original_source,
                temp_dir=temp_dir,
                prefix="original",
                num_frames=FRAME_COUNT,
                start_ts=variant.job.start_ts,
                end_ts=variant.job.end_ts,
            )
            prompt_text = (
                f"{prompt_text}\n"
                "the first set of frames are from the original source segment. "
                "the second set of frames are from the generated variant."
            )
            frame_paths = [*original_paths, *frame_paths]
        else:
            prompt_text = (
                f"{prompt_text}\n"
                "all attached frames are sampled from the generated variant."
            )

        return await _generate_json(
            client=client,
            prompt_text=prompt_text,
            frame_paths=frame_paths,
        )


@_register("score_continuity")
async def _score_continuity(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Check continuity across project timeline boundaries."""
    project_id: str = args["project_id"]
    proj = await _get_project_or_error(db, project_id, session_id)
    timeline = await build_timeline(db, proj)
    if len(timeline) < 2:
        return {"overall": 10.0, "issues": []}

    client = _get_gemini_client()
    analyses: list[tuple[float, dict[str, Any]]] = []
    with tempfile.TemporaryDirectory(prefix="fiebatt-agent-continuity-") as temp_root:
        temp_dir = Path(temp_root)
        for index, (prev_item, next_item) in enumerate(zip(timeline, timeline[1:]), start=1):
            boundary_ts = prev_item.end_ts
            prev_frame = await _boundary_frame(
                proj=proj,
                item=prev_item,
                is_end=True,
                output_path=temp_dir / f"boundary_{index:02d}_prev.png",
            )
            next_frame = await _boundary_frame(
                proj=proj,
                item=next_item,
                is_end=False,
                output_path=temp_dir / f"boundary_{index:02d}_next.png",
            )
            payload = await _generate_json(
                client=client,
                prompt_text=_continuity_prompt(boundary_ts),
                frame_paths=[prev_frame, next_frame],
            )
            analyses.append((boundary_ts, payload))

    issues = [
        {
            "at_ts": boundary_ts,
            "type": issue.get("type", "unknown"),
            "severity": float(issue.get("severity", 0.0)),
        }
        for boundary_ts, analysis in analyses
        for issue in analysis.get("issues", [])
    ]
    overall = sum(float(analysis.get("score", 0.0)) for _, analysis in analyses) / len(analyses)
    return {"overall": overall, "issues": issues}


@_register("remix_variant")
async def _remix_variant(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Create a remix generation job for an existing variant."""
    from app.workers import generate_job as generate_worker

    variant_id: str = args["variant_id"]
    modifier_prompt: str = args["modifier_prompt"]
    preserve_composition: bool = bool(args.get("preserve_composition", True))

    variant = await _get_variant_for_session_or_error(db, variant_id, session_id)
    job = variant.job

    remix_job = Job(
        project_id=job.project_id,
        kind="generate",
        status="pending",
        start_ts=job.start_ts,
        end_ts=job.end_ts,
        bbox_json=job.bbox_json,
        prompt=f"{job.prompt or ''} | REFINEMENT: {modifier_prompt}",
        reference_frame_ts=job.reference_frame_ts,
        payload={
            "remix_source_variant_id": variant.id,
            "preserve_composition": preserve_composition,
        },
    )
    db.add(remix_job)
    await db.commit()
    await db.refresh(remix_job)

    if runner is not None:
        runner.submit(remix_job.id, lambda: generate_worker.run(remix_job.id))
    return {"job_id": remix_job.id}


@_register("batch_generate")
async def _batch_generate(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Submit multiple generate jobs in one request."""
    from app.workers import generate_job as generate_worker

    edits = args.get("edits") or []
    if not edits:
        raise ValueError("edits must not be empty")
    if len(edits) > MAX_BATCH_EDITS:
        raise ValueError(f"at most {MAX_BATCH_EDITS} edits are allowed per batch")

    jobs: list[Job] = []
    for edit in edits:
        project_id = str(edit["project_id"])
        start_ts = float(edit["start_ts"])
        end_ts = float(edit["end_ts"])
        bbox = dict(edit["bbox"])
        prompt = str(edit["prompt"])
        reference_frame_ts = edit.get("reference_frame_ts")

        proj = await _get_project_or_error(db, project_id, session_id)
        _validate_segment_length(start_ts, end_ts)
        if end_ts > proj.duration + 1e-3:
            raise ValueError("end_ts past project duration")
        _validate_bbox_bounds(bbox)

        job = Job(
            project_id=proj.id,
            kind="generate",
            status="pending",
            start_ts=start_ts,
            end_ts=end_ts,
            bbox_json=bbox,
            prompt=prompt,
            reference_frame_ts=float(reference_frame_ts) if reference_frame_ts is not None else None,
        )
        db.add(job)
        jobs.append(job)

    await db.commit()

    job_ids: list[str] = []
    for job in jobs:
        await db.refresh(job)
        if runner is not None:
            runner.submit(job.id, lambda job_id=job.id: generate_worker.run(job_id))
        job_ids.append(job.id)

    return {"job_ids": job_ids}


@_register("snapshot_timeline")
async def _snapshot_timeline(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Save a timeline snapshot."""
    project_id: str = args["project_id"]
    await _get_project_or_error(db, project_id, session_id)

    active_segments = (
        await db.execute(
            select(Segment)
            .where(
                Segment.project_id == project_id,
                Segment.active == True,  # noqa: E712
            )
            .order_by(Segment.order_index, Segment.start_ts)
        )
    ).scalars().all()

    snapshot = TimelineSnapshot(
        project_id=project_id,
        segments_json=[_snapshot_payload(segment) for segment in active_segments],
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)

    return {
        "snapshot_id": snapshot.id,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


@_register("revert_timeline")
async def _revert_timeline(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Restore a project timeline from a snapshot."""
    project_id: str = args["project_id"]
    snapshot_id: str = args["snapshot_id"]

    await _get_project_or_error(db, project_id, session_id)
    snapshot = await db.get(TimelineSnapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise ValueError(f"snapshot not found: {snapshot_id}")

    active_segments = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == project_id,
                Segment.active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    for segment in active_segments:
        segment.active = False

    restored_segments = [
        Segment(
            project_id=project_id,
            start_ts=float(segment_data["start_ts"]),
            end_ts=float(segment_data["end_ts"]),
            source=str(segment_data["source"]),
            url=str(segment_data["url"]),
            variant_id=(
                str(segment_data["variant_id"])
                if segment_data.get("variant_id") is not None
                else None
            ),
            order_index=int(segment_data.get("order_index", 0)),
            active=bool(segment_data.get("active", True)),
        )
        for segment_data in snapshot.segments_json
    ]
    db.add_all(restored_segments)
    await db.commit()

    return {
        "reverted": True,
        "segment_count": len(restored_segments),
    }


@_register("list_entities")
async def _list_entities(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """List entities identified in a project, with their appearances.

    Use this after the user accepts a variant to find the entity ID for
    propagation.
    """
    from app.models.entity import Entity, EntityAppearance
    from sqlalchemy.orm import selectinload

    project_id: str = args["project_id"]

    proj = await _get_project_or_error(db, project_id, session_id)

    entities = (
        await db.execute(
            select(Entity)
            .where(Entity.project_id == proj.id)
            .options(selectinload(Entity.appearances))
            .order_by(Entity.created_at.desc())
        )
    ).scalars().all()

    return {
        "project_id": proj.id,
        "entities": [
            {
                "id": e.id,
                "description": e.description,
                "category": e.category,
                "reference_variant_url": e.reference_variant_url,
                "appearance_count": len(e.appearances),
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "appearances": [
                    {
                        "id": a.id,
                        "start_ts": a.start_ts,
                        "end_ts": a.end_ts,
                        "confidence": a.confidence,
                    }
                    for a in e.appearances
                ],
            }
            for e in entities
        ],
    }


@_register("propagate_edit")
async def _propagate_edit(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Propagate an accepted edit to all appearances of an entity.

    Mirrors POST /api/propagate.
    """
    from app.models.entity import Entity
    from app.models.propagation import PropagationJob, PropagationResult
    from app.workers import propagate_job as propagate_worker
    from sqlalchemy.orm import selectinload

    entity_id: str = args["entity_id"]
    source_variant_url: str = args["source_variant_url"]
    prompt: str = args["prompt"]
    auto_apply: bool = bool(args.get("auto_apply", True))

    entity = (
        await db.execute(
            select(Entity)
            .where(Entity.id == entity_id)
            .options(selectinload(Entity.appearances))
        )
    ).scalar_one_or_none()
    if entity is None:
        raise ValueError(f"entity not found: {entity_id}")

    proj = await db.get(Project, entity.project_id)
    if proj is None or proj.session_id != session_id:
        raise ValueError(f"entity not found: {entity_id}")

    if not entity.appearances:
        raise ValueError("entity has no other appearances to propagate")

    pjob = PropagationJob(
        project_id=proj.id,
        entity_id=entity.id,
        source_variant_url=source_variant_url,
        prompt=prompt,
        auto_apply=auto_apply,
        status="pending",
    )
    db.add(pjob)
    await db.flush()

    for app in entity.appearances:
        res = PropagationResult(
            propagation_job_id=pjob.id,
            appearance_id=app.id,
            status="pending",
        )
        db.add(res)

    await db.commit()
    await db.refresh(pjob)

    if runner is not None:
        runner.submit(pjob.id, lambda: propagate_worker.run(pjob.id))

    return {
        "propagation_job_id": pjob.id,
        "entity_id": entity_id,
        "appearance_count": len(entity.appearances),
        "auto_apply": auto_apply,
        "message": f"Propagation job created for {len(entity.appearances)} appearances",
    }


@_register("get_propagation_status")
async def _get_propagation_status(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Check the status of a propagation job.

    Mirrors GET /api/propagate/{id}.
    """
    from app.models.propagation import PropagationJob, PropagationResult

    propagation_job_id: str = args["propagation_job_id"]

    pjob = await db.get(PropagationJob, propagation_job_id)
    if pjob is None:
        raise ValueError(f"propagation job not found: {propagation_job_id}")

    proj = await db.get(Project, pjob.project_id)
    if proj is None or proj.session_id != session_id:
        raise ValueError(f"propagation job not found: {propagation_job_id}")

    rows = (
        await db.execute(
            select(PropagationResult).where(
                PropagationResult.propagation_job_id == propagation_job_id
            )
        )
    ).scalars().all()

    return {
        "propagation_job_id": pjob.id,
        "status": pjob.status,
        "error": pjob.error,
        "results": [
            {
                "id": r.id,
                "appearance_id": r.appearance_id,
                "segment_id": r.segment_id,
                "variant_url": r.variant_url,
                "status": r.status,
                "applied": r.applied,
                "error": r.error,
            }
            for r in rows
        ],
    }


@_register("apply_propagation_result")
async def _apply_propagation_result(
    *,
    args: dict[str, Any],
    db: AsyncSession,
    session_id: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Apply a specific propagation result to the timeline.

    Mirrors POST /api/propagate/{id}/apply/{result_id}.
    """
    from app.models.entity import EntityAppearance
    from app.models.propagation import PropagationJob, PropagationResult
    from app.models.segment import Segment

    propagation_job_id: str = args["propagation_job_id"]
    result_id: str = args["result_id"]

    pjob = await db.get(PropagationJob, propagation_job_id)
    if pjob is None:
        raise ValueError(f"propagation job not found: {propagation_job_id}")

    proj = await db.get(Project, pjob.project_id)
    if proj is None or proj.session_id != session_id:
        raise ValueError(f"propagation job not found: {propagation_job_id}")

    res = await db.get(PropagationResult, result_id)
    if res is None or res.propagation_job_id != propagation_job_id:
        raise ValueError(f"result not found: {result_id}")
    if res.status != "done" or not res.variant_url:
        raise ValueError(f"result not ready (status={res.status})")
    if res.applied:
        return {
            "result_id": res.id,
            "already_applied": True,
            "segment_id": res.segment_id,
            "variant_url": res.variant_url,
        }

    appearance = await db.get(EntityAppearance, res.appearance_id)
    if appearance is None:
        raise ValueError("appearance missing")

    # deactivate overlapping generated segments
    overlapping = (
        await db.execute(
            select(Segment).where(
                Segment.project_id == proj.id,
                Segment.active == True,  # noqa: E712
                Segment.source == "generated",
                Segment.start_ts < appearance.end_ts,
                Segment.end_ts > appearance.start_ts,
            )
        )
    ).scalars().all()
    for seg in overlapping:
        seg.active = False

    seg = Segment(
        project_id=proj.id,
        start_ts=appearance.start_ts,
        end_ts=appearance.end_ts,
        source="generated",
        url=res.variant_url,
        order_index=int(appearance.start_ts * 1000),
        active=True,
    )
    db.add(seg)
    await db.flush()

    res.segment_id = seg.id
    res.applied = True
    await db.commit()

    return {
        "result_id": res.id,
        "segment_id": seg.id,
        "variant_url": res.variant_url,
        "applied": True,
        "start_ts": appearance.start_ts,
        "end_ts": appearance.end_ts,
    }
