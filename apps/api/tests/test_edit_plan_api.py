import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ["USE_AI_STUBS"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_edit_plan_api.db"

from app.api.routes import edit_plans as route  # noqa: E402
from app.db.init import create_all  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.selection import SelectionArtifact  # noqa: E402
from app.schemas.edit_plan import EditCore, GenerationContext, LocalRangeResolution  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await create_all()
    yield
    try:
        os.unlink("./test_edit_plan_api.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


@pytest_asyncio.fixture
async def owned_selection(client: AsyncClient):
    await client.get("/api/projects", headers={"X-Session-Id": "plan-owner"})
    async with AsyncSessionLocal() as db:
        project = Project(
            session_id="plan-owner",
            video_path="/tmp/source.mp4",
            video_url="/media/source.mp4",
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
            bbox_json={"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
            contours_json=[],
            mask_url="/media/mask.png",
            subject_reference_url="/media/subject.png",
            sam_score=0.91,
            source_revision=project.video_url,
        )
        db.add(selection)
        await db.commit()
        return project.id, selection.id


@pytest.fixture(autouse=True)
def fake_resolver(monkeypatch):
    async def resolve(*args, **kwargs):
        core = EditCore(start_ts=8.25, end_ts=11.75)
        return LocalRangeResolution(
            edit_core=core,
            generation_context=GenerationContext(
                start_ts=7.5, end_ts=12.5, edit_core=core
            ),
            occurrence_start=5.0,
            occurrence_end=17.0,
            analysis_start=6.5,
            analysis_end=13.5,
            frames_inspected=29,
            confidence=0.88,
            warnings=["fixture warning"],
        )

    monkeypatch.setattr(route, "resolve_local_range", resolve)


@pytest.mark.asyncio
async def test_create_and_get_non_generating_plan(client, owned_selection):
    project_id, selection_id = owned_selection
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this person jump",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scope"] == "local"
    assert body["provider"] == "wan"
    assert body["edit_core"] == {"start_ts": 8.25, "end_ts": 11.75}
    assert body["generation_context"]["start_ts"] == 7.5
    assert body["estimate"]["expected_generation_calls"] == 1
    assert body["estimate"]["analysis_duration_ms"] >= 0.0
    assert body["adaptive_generation_enabled"] is True
    assert "fixture warning" in body["warnings"]
    assert not any("legacy fixed window" in warning for warning in body["warnings"])
    assert len(body["chunks"]) == 1

    fetched = await client.get(
        f"/api/edit-plans/{body['plan_id']}",
        headers={"X-Session-Id": "plan-owner"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["plan_id"] == body["plan_id"]


@pytest.mark.asyncio
async def test_plan_ownership_is_hidden(client, owned_selection):
    project_id, selection_id = owned_selection
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "different-session"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this person jump",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_invalid_explicit_range_fails_before_analysis(client, owned_selection):
    project_id, selection_id = owned_selection
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this person jump",
            "explicit_start_ts": 12.0,
            "explicit_end_ts": 10.0,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_provider_constraints_fail_before_generation(client, owned_selection):
    project_id, selection_id = owned_selection
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this person jump",
            "video_gen_provider": "veo",
        },
    )
    assert response.status_code == 422
    assert "exactly 4, 6, 8 seconds" in response.json()["detail"]
