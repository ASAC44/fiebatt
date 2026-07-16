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
    PropagationResult,
)
from app.models.segment import Segment


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
    assert runner.submissions == []

    async with sessions() as db:
        occurrence_plans = (
            await db.execute(select(GlobalOccurrencePlan))
        ).scalars().all()
        chunks = (await db.execute(select(GlobalGenerationChunk))).scalars().all()
        assert len(occurrence_plans) == 1
        assert len(chunks) == 1


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
