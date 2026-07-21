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
from app.api.routes import generate as generate_route  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.selection import SelectionArtifact  # noqa: E402
from app.schemas.edit_plan import EditCore, GenerationContext, LocalRangeResolution  # noqa: E402
from app.services.agent_tools import execute_tool  # noqa: E402


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
    assert body["intent"]["duration_policy"] == "bounded_action"
    assert body["intent"]["grounded_edit"]["prompt_for_video_edit"]

    fetched = await client.get(
        f"/api/edit-plans/{body['plan_id']}",
        headers={"X-Session-Id": "plan-owner"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["plan_id"] == body["plan_id"]


@pytest.mark.asyncio
async def test_timeline_snapshot_can_be_saved(client, owned_selection):
    project_id, _ = owned_selection
    response = await client.put(
        f"/api/timeline/{project_id}",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "clips": [
                {
                    "id": "source-clip",
                    "kind": "source",
                    "url": "/media/source.mp4",
                    "source_start": 0.0,
                    "source_end": 30.0,
                    "media_duration": 30.0,
                    "volume": 1.0,
                    "project_id": project_id,
                    "source_asset_id": "source-asset",
                }
            ],
            "sources": [
                {
                    "id": "source-asset",
                    "kind": "source",
                    "url": "/media/source.mp4",
                    "duration": 30.0,
                    "fps": 30.0,
                    "project_id": project_id,
                    "label": "source",
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    saved = await client.get(
        f"/api/timeline/{project_id}",
        headers={"X-Session-Id": "plan-owner"},
    )
    assert saved.status_code == 200
    assert saved.json()["edl"]["clips"][0]["id"] == "source-clip"


@pytest.mark.asyncio
async def test_state_change_plan_covers_current_occurrence(client, owned_selection):
    project_id, selection_id = owned_selection
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this ball pink",
        },
    )

    assert response.status_code == 201, response.text
    intent = response.json()["intent"]
    assert intent["scope"] == "local"
    assert intent["duration_policy"] == "continuous_occurrence"


@pytest.mark.asyncio
async def test_clear_target_mismatch_stops_before_range_and_generation(
    client, owned_selection, monkeypatch
):
    project_id, selection_id = owned_selection
    planning_frames = []

    async def subject_reference_path(url):
        assert url == "/media/subject.png"
        return "/tmp/isolated-subject.png"

    async def mismatched_plan(prompt, bbox, planning_frame):
        planning_frames.append(planning_frame)
        return {
            "decision": {
                "scope": "local",
                "change_type": "motion",
                "duration_policy": "trajectory_continuation",
                "temporal_behavior": "future_changing_motion",
                "target_description": "red double-decker bus",
                "selection_match": "mismatch",
                "selection_match_reason": "The selection contains a bus, not the requested man.",
                "action_phases": ["begin running"],
                "estimated_action_seconds": 3.0,
                "requires_recovery_motion": False,
                "preservation_requirements": ["preserve the background"],
                "reasoning": "the requested person is not selected",
            },
            "variants": [{
                "intent": "transform",
                "description": "Make the man run",
                "region_emphasis": "selected man",
                "prompt_for_video_edit": "Edit only the selected target. Make the selected man run naturally while preserving the source scene. Do not regenerate the scene.",
            }],
        }

    monkeypatch.setattr(route.storage, "path_from_url", subject_reference_path)
    monkeypatch.setattr(route.ai.gemini, "interpret_edit", mismatched_plan)
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make the man run",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "selection_target_mismatch"
    assert "bus" in response.json()["detail"]["message"]
    assert planning_frames == ["/tmp/isolated-subject.png"]


@pytest.mark.asyncio
async def test_long_local_edit_is_split_into_provider_sized_chunks(
    client, owned_selection, monkeypatch
):
    project_id, selection_id = owned_selection

    async def resolve(*args, **kwargs):
        core = EditCore(start_ts=3.0, end_ts=23.0)
        return LocalRangeResolution(
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
            frames_inspected=45,
            tracked_frames=[
                {
                    "timestamp": timestamp,
                    "state": "tracked",
                    "confidence": 0.9,
                    "bbox": {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7},
                }
                for timestamp in range(2, 25)
            ],
            confidence=0.9,
        )

    monkeypatch.setattr(route, "resolve_local_range", resolve)
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this ball pink",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "wan"
    assert body["estimate"]["expected_generation_calls"] == 3
    assert len(body["chunks"]) == 3
    assert all(
        chunk["generation_context"]["end_ts"]
        - chunk["generation_context"]["start_ts"]
        <= 10.0
        for chunk in body["chunks"]
    )
    assert body["chunks"][0]["edit_core"]["end_ts"] == body["chunks"][1]["edit_core"]["start_ts"]

    class FakeRunner:
        def __init__(self):
            self.submissions = []

        def submit(self, job_id, factory):
            self.submissions.append((job_id, factory))

    runner = FakeRunner()
    app.state.runner = runner
    generated = await client.post(
        "/api/generate",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "plan_id": body["plan_id"],
        },
    )

    assert generated.status_code == 200, generated.text
    assert len(runner.submissions) == 1
    factory = runner.submissions[0][1]
    closure_values = [cell.cell_contents for cell in factory.__closure__ or ()]
    assert generate_route.local_chunk_job.run in closure_values

    runner.submissions.clear()
    async with AsyncSessionLocal() as db:
        via_agent = await execute_tool(
            "generate_edit",
            {
                "project_id": project_id,
                "plan_id": body["plan_id"],
                "user_prompt": "make this ball pink",
            },
            db,
            "plan-owner",
            runner=runner,
        )

    assert via_agent["status"] == "pending"
    assert len(runner.submissions) == 1
    factory = runner.submissions[0][1]
    closure_values = [cell.cell_contents for cell in factory.__closure__ or ()]
    assert generate_route.local_chunk_job.run in closure_values


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


@pytest.mark.asyncio
async def test_over_thirty_second_plan_returns_render_limit_card_data(
    client, owned_selection, monkeypatch
):
    project_id, selection_id = owned_selection
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        project.duration = 50.0
        await db.commit()

    async def resolve(*args, **kwargs):
        core = EditCore(start_ts=5.0, end_ts=36.0)
        return LocalRangeResolution(
            edit_core=core,
            generation_context=GenerationContext(
                start_ts=4.25,
                end_ts=36.75,
                edit_core=core,
            ),
            occurrence_start=5.0,
            occurrence_end=36.0,
            analysis_start=4.0,
            analysis_end=37.0,
            confidence=0.9,
        )

    monkeypatch.setattr(route, "resolve_local_range", resolve)
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this ball pink",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "edit_window_too_long"
    assert detail["limit_seconds"] == 30.0
    assert detail["detected_seconds"] == 31.0


@pytest.mark.asyncio
async def test_active_source_clip_must_contain_selection(client, owned_selection):
    project_id, selection_id = owned_selection
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this person jump",
            "source_start_ts": 12.0,
            "source_end_ts": 18.0,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "selection is outside the active source clip"


@pytest.mark.asyncio
async def test_active_source_clip_bounds_reach_resolver(
    client, owned_selection, monkeypatch
):
    project_id, selection_id = owned_selection

    async def resolve(*args, **kwargs):
        assert kwargs["source_start"] == 7.0
        assert kwargs["source_end"] == 13.0
        core = EditCore(start_ts=8.25, end_ts=11.75)
        return LocalRangeResolution(
            edit_core=core,
            generation_context=GenerationContext(
                start_ts=7.5, end_ts=12.5, edit_core=core
            ),
            occurrence_start=7.0,
            occurrence_end=13.0,
            analysis_start=7.0,
            analysis_end=13.0,
            frames_inspected=25,
            confidence=0.9,
        )

    monkeypatch.setattr(route, "resolve_local_range", resolve)
    response = await client.post(
        "/api/edit-plans",
        headers={"X-Session-Id": "plan-owner"},
        json={
            "project_id": project_id,
            "selection_id": selection_id,
            "prompt": "make this person jump",
            "source_start_ts": 7.0,
            "source_end_ts": 13.0,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["occurrence_start"] == 7.0
