from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models as _models  # noqa: F401
from app.config.settings import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entity import Entity, EntityAppearance, OccurrenceCandidate, OccurrenceTrack
from app.models.job import Job, Variant
from app.models.project import Project
from app.models.propagation import (
    GlobalEditPlan,
    GlobalGenerationChunk,
    GlobalOccurrencePlan,
    PropagationJob,
    PropagationResult,
)
from app.models.segment import Segment
from app.schemas.timeline import PersistedAsset, PersistedClip, PersistedEDL
from app.services.global_chunk_sequence import ChunkExecution
from app.services.global_seam import AssemblyResult, GlobalSeamError
from app.workers import global_edit_job, propagate_job


class FakeRunner:
    def __init__(self):
        self.submissions = []

    def submit(self, job_id, factory):
        self.submissions.append((job_id, factory))


@pytest_asyncio.fixture
async def global_api(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOBAL_EDIT_PLANNING", "true")
    get_settings.cache_clear()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'global.db'}")
    sessions = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with sessions() as session:
            yield session

    runner = FakeRunner()
    monkeypatch.setattr(propagate_job, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(global_edit_job, "AsyncSessionLocal", sessions)
    app.dependency_overrides[get_db] = override_get_db
    app.state.runner = runner
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/projects", headers={"X-Session-Id": "global-owner"})
        async with sessions() as db:
            project = Project(
                session_id="global-owner",
                video_path="/tmp/source.mp4",
                video_url="/media/source-v1.mp4",
                duration=30.0,
                fps=30.0,
                width=1280,
                height=720,
            )
            db.add(project)
            await db.flush()
            source_job = Job(
                project_id=project.id,
                kind="generate",
                status="done",
                start_ts=2.0,
                end_ts=5.0,
                reference_frame_ts=3.0,
                bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
                prompt="make the jacket blue",
            )
            db.add(source_job)
            await db.flush()
            variant = Variant(
                job_id=source_job.id,
                index=0,
                url="/media/reference.mp4",
                status="done",
            )
            db.add(variant)
            await db.flush()
            segment = Segment(
                project_id=project.id,
                start_ts=2.0,
                end_ts=5.0,
                source="generated",
                url=variant.url,
                variant_id=variant.id,
                order_index=2000,
                active=True,
            )
            db.add(segment)
            await db.flush()
            project.timeline_edl = PersistedEDL(
                clips=[
                    PersistedClip(
                        id="source-before",
                        kind="source",
                        url=project.video_url,
                        source_start=0.0,
                        source_end=2.0,
                        media_duration=project.duration,
                        project_id=project.id,
                    ),
                    PersistedClip(
                        id=segment.id,
                        kind="generated",
                        url=variant.url,
                        source_start=0.0,
                        source_end=3.0,
                        media_duration=3.0,
                        volume=0.0,
                        project_id=project.id,
                    ),
                    PersistedClip(
                        id="source-after",
                        kind="source",
                        url=project.video_url,
                        source_start=5.0,
                        source_end=30.0,
                        media_duration=project.duration,
                        project_id=project.id,
                    ),
                ],
                sources=[
                    PersistedAsset(
                        id="source",
                        kind="source",
                        url=project.video_url,
                        duration=project.duration,
                        fps=project.fps,
                        project_id=project.id,
                        label="source",
                    ),
                    PersistedAsset(
                        id=variant.id,
                        kind="generated",
                        url=variant.url,
                        duration=3.0,
                        fps=project.fps,
                        project_id=project.id,
                        label="ai edit",
                    ),
                ],
            ).model_dump(mode="json")
            entity = Entity(
                project_id=project.id,
                source_segment_id=segment.id,
                description="person in a jacket",
            )
            db.add(entity)
            await db.flush()
            appearance_ids = []
            for index, (start, end) in enumerate(((8.0, 11.0), (18.0, 22.0))):
                candidate = OccurrenceCandidate(
                    entity_id=entity.id,
                    source_revision=project.video_url,
                    cache_key=f"candidate-{index}",
                    keyframe_ts=start + 1.0,
                    start_ts=start,
                    end_ts=end,
                    confidence=0.9,
                    evidence_json={},
                    status="confirmed",
                )
                db.add(candidate)
                await db.flush()
                db.add(
                    OccurrenceTrack(
                        entity_id=entity.id,
                        candidate_id=candidate.id,
                        source_revision=project.video_url,
                        seed_ts=start + 1.0,
                        start_ts=start,
                        end_ts=end,
                        confidence=0.9,
                        tracker="sam2_video",
                        frames_json=[],
                        status="confirmed",
                    )
                )
                appearance = EntityAppearance(
                    entity_id=entity.id,
                    start_ts=start,
                    end_ts=end,
                    confidence=0.9,
                )
                db.add(appearance)
                await db.flush()
                appearance_ids.append(appearance.id)
            await db.commit()
            context = {
                "project_id": project.id,
                "entity_id": entity.id,
                "segment_id": segment.id,
                "appearance_ids": appearance_ids,
            }
        yield client, sessions, runner, context

    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_global_plan_is_non_generating_and_estimates_selected_work(global_api):
    client, sessions, runner, context = global_api
    response = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "selected_occurrences",
            "occurrence_ids": [context["appearance_ids"][1]],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scope"] == "selected_occurrences"
    assert [item["appearance_id"] for item in body["occurrences"]] == [
        context["appearance_ids"][1]
    ]
    assert body["estimate"]["occurrence_count"] == 1
    assert body["estimate"]["expected_generation_calls"] == 1
    assert body["estimate"]["expected_generated_seconds"] == 5.5
    assert body["estimate"]["reference_accepted"] is True
    assert body["requested_provider"] == "auto"
    assert len(body["occurrences"][0]["chunks"]) == 1
    chunk = body["occurrences"][0]["chunks"][0]
    assert chunk["provider"] == "wan"
    assert chunk["edit_start"] == 18.0
    assert chunk["edit_end"] == 22.0
    assert chunk["attempts"] == 0
    assert runner.submissions == []

    async with sessions() as db:
        occurrence_plans = (
            await db.execute(select(GlobalOccurrencePlan))
        ).scalars().all()
        chunks = (await db.execute(select(GlobalGenerationChunk))).scalars().all()
        assert len(occurrence_plans) == 1
        assert len(chunks) == 1
        assert chunks[0].payload_json["boundary_contract"] == {
            "protect_source_before": True,
            "protect_source_after": True,
            "handoff_from_previous": False,
            "handoff_to_next": False,
        }


@pytest.mark.asyncio
async def test_global_plan_rejects_foreign_occurrence(global_api):
    client, _, _, context = global_api
    response = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "selected_occurrences",
            "occurrence_ids": ["not-this-entity"],
        },
    )

    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]


@pytest.mark.asyncio
async def test_global_plan_rejects_image_conditioned_provider(global_api):
    client, _, runner, context = global_api
    response = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "selected_occurrences",
            "occurrence_ids": [context["appearance_ids"][0]],
            "video_gen_provider": "veo",
        },
    )

    assert response.status_code == 422
    assert "cannot preserve source-video motion" in response.json()["detail"]
    assert runner.submissions == []


@pytest.mark.asyncio
async def test_global_propagation_uses_server_verified_plan(global_api):
    client, sessions, runner, context = global_api
    planned = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "selected_occurrences",
            "occurrence_ids": [context["appearance_ids"][0]],
        },
    )
    plan_id = planned.json()["plan_id"]

    response = await client.post(
        "/api/propagate",
        headers={"X-Session-Id": "global-owner"},
        json={"global_plan_id": plan_id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["global_plan_id"] == plan_id
    assert len(runner.submissions) == 1
    async with sessions() as db:
        plan = await db.get(GlobalEditPlan, plan_id)
        results = (
            await db.execute(
                select(PropagationResult).where(
                    PropagationResult.propagation_job_id == plan.propagation_job_id
                )
            )
        ).scalars().all()
        assert plan.status == "running"
        assert len(results) == 1
        assert results[0].appearance_id == context["appearance_ids"][0]


@pytest.mark.asyncio
async def test_global_rollout_keeps_legacy_propagation_available(global_api):
    client, sessions, runner, context = global_api

    response = await client.post(
        "/api/propagate",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "source_variant_url": "/media/reference.mp4",
            "prompt": "make the jacket blue",
            "auto_apply": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["global_plan_id"] is None
    assert len(runner.submissions) == 1
    async with sessions() as db:
        job = await db.get(
            PropagationJob,
            response.json()["propagation_job_id"],
        )
        assert job.status == "pending"
        assert job.auto_apply is False


@pytest.mark.asyncio
async def test_global_worker_persists_generated_chunk_state(
    global_api,
    monkeypatch,
    tmp_path,
):
    client, sessions, runner, context = global_api
    planned = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "selected_occurrences",
            "occurrence_ids": [context["appearance_ids"][0]],
        },
    )
    plan_id = planned.json()["plan_id"]
    response = await client.post(
        "/api/propagate",
        headers={"X-Session-Id": "global-owner"},
        json={"global_plan_id": plan_id},
    )

    async def prepare_reference_subject(**kwargs):
        return Path(tmp_path / "accepted-subject.png")

    calls = []

    async def execute_global_chunk(**kwargs):
        chunk = kwargs["chunk"]
        previous = kwargs["previous"]
        calls.append((chunk.index, previous))
        return ChunkExecution(
            output_url=f"/media/chunk-{chunk.index}.mp4",
            metadata={"provider": chunk.provider},
        )

    async def assemble_global_occurrence(**kwargs):
        assert [chunk.output_url for chunk in kwargs["chunks"]] == [
            "/media/chunk-0.mp4"
        ]
        return AssemblyResult(
            output_url="/media/occurrence.mp4",
            seams=(),
            continuity={"entry": {"passed": True}, "exit": {"passed": True}},
        )

    monkeypatch.setattr(
        global_edit_job,
        "prepare_reference_subject",
        prepare_reference_subject,
    )
    monkeypatch.setattr(
        global_edit_job,
        "execute_global_chunk",
        execute_global_chunk,
    )
    monkeypatch.setattr(
        global_edit_job,
        "assemble_global_occurrence",
        assemble_global_occurrence,
    )
    await runner.submissions[0][1]()

    assert response.status_code == 200
    assert calls == [(0, None)]
    async with sessions() as db:
        plan = await db.get(GlobalEditPlan, plan_id)
        occurrence = (
            await db.execute(
                select(GlobalOccurrencePlan).where(
                    GlobalOccurrencePlan.global_plan_id == plan_id
                )
            )
        ).scalar_one()
        chunk = (
            await db.execute(
                select(GlobalGenerationChunk).where(
                    GlobalGenerationChunk.occurrence_plan_id == occurrence.id
                )
            )
        ).scalar_one()
        result = (
            await db.execute(
                select(PropagationResult).where(
                    PropagationResult.propagation_job_id == plan.propagation_job_id
                )
            )
        ).scalar_one()
        assert plan.status == "done"
        assert occurrence.status == "done"
        assert occurrence.output_url == "/media/occurrence.mp4"
        assert chunk.status == "generated"
        assert chunk.attempts == 1
        assert chunk.output_url == "/media/chunk-0.mp4"
        assert result.status == "done"
        assert result.variant_url == "/media/occurrence.mp4"

    applied = await client.post(
        f"/api/global-edit-plans/{plan_id}/apply",
        headers={"X-Session-Id": "global-owner"},
    )
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["segment_ids"]) == 1
    segment_id = applied.json()["segment_ids"][0]

    applied_again = await client.post(
        f"/api/global-edit-plans/{plan_id}/apply",
        headers={"X-Session-Id": "global-owner"},
    )
    assert applied_again.status_code == 200
    assert applied_again.json()["segment_ids"] == [segment_id]

    individual = await client.post(
        f"/api/propagate/{response.json()['propagation_job_id']}/apply/{result.id}",
        headers={"X-Session-Id": "global-owner"},
    )
    assert individual.status_code == 409
    assert "one operation" in individual.json()["detail"]

    async with sessions() as db:
        plan = await db.get(GlobalEditPlan, plan_id)
        project = await db.get(Project, context["project_id"])
        result = await db.get(PropagationResult, result.id)
        segment = await db.get(Segment, segment_id)
        assert plan.status == "applied"
        assert project.video_url == "/media/source-v1.mp4"
        assert result.applied is True
        assert result.segment_id == segment.id
        assert segment.start_ts == 8.0
        assert segment.end_ts == 11.0
        edl = PersistedEDL.model_validate(project.timeline_edl)
        global_clip = next(clip for clip in edl.clips if clip.id == segment.id)
        assert global_clip.kind == "generated"
        assert global_clip.source_start == 0.0
        assert global_clip.source_end == 3.0


@pytest.mark.asyncio
async def test_failed_seam_retries_only_failed_work(global_api, monkeypatch, tmp_path):
    client, sessions, runner, context = global_api
    async with sessions() as db:
        appearance = await db.get(EntityAppearance, context["appearance_ids"][0])
        track = (
            await db.execute(
                select(OccurrenceTrack).where(
                    OccurrenceTrack.entity_id == context["entity_id"],
                    OccurrenceTrack.start_ts == 8.0,
                )
            )
        ).scalar_one()
        appearance.end_ts = 25.0
        track.end_ts = 25.0
        await db.commit()

    planned = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "selected_occurrences",
            "occurrence_ids": [context["appearance_ids"][0]],
        },
    )
    plan_id = planned.json()["plan_id"]
    started = await client.post(
        "/api/propagate",
        headers={"X-Session-Id": "global-owner"},
        json={"global_plan_id": plan_id},
    )
    job_id = started.json()["propagation_job_id"]

    async def prepare_reference_subject(**kwargs):
        return Path(tmp_path / "accepted-subject.png")

    generation_calls = []

    async def execute_global_chunk(**kwargs):
        chunk = kwargs["chunk"]
        generation_calls.append(chunk.index)
        return ChunkExecution(
            output_url=f"/media/chunk-{chunk.index}-{len(generation_calls)}.mp4",
            metadata={"provider": chunk.provider},
        )

    assembly_calls = 0

    async def assemble_global_occurrence(**kwargs):
        nonlocal assembly_calls
        assembly_calls += 1
        assert all(chunk.output_url for chunk in kwargs["chunks"])
        if assembly_calls == 1:
            raise GlobalSeamError("overlap did not match", retry_chunk_index=1)
        return AssemblyResult(
            output_url="/media/retried-occurrence.mp4",
            seams=(),
            continuity={"entry": {"passed": True}, "exit": {"passed": True}},
        )

    monkeypatch.setattr(
        global_edit_job, "prepare_reference_subject", prepare_reference_subject
    )
    monkeypatch.setattr(global_edit_job, "execute_global_chunk", execute_global_chunk)
    monkeypatch.setattr(
        global_edit_job, "assemble_global_occurrence", assemble_global_occurrence
    )

    await runner.submissions[0][1]()
    async with sessions() as db:
        plan = await db.get(GlobalEditPlan, plan_id)
        job = await db.get(PropagationJob, job_id)
        chunks = (
            await db.execute(
                select(GlobalGenerationChunk).order_by(GlobalGenerationChunk.index)
            )
        ).scalars().all()
        assert plan.status == "error"
        assert job.status == "error"
        assert len(chunks) == 2
        assert chunks[0].status == "generated"
        assert chunks[0].output_url == "/media/chunk-0-1.mp4"
        assert chunks[0].attempts == 1
        assert chunks[1].status == "planned"
        assert chunks[1].output_url is None
        assert chunks[1].attempts == 1

    retried = await client.post(
        "/api/propagate",
        headers={"X-Session-Id": "global-owner"},
        json={"global_plan_id": plan_id},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["propagation_job_id"] == job_id
    assert len(runner.submissions) == 2
    await runner.submissions[1][1]()

    assert generation_calls == [0, 1, 1]
    async with sessions() as db:
        plan = await db.get(GlobalEditPlan, plan_id)
        job = await db.get(PropagationJob, job_id)
        chunks = (
            await db.execute(
                select(GlobalGenerationChunk).order_by(GlobalGenerationChunk.index)
            )
        ).scalars().all()
        result = (
            await db.execute(
                select(PropagationResult).where(
                    PropagationResult.propagation_job_id == job_id
                )
            )
        ).scalar_one()
        assert plan.status == "done"
        assert job.status == "done"
        assert [chunk.status for chunk in chunks] == ["generated", "generated"]
        assert [chunk.attempts for chunk in chunks] == [1, 2]
        assert result.status == "done"
        assert result.variant_url == "/media/retried-occurrence.mp4"


@pytest.mark.asyncio
async def test_retry_limit_prevents_more_generation(global_api):
    client, sessions, runner, context = global_api
    planned = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "selected_occurrences",
            "occurrence_ids": [context["appearance_ids"][0]],
        },
    )
    plan_id = planned.json()["plan_id"]
    started = await client.post(
        "/api/propagate",
        headers={"X-Session-Id": "global-owner"},
        json={"global_plan_id": plan_id},
    )
    job_id = started.json()["propagation_job_id"]

    async with sessions() as db:
        plan = await db.get(GlobalEditPlan, plan_id)
        job = await db.get(PropagationJob, job_id)
        chunk = (await db.execute(select(GlobalGenerationChunk))).scalar_one()
        plan.status = "error"
        job.status = "error"
        chunk.status = "error"
        chunk.attempts = 3
        await db.commit()

    retried = await client.post(
        "/api/propagate",
        headers={"X-Session-Id": "global-owner"},
        json={"global_plan_id": plan_id},
    )
    assert retried.status_code == 409
    assert "retry limit" in retried.json()["detail"]
    assert len(runner.submissions) == 1


@pytest.mark.asyncio
async def test_plan_limit_rolls_back_all_planning_rows(global_api, monkeypatch):
    client, sessions, runner, context = global_api
    monkeypatch.setattr(get_settings(), "global_edit_max_occurrences", 1)

    response = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "all_occurrences",
        },
    )

    assert response.status_code == 422
    assert "at most 1" in response.json()["detail"]
    assert runner.submissions == []
    async with sessions() as db:
        assert (await db.execute(select(GlobalEditPlan))).scalars().all() == []
        assert (await db.execute(select(GlobalOccurrencePlan))).scalars().all() == []
        assert (await db.execute(select(GlobalGenerationChunk))).scalars().all() == []


@pytest.mark.asyncio
async def test_generation_limit_rolls_back_flushed_plan(global_api, monkeypatch):
    client, sessions, runner, context = global_api
    monkeypatch.setattr(get_settings(), "global_edit_max_generation_calls", 0)

    response = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "selected_occurrences",
            "occurrence_ids": [context["appearance_ids"][0]],
        },
    )

    assert response.status_code == 422
    assert "1 generation calls" in response.json()["detail"]
    assert runner.submissions == []
    async with sessions() as db:
        assert (await db.execute(select(GlobalEditPlan))).scalars().all() == []
        assert (await db.execute(select(GlobalOccurrencePlan))).scalars().all() == []
        assert (await db.execute(select(GlobalGenerationChunk))).scalars().all() == []


@pytest.mark.asyncio
async def test_atomic_apply_rolls_back_when_one_result_is_incomplete(global_api):
    client, sessions, _, context = global_api
    planned = await client.post(
        "/api/global-edit-plans",
        headers={"X-Session-Id": "global-owner"},
        json={
            "entity_id": context["entity_id"],
            "reference_segment_id": context["segment_id"],
            "scope": "all_occurrences",
        },
    )
    plan_id = planned.json()["plan_id"]
    started = await client.post(
        "/api/propagate",
        headers={"X-Session-Id": "global-owner"},
        json={"global_plan_id": plan_id},
    )
    job_id = started.json()["propagation_job_id"]

    async with sessions() as db:
        plan = await db.get(GlobalEditPlan, plan_id)
        job = await db.get(PropagationJob, job_id)
        occurrences = (
            await db.execute(
                select(GlobalOccurrencePlan)
                .where(GlobalOccurrencePlan.global_plan_id == plan_id)
                .order_by(GlobalOccurrencePlan.index)
            )
        ).scalars().all()
        results = (
            await db.execute(
                select(PropagationResult).where(
                    PropagationResult.propagation_job_id == job_id
                )
            )
        ).scalars().all()
        result_by_appearance = {result.appearance_id: result for result in results}
        first_result = result_by_appearance[occurrences[0].appearance_id]
        second_result = result_by_appearance[occurrences[1].appearance_id]
        plan.status = "done"
        job.status = "done"
        first_result.status = "done"
        first_result.variant_url = "/media/first.mp4"
        second_result.status = "error"
        second_result.error = "generation failed"
        await db.commit()
        first_result_id = first_result.id

    applied = await client.post(
        f"/api/global-edit-plans/{plan_id}/apply",
        headers={"X-Session-Id": "global-owner"},
    )
    assert applied.status_code == 422

    async with sessions() as db:
        plan = await db.get(GlobalEditPlan, plan_id)
        first_result = await db.get(PropagationResult, first_result_id)
        global_segments = (
            await db.execute(
                select(Segment).where(
                    Segment.project_id == context["project_id"],
                    Segment.start_ts >= 8.0,
                )
            )
        ).scalars().all()
        assert plan.status == "done"
        assert first_result.applied is False
        assert first_result.segment_id is None
        assert global_segments == []
