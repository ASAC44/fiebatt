from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models as _models  # noqa: F401
from app.db.base import Base
from app.models.edit_plan import EditPlanRecord, GenerationChunk
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.selection import SelectionArtifact
from app.schemas.edit_plan import (
    EditCore,
    EditIntent,
    GenerationContext,
    GroundedEditInstruction,
    LocalRangeResolution,
)
from app.services.global_chunk_sequence import ChunkExecution
from app.services.global_seam import AssemblyResult
from app.workers import local_chunk_job


@pytest.mark.asyncio
async def test_chunk_worker_renders_in_order_and_publishes_one_variant(
    tmp_path,
    monkeypatch,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chunks.db'}")
    sessions = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    core = EditCore(start_ts=3.0, end_ts=23.0)
    resolution = LocalRangeResolution(
        edit_core=core,
        generation_context=GenerationContext(
            start_ts=2.25,
            end_ts=23.75,
            edit_core=core,
        ),
        occurrence_start=3.0,
        occurrence_end=23.0,
        analysis_start=2.0,
        analysis_end=24.0,
        confidence=0.9,
    )
    intent = EditIntent(
        raw_prompt="make this ball pink",
        change_type="appearance",
        duration_policy="continuous_occurrence",
        grounded_edit=GroundedEditInstruction(
            intent="appearance change",
            description="pink ball",
            region_emphasis="selected ball only",
            prompt_for_video_edit="Change only the selected ball to pink.",
        ),
    )
    bbox = {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.3}
    track_frames = [
        {
            "timestamp": timestamp,
            "state": "tracked",
            "confidence": 0.9,
            "bbox": bbox,
        }
        for timestamp in range(2, 25)
    ]
    async with sessions() as db:
        project = Project(
            session_id="owner",
            video_path=str(tmp_path / "source.mp4"),
            video_url="https://media.example/source.mp4",
            duration=30.0,
            fps=30.0,
            width=1280,
            height=720,
        )
        db.add(project)
        await db.flush()
        selection = SelectionArtifact(
            project_id=project.id,
            frame_ts=10.0,
            bbox_json=bbox,
            contours_json=[],
            mask_url="https://media.example/mask.png",
            subject_reference_url="https://media.example/subject.png",
            sam_score=0.9,
            source_revision=project.video_url,
        )
        db.add(selection)
        await db.flush()
        plan = EditPlanRecord(
            project_id=project.id,
            selection_id=selection.id,
            raw_prompt=intent.raw_prompt,
            scope="local",
            intent_json=intent.model_dump(mode="json"),
            range_json=resolution.model_dump(mode="json"),
            estimate_json={},
            provider="happyhorse",
            provider_reason="test",
            warnings_json=[],
            source_revision=project.video_url,
        )
        db.add(plan)
        await db.flush()
        for index, values in enumerate(
            [
                (3.0, 16.0, 2.25, 16.75),
                (16.0, 23.0, 15.25, 23.75),
            ]
        ):
            edit_start, edit_end, context_start, context_end = values
            db.add(
                GenerationChunk(
                    plan_id=plan.id,
                    index=index,
                    edit_start=edit_start,
                    edit_end=edit_end,
                    context_start=context_start,
                    context_end=context_end,
                    provider="happyhorse",
                    payload_json={
                        "track_frames": track_frames,
                        "boundary_contract": {
                            "protect_source_before": index == 0,
                            "protect_source_after": index == 1,
                            "handoff_from_previous": index == 1,
                            "handoff_to_next": index == 0,
                        },
                    },
                )
            )
        job = Job(
            project_id=project.id,
            kind="generate",
            status="pending",
            start_ts=3.0,
            end_ts=23.0,
            bbox_json=bbox,
            prompt=intent.raw_prompt,
            reference_frame_ts=10.0,
            payload={"plan_id": plan.id},
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    monkeypatch.setattr(local_chunk_job, "AsyncSessionLocal", sessions)

    async def reference_subject(project, selection):
        return tmp_path / "subject.png"

    executions = []

    async def execute(**kwargs):
        chunk = kwargs["chunk"]
        previous = kwargs["previous"]
        executions.append((chunk.index, previous.output_url if previous else None))
        return ChunkExecution(
            output_url=f"https://media.example/chunk-{chunk.index}.mp4",
            metadata={"provider": chunk.provider},
        )

    async def assemble(*, project, occurrence, chunks):
        assert occurrence.edit_start == pytest.approx(2.25)
        assert occurrence.edit_end == pytest.approx(23.75)
        assert [chunk.output_url for chunk in chunks] == [
            "https://media.example/chunk-0.mp4",
            "https://media.example/chunk-1.mp4",
        ]
        return AssemblyResult(
            "https://media.example/final.mp4",
            (),
            {"entry": {"passed": True}, "exit": {"passed": True}},
        )

    async def sample(url):
        assert url == "https://media.example/final.mp4"
        return [str(tmp_path / "frame.jpg")]

    async def crop(frame, bbox, output):
        return Path(output)

    async def score(*args, **kwargs):
        return {"visual_coherence": 9, "prompt_adherence": 9}

    monkeypatch.setattr(local_chunk_job, "_reference_subject", reference_subject)
    monkeypatch.setattr(local_chunk_job, "execute_global_chunk", execute)
    monkeypatch.setattr(local_chunk_job, "assemble_global_occurrence", assemble)
    monkeypatch.setattr(local_chunk_job, "_sample_variant_frames", sample)
    monkeypatch.setattr(local_chunk_job.ffmpeg, "crop_bbox_from_frame", crop)
    monkeypatch.setattr(local_chunk_job, "_score_variant_safe", score)

    await local_chunk_job.run(job_id)

    assert executions == [
        (0, None),
        (1, "https://media.example/chunk-0.mp4"),
    ]
    async with sessions() as db:
        job = await db.get(Job, job_id)
        variant = (await db.execute(select(Variant))).scalar_one()
        chunks = (
            await db.execute(select(GenerationChunk).order_by(GenerationChunk.index))
        ).scalars().all()
        assert job.status == "done"
        assert job.payload["generation_quality_state"] == "pass"
        assert job.payload["progress_state"]["status"] == "done"
        assert job.payload["progress_state"]["stage"] == "done"
        assert variant.status == "done"
        assert variant.url == "https://media.example/final.mp4"
        assert [chunk.status for chunk in chunks] == ["generated", "generated"]

    await engine.dispose()
