"""
End-to-end integration test for the iris pipeline.
Exercises: upload → generate → accept → entity search → propagate → export
All AI services run in stub mode (USE_AI_STUBS=true).
"""
import os
import asyncio

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# force stub mode before importing the app
os.environ["USE_AI_STUBS"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_pipeline.db"

from app.main import app  # noqa: E402
from app.db.init import create_all  # noqa: E402
from app.workers.runner import JobRunner  # noqa: E402


FIXTURE_VIDEO = os.path.join(os.path.dirname(__file__), "fixtures", "test_5s.mp4")
SESSION_ID = "test-pipeline-session"


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Initialize the database and job runner before tests."""
    await create_all()
    app.state.runner = JobRunner()
    yield
    await app.state.runner.shutdown()
    try:
        os.unlink("./test_pipeline.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def headers():
    return {"X-Session-Id": SESSION_ID}


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    res = await client.get("/api/health", headers=headers())
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["ai_mode"] == "stub"


@pytest.mark.asyncio
async def test_ai_health(client: AsyncClient):
    res = await client.get("/api/ai/health", headers=headers())
    assert res.status_code == 200
    body = res.json()
    assert "total_calls" in body


@pytest.mark.asyncio
async def test_list_projects(client: AsyncClient):
    res = await client.get("/api/projects", headers=headers())
    assert res.status_code == 200
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_upload_and_project(client: AsyncClient):
    if not os.path.exists(FIXTURE_VIDEO):
        pytest.skip("test fixture video not found")

    with open(FIXTURE_VIDEO, "rb") as f:
        res = await client.post(
            "/api/upload",
            files={"file": ("test.mp4", f, "video/mp4")},
            headers=headers(),
        )
    assert res.status_code == 200
    body = res.json()
    assert "project_id" in body
    assert "video_url" in body
    assert body["duration"] > 0
    assert body["fps"] > 0

    # verify project is retrievable
    proj_res = await client.get(f"/api/projects/{body['project_id']}", headers=headers())
    assert proj_res.status_code == 200
    proj = proj_res.json()
    assert proj["project_id"] == body["project_id"]


@pytest.mark.asyncio
async def test_identify_endpoint(client: AsyncClient):
    """identify region returns entity description with dict attributes"""
    if not os.path.exists(FIXTURE_VIDEO):
        pytest.skip("test fixture video not found")

    with open(FIXTURE_VIDEO, "rb") as f:
        upload_res = await client.post(
            "/api/upload",
            files={"file": ("test.mp4", f, "video/mp4")},
            headers=headers(),
        )
    project_id = upload_res.json()["project_id"]

    id_res = await client.post(
        "/api/identify",
        json={
            "project_id": project_id,
            "frame_ts": 1.0,
            "bbox": {"x": 0.25, "y": 0.3, "w": 0.3, "h": 0.3},
        },
        headers=headers(),
    )
    assert id_res.status_code == 200
    body = id_res.json()
    assert "description" in body
    assert "category" in body
    assert "attributes" in body
    assert isinstance(body["attributes"], dict), f"attributes should be dict, got {type(body['attributes'])}"


@pytest.mark.asyncio
async def test_generate_poll_accept(client: AsyncClient):
    """generate → poll → accept → verify timeline"""
    if not os.path.exists(FIXTURE_VIDEO):
        pytest.skip("test fixture video not found")

    # upload
    with open(FIXTURE_VIDEO, "rb") as f:
        upload_res = await client.post(
            "/api/upload",
            files={"file": ("test.mp4", f, "video/mp4")},
            headers=headers(),
        )
    project = upload_res.json()
    project_id = project["project_id"]

    # generate
    gen_res = await client.post(
        "/api/generate",
        json={
            "project_id": project_id,
            "start_ts": 0.0,
            "end_ts": min(2.0, project["duration"]),
            "bbox": {"x": 0.2, "y": 0.3, "w": 0.4, "h": 0.3},
            "prompt": "make the car red with warm cinematic lighting",
            "reference_frame_ts": 1.0,
        },
        headers=headers(),
    )
    assert gen_res.status_code == 200
    job_id = gen_res.json()["job_id"]

    # poll until done
    job = None
    for _ in range(40):
        job_res = await client.get(f"/api/jobs/{job_id}", headers=headers())
        assert job_res.status_code == 200
        job = job_res.json()
        if job["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.5)

    assert job is not None
    assert job["status"] == "done", f"job failed: {job.get('error')}"
    assert len(job["variants"]) > 0
    variant = job["variants"][0]
    assert variant["url"] is not None
    assert "id" in variant
    assert "index" in variant
    assert "status" in variant

    # accept
    accept_res = await client.post(
        "/api/accept",
        json={"job_id": job_id, "variant_index": 0},
        headers=headers(),
    )
    assert accept_res.status_code == 200
    accepted = accept_res.json()
    assert "segment_id" in accepted

    # verify timeline has generated segment
    tl_res = await client.get(f"/api/timeline/{project_id}", headers=headers())
    assert tl_res.status_code == 200
    tl = tl_res.json()
    generated = [s for s in tl["segments"] if s["source"] == "generated"]
    assert len(generated) > 0, "no generated segment after accept"


@pytest.mark.asyncio
async def test_export_pipeline(client: AsyncClient):
    """upload → export (no edits, just original) → verify completion"""
    if not os.path.exists(FIXTURE_VIDEO):
        pytest.skip("test fixture video not found")

    with open(FIXTURE_VIDEO, "rb") as f:
        upload_res = await client.post(
            "/api/upload",
            files={"file": ("test.mp4", f, "video/mp4")},
            headers=headers(),
        )
    project_id = upload_res.json()["project_id"]

    # export
    export_res = await client.post(
        "/api/export",
        json={"project_id": project_id},
        headers=headers(),
    )
    assert export_res.status_code == 200
    export_job_id = export_res.json()["export_job_id"]

    # poll
    es = None
    for _ in range(60):
        es_res = await client.get(f"/api/export/{export_job_id}", headers=headers())
        assert es_res.status_code == 200
        es = es_res.json()
        if es["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.5)

    assert es is not None
    assert es["status"] == "done", f"export failed: {es.get('error')}"
    assert es["export_url"] is not None
