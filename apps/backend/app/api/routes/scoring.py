from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai.services.ffmpeg import sample_frames_from_clip
from app.db.session import get_db
from app.deps import get_session
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.services import storage
from app.services.ffmpeg import extract_frame
from app.services.timeline_builder import TimelineItem, build_timeline


router = APIRouter(tags=["scoring"])

GEMINI_MODEL = "gemini-2.5-flash"
FRAME_COUNT = 5


class ScoreRequest(BaseModel):
    variant_id: str
    compare_to: Literal["original", "prompt"] = "prompt"


class ScoreCategory(BaseModel):
    score: float
    issues: list[str] = Field(default_factory=list)


class PromptAdherenceCategory(BaseModel):
    score: float
    misses: list[str] = Field(default_factory=list)


class TemporalConsistencyCategory(BaseModel):
    score: float
    flicker_detected: bool


class VariantScoreResponse(BaseModel):
    visual_coherence: ScoreCategory
    prompt_adherence: PromptAdherenceCategory
    temporal_consistency: TemporalConsistencyCategory
    edge_quality: ScoreCategory
    overall: float
    recommendation: Literal["accept", "remix", "reject"]


class CompareRequest(BaseModel):
    variant_ids: list[str]


class VariantRanking(BaseModel):
    variant_id: str
    overall: float
    strengths: list[str]
    weaknesses: list[str]


class CompareResponse(BaseModel):
    rankings: list[VariantRanking]
    best: str


class ContinuityRequest(BaseModel):
    project_id: str


class ContinuityIssue(BaseModel):
    at_ts: float
    type: str
    severity: float


class ContinuityResponse(BaseModel):
    overall: float
    issues: list[ContinuityIssue]


class BoundaryIssue(BaseModel):
    type: str
    severity: float


class BoundaryAnalysis(BaseModel):
    score: float
    issues: list[BoundaryIssue] = Field(default_factory=list)


def _get_client() -> genai.Client:
    from app.config.settings import get_settings
    settings = get_settings()
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured")
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


def _score_prompt(compare_to: Literal["original", "prompt"], prompt: str) -> str:
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
) -> dict:
    contents = [types.Part.from_text(prompt_text)]
    contents.extend(_image_part(path) for path in frame_paths)

    response = await client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text)


async def _load_variant_for_session(
    db: AsyncSession,
    session: SessionModel,
    variant_id: str,
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
    if variant is None or variant.job.project.session_id != session.id:
        raise HTTPException(status_code=404, detail="variant not found")
    return variant


async def _load_project_for_session(
    db: AsyncSession,
    session: SessionModel,
    project_id: str,
) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.session_id != session.id:
        raise HTTPException(status_code=404, detail="project not found")
    return project


async def _sample_original_segment_frames(
    *,
    project: Project,
    job: Job,
    temp_dir: Path,
    num_frames: int,
) -> list[Path]:
    if job.start_ts is None or job.end_ts is None:
        raise HTTPException(status_code=422, detail="job has no segment range")
    if job.end_ts <= job.start_ts:
        raise HTTPException(status_code=422, detail="job segment range is invalid")

    source_path = await storage.path_from_url(project.video_url)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="original project video not found")

    duration = job.end_ts - job.start_ts
    sampled_paths: list[Path] = []
    for index in range(num_frames):
        ts = job.start_ts + ((index + 0.5) * duration / num_frames)
        out = temp_dir / f"original_{index:02d}.png"
        await extract_frame(source_path, ts, out)
        sampled_paths.append(out)
    return sampled_paths


async def _score_variant_impl(
    *,
    variant: Variant,
    compare_to: Literal["original", "prompt"],
    client: genai.Client,
) -> VariantScoreResponse:
    if not variant.url:
        raise HTTPException(status_code=422, detail="variant has no video url")
    if not variant.job.prompt:
        raise HTTPException(status_code=422, detail="variant job has no prompt")

    video_path = await storage.path_from_url(variant.url)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="variant video not found")

    with tempfile.TemporaryDirectory(prefix="fiebatt-score-") as temp_root:
        temp_dir = Path(temp_root)
        variant_frame_paths = await asyncio.to_thread(
            sample_frames_from_clip,
            str(video_path),
            FRAME_COUNT,
        )
        frame_paths = [Path(path) for path in variant_frame_paths]

        prompt_text = _score_prompt(compare_to, variant.job.prompt)
        if compare_to == "original":
            original_paths = await _sample_original_segment_frames(
                project=variant.job.project,
                job=variant.job,
                temp_dir=temp_dir,
                num_frames=FRAME_COUNT,
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

        payload = await _generate_json(
            client=client,
            prompt_text=prompt_text,
            frame_paths=frame_paths,
        )
        return VariantScoreResponse.model_validate(payload)


def _strengths_from_score(score: VariantScoreResponse) -> list[str]:
    strengths: list[str] = []
    if score.visual_coherence.score >= 8.0:
        strengths.append("strong visual coherence")
    if score.prompt_adherence.score >= 8.0:
        strengths.append("good prompt adherence")
    if score.temporal_consistency.score >= 8.0 and not score.temporal_consistency.flicker_detected:
        strengths.append("stable temporal consistency")
    if score.edge_quality.score >= 8.0:
        strengths.append("clean edge quality")
    if not strengths:
        strengths.append(score.recommendation.replace("_", " "))
    return strengths[:3]


def _weaknesses_from_score(score: VariantScoreResponse) -> list[str]:
    weaknesses: list[str] = []
    weaknesses.extend(score.visual_coherence.issues[:1])
    weaknesses.extend(score.prompt_adherence.misses[:1])
    if score.temporal_consistency.flicker_detected:
        weaknesses.append("flicker detected")
    weaknesses.extend(score.edge_quality.issues[:1])
    if not weaknesses and score.recommendation != "accept":
        weaknesses.append(f"recommendation: {score.recommendation}")
    return weaknesses[:3]


async def _boundary_frame(
    *,
    item: TimelineItem,
    is_end: bool,
    output_path: Path,
) -> Path:
    clip_path = await storage.path_from_url(item.url)
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="timeline source video not found")

    epsilon = min(0.04, max(item.duration / 10.0, 0.001))
    if item.source == "original":
        ts = max(0.0, item.end_ts - epsilon) if is_end else max(0.0, item.start_ts)
    else:
        ts = max(0.0, item.duration - epsilon) if is_end else 0.0

    return await extract_frame(clip_path, ts, output_path)


async def _analyze_boundary(
    *,
    client: genai.Client,
    prev_item: TimelineItem,
    next_item: TimelineItem,
    boundary_ts: float,
    temp_dir: Path,
    index: int,
) -> BoundaryAnalysis:
    prev_frame = await _boundary_frame(
        item=prev_item,
        is_end=True,
        output_path=temp_dir / f"boundary_{index:02d}_prev.png",
    )
    next_frame = await _boundary_frame(
        item=next_item,
        is_end=False,
        output_path=temp_dir / f"boundary_{index:02d}_next.png",
    )
    payload = await _generate_json(
        client=client,
        prompt_text=_continuity_prompt(boundary_ts),
        frame_paths=[prev_frame, next_frame],
    )
    return BoundaryAnalysis.model_validate(payload)


@router.post("/score", response_model=VariantScoreResponse)
async def score_variant(
    body: ScoreRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    variant = await _load_variant_for_session(db, session, body.variant_id)
    client = _get_client()
    return await _score_variant_impl(
        variant=variant,
        compare_to=body.compare_to,
        client=client,
    )


@router.post("/score/compare", response_model=CompareResponse)
async def compare_variants(
    body: CompareRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    if not body.variant_ids:
        raise HTTPException(status_code=422, detail="variant_ids must not be empty")

    client = _get_client()
    variants = [
        await _load_variant_for_session(db, session, variant_id)
        for variant_id in body.variant_ids
    ]

    scored = await asyncio.gather(
        *[
            _score_variant_impl(
                variant=variant,
                compare_to="prompt",
                client=client,
            )
            for variant in variants
        ]
    )

    rankings = [
        VariantRanking(
            variant_id=variant.id,
            overall=score.overall,
            strengths=_strengths_from_score(score),
            weaknesses=_weaknesses_from_score(score),
        )
        for variant, score in zip(variants, scored, strict=True)
    ]
    rankings.sort(key=lambda item: item.overall, reverse=True)

    return CompareResponse(rankings=rankings, best=rankings[0].variant_id)


@router.post("/score/continuity", response_model=ContinuityResponse)
async def score_continuity(
    body: ContinuityRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    project = await _load_project_for_session(db, session, body.project_id)
    timeline = await build_timeline(db, project)
    if len(timeline) < 2:
        return ContinuityResponse(overall=10.0, issues=[])

    client = _get_client()
    analyses: list[tuple[float, BoundaryAnalysis]] = []
    with tempfile.TemporaryDirectory(prefix="fiebatt-continuity-") as temp_root:
        temp_dir = Path(temp_root)
        for index, (prev_item, next_item) in enumerate(zip(timeline, timeline[1:]), start=1):
            boundary_ts = prev_item.end_ts
            analysis = await _analyze_boundary(
                client=client,
                prev_item=prev_item,
                next_item=next_item,
                boundary_ts=boundary_ts,
                temp_dir=temp_dir,
                index=index,
            )
            analyses.append((boundary_ts, analysis))

    issues = [
        ContinuityIssue(
            at_ts=boundary_ts,
            type=issue.type,
            severity=issue.severity,
        )
        for boundary_ts, analysis in analyses
        for issue in analysis.issues
    ]
    overall = sum(analysis.score for _, analysis in analyses) / len(analyses)

    return ContinuityResponse(overall=overall, issues=issues)
