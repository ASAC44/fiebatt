"""
Tests for the agent chat endpoint and agent tool dispatch layer.
Exercises: SSE streaming, request validation, helper functions, tool dispatch,
ownership checks, and tool-specific logic.
All AI services run in stub mode (USE_AI_STUBS=true).
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# force stub mode before importing the app
os.environ["USE_AI_STUBS"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_agent.db"

from app.main import app  # noqa: E402
from app.db.init import create_all  # noqa: E402
from app.workers.runner import JobRunner  # noqa: E402
from app.api.routes.agent import (  # noqa: E402
    AgentChatRequest,
    _build_messages,
    _resolve_plan_selection_id,
    _clean_agent_text,
    _detached_agent_relay,
    _parse_dsml_tool_calls,
    sse_event,
)
from app.services.agent_tools import execute_tool  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.config.settings import get_settings  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.selection import SelectionArtifact  # noqa: E402


FIXTURE_VIDEO = os.path.join(os.path.dirname(__file__), "fixtures", "test_5s.mp4")
SESSION_ID = "test-agent-session"


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Initialize the database and job runner before tests."""
    await create_all()
    app.state.runner = JobRunner()
    yield
    await app.state.runner.shutdown()
    try:
        os.unlink("./test_agent.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db_session():
    """Yield a raw async DB session for direct tool tests."""
    async for session in get_db():
        yield session


def headers():
    return {"X-Session-Id": SESSION_ID}


async def upload_fixture_video(client: AsyncClient) -> dict:
    """Upload the test fixture video and return the response body."""
    if not os.path.exists(FIXTURE_VIDEO):
        pytest.skip("test fixture video not found")

    with open(FIXTURE_VIDEO, "rb") as f:
        res = await client.post(
            "/api/upload",
            files={"file": ("test.mp4", f, "video/mp4")},
            headers=headers(),
        )
    assert res.status_code == 200
    return res.json()


@pytest.mark.asyncio
async def test_agent_resolves_matching_selection_when_frontend_mask_finishes_late(
    client: AsyncClient,
    db_session,
):
    upload = await upload_fixture_video(client)
    project = await db_session.get(Project, upload["project_id"])
    assert project is not None
    bbox = {"x": 0.2, "y": 0.3, "w": 0.4, "h": 0.25}
    artifact = SelectionArtifact(
        project_id=project.id,
        frame_ts=1.0,
        bbox_json=bbox,
        contours_json=[],
        mask_url="/media/keyframes/test-mask.png",
        source_revision=project.video_url,
    )
    db_session.add(artifact)
    await db_session.commit()
    await db_session.refresh(artifact)

    resolved = await _resolve_plan_selection_id(
        AgentChatRequest(
            project_id=project.id,
            message="make this car green",
            conversation_id="selection-race",
            bbox=bbox,
        )
    )

    assert resolved == artifact.id


@pytest.mark.asyncio
async def test_agent_persists_bbox_target_when_sam_selection_is_unavailable(
    client: AsyncClient,
    db_session,
):
    upload = await upload_fixture_video(client)
    project = await db_session.get(Project, upload["project_id"])
    assert project is not None
    bbox = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}

    resolved = await _resolve_plan_selection_id(
        AgentChatRequest(
            project_id=project.id,
            message="make this bus yellow",
            conversation_id="bbox-fallback",
            playhead_ts=1.25,
            bbox=bbox,
        )
    )

    assert resolved is not None
    artifact = await db_session.get(SelectionArtifact, resolved)
    assert artifact is not None
    assert artifact.bbox_json == bbox
    assert artifact.sam_score is None
    assert artifact.contours_json


def _make_mock_agent_response(
    text: str = "I can help you edit that video.",
    *,
    tool_calls: list[MagicMock] | None = None,
):
    """Build one OpenAI-compatible chat completion response."""
    message = MagicMock()
    message.content = text
    message.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(name: str, arguments: dict) -> MagicMock:
    tool_call = MagicMock()
    tool_call.id = f"call-{name}"
    tool_call.function.name = name
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call


def _mock_agent_client(*responses) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=list(responses) if len(responses) > 1 else None,
        return_value=responses[0] if len(responses) == 1 else None,
    )
    return client


@pytest.fixture
def live_agent_settings(monkeypatch):
    monkeypatch.setenv("USE_AI_STUBS", "false")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("MESH_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _parse_sse_events(raw: str) -> list[dict]:
    """Parse raw SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = None

    for line in raw.split("\n"):
        if line.startswith("event: "):
            current_event = line[len("event: "):]
        elif line.startswith("data: "):
            current_data = line[len("data: "):]
        elif line == "" and current_event is not None and current_data is not None:
            events.append({"event": current_event, "data": json.loads(current_data)})
            current_event = None
            current_data = None

    return events


# ---- SSE helper tests ----


class TestSseEvent:
    """Test the sse_event() helper function directly."""

    def test_basic_format(self):
        result = sse_event("token", {"text": "hello"})
        assert result == 'event: token\ndata: {"text": "hello"}\n\n'

    def test_nested_dicts(self):
        data = {"edit": {"job_id": "abc", "bbox": {"x": 0.1, "y": 0.2}}}
        result = sse_event("suggestion", data)
        assert result.startswith("event: suggestion\ndata: ")
        assert result.endswith("\n\n")
        parsed = json.loads(result.split("data: ")[1].strip())
        assert parsed["edit"]["bbox"]["x"] == 0.1

    def test_special_characters(self):
        data = {"text": 'He said "hello" & <goodbye>'}
        result = sse_event("token", data)
        parsed = json.loads(result.split("data: ")[1].strip())
        assert parsed["text"] == 'He said "hello" & <goodbye>'

    def test_empty_data(self):
        result = sse_event("done", {})
        assert result == "event: done\ndata: {}\n\n"

    def test_unicode(self):
        data = {"text": "emoji test: \u2728\u2764\ufe0f"}
        result = sse_event("token", data)
        parsed = json.loads(result.split("data: ")[1].strip())
        assert "\u2728" in parsed["text"]


def test_dsml_tool_markup_is_parsed_and_hidden():
    raw = """I will inspect it now.
<｜DSML｜function_calls>
<｜DSML｜invoke name="analyze_video">
<｜DSML｜parameter name="project_id" string="true">project-1</｜DSML｜parameter>
<｜DSML｜parameter name="fps" string="false">2</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜function_calls>"""

    assert _clean_agent_text(raw) == "I will inspect it now."
    calls = _parse_dsml_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].function.name == "analyze_video"
    assert json.loads(calls[0].function.arguments) == {
        "project_id": "project-1",
        "fps": 2,
    }


def test_malformed_dsml_prefix_is_never_user_visible():
    assert _clean_agent_text("Starting <DSML functioncall") == "Starting"


# ---- _build_contents tests ----


class TestBuildMessages:
    """Test the _build_messages() helper."""

    def test_empty_history_with_message(self):
        messages = _build_messages(None, "Hello agent")
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello agent"

    def test_empty_list_history(self):
        messages = _build_messages([], "Hello agent")
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_history_with_user_and_model(self):
        history = [
            {"role": "user", "text": "Hi"},
            {"role": "model", "text": "Hello! How can I help?"},
        ]
        messages = _build_messages(history, "Edit the car")
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hi"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hello! How can I help?"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "Edit the car"

    def test_model_role_maps_to_assistant(self):
        history = [
            {"role": "model", "text": "I am a model"},
        ]
        messages = _build_messages(history, "ok")
        assert messages[0]["role"] == "assistant"

    def test_missing_content_defaults_to_empty(self):
        history = [{"role": "user"}]
        messages = _build_messages(history, "hello")
        assert len(messages) == 2
        assert messages[0]["content"] == ""


@pytest.mark.asyncio
async def test_agent_turn_survives_closed_browser_relay():
    """Closing SSE delivery must not cancel backend agent orchestration."""
    release = asyncio.Event()
    completed = asyncio.Event()

    async def source():
        yield sse_event("token", {"text": "planning"})
        await release.wait()
        completed.set()
        yield sse_event("done", {})

    relay = _detached_agent_relay(source())
    first = await anext(relay)
    assert "planning" in first
    await relay.aclose()

    release.set()
    await asyncio.wait_for(completed.wait(), timeout=1)


# ---- agent chat endpoint tests ----


@pytest.mark.asyncio
async def test_agent_chat_request_validation(client: AsyncClient):
    """Missing required fields return 422."""
    # completely empty body
    res = await client.post("/api/agent/chat", json={}, headers=headers())
    assert res.status_code == 422

    # missing message
    res = await client.post(
        "/api/agent/chat",
        json={"project_id": "abc", "conversation_id": "conv1"},
        headers=headers(),
    )
    assert res.status_code == 422

    # missing project_id
    res = await client.post(
        "/api/agent/chat",
        json={"message": "hello", "conversation_id": "conv1"},
        headers=headers(),
    )
    assert res.status_code == 422

    # missing conversation_id
    res = await client.post(
        "/api/agent/chat",
        json={"project_id": "abc", "message": "hello"},
        headers=headers(),
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_agent_chat_endpoint_exists(client: AsyncClient, live_agent_settings):
    """POST /api/agent/chat with a valid body returns 200 with SSE content type.

    Mocks Gemini to avoid needing an API key.
    """
    mock_response = _make_mock_agent_response("I can help you edit that video.")
    mock_client = _mock_agent_client(mock_response)

    with patch("app.api.routes.agent.AsyncOpenAI", return_value=mock_client):
        res = await client.post(
            "/api/agent/chat",
            json={
                "project_id": "some-project",
                "message": "Hello",
                "conversation_id": "conv-1",
            },
            headers=headers(),
        )

    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]

    events = _parse_sse_events(res.text)
    event_types = [e["event"] for e in events]
    assert "token" in event_types
    assert "done" in event_types

    # verify the token text is what we mocked
    token_event = next(e for e in events if e["event"] == "token")
    assert token_event["data"]["text"] == "I can help you edit that video."


@pytest.mark.asyncio
async def test_agent_chat_no_api_key(client: AsyncClient, monkeypatch):
    """Without a model gateway key, the endpoint streams an error event."""
    monkeypatch.setenv("USE_AI_STUBS", "false")
    monkeypatch.setenv("MESH_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    try:
        res = await client.post(
            "/api/agent/chat",
            json={
                "project_id": "some-project",
                "message": "Hello",
                "conversation_id": "conv-1",
            },
            headers=headers(),
        )
    finally:
        get_settings.cache_clear()

    assert res.status_code == 200
    events = _parse_sse_events(res.text)
    event_types = [e["event"] for e in events]
    assert "error" in event_types
    assert "done" in event_types

    error_event = next(e for e in events if e["event"] == "error")
    assert "No AI API key" in error_event["data"]["message"]


@pytest.mark.asyncio
async def test_agent_chat_missing_project(client: AsyncClient, live_agent_settings):
    """POST /api/agent/chat with a non-existent project_id still starts streaming.

    The endpoint itself doesn't validate project_id -- that happens during tool
    execution. So we expect a 200 with SSE events.
    """
    mock_response = _make_mock_agent_response("Let me look at that project.")
    mock_client = _mock_agent_client(mock_response)

    with patch("app.api.routes.agent.AsyncOpenAI", return_value=mock_client):
        res = await client.post(
            "/api/agent/chat",
            json={
                "project_id": "nonexistent-project",
                "message": "Edit the car",
                "conversation_id": "conv-2",
            },
            headers=headers(),
        )

    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]


@pytest.mark.asyncio
async def test_sse_event_format_in_stream(client: AsyncClient, live_agent_settings):
    """Verify SSE events in the stream are properly formatted with event/data lines."""
    mock_response = _make_mock_agent_response("Testing SSE format.")
    mock_client = _mock_agent_client(mock_response)

    with patch("app.api.routes.agent.AsyncOpenAI", return_value=mock_client):
        res = await client.post(
            "/api/agent/chat",
            json={
                "project_id": "p1",
                "message": "test",
                "conversation_id": "conv-3",
            },
            headers=headers(),
        )

    # Each SSE event should have "event: ...\ndata: ...\n\n" format
    raw = res.text
    assert "event: token\n" in raw
    assert "event: done\n" in raw
    # data lines should be valid JSON
    for line in raw.split("\n"):
        if line.startswith("data: "):
            json.loads(line[len("data: "):])


# ---- tool dispatch tests ----


@pytest.mark.asyncio
async def test_tool_dispatch_unknown(db_session):
    """execute_tool with an unrecognized name raises ValueError."""
    with pytest.raises(ValueError, match="unknown tool"):
        await execute_tool("nonexistent_tool", {}, db_session, SESSION_ID)


@pytest.mark.asyncio
async def test_tool_analyze_video(client: AsyncClient, db_session):
    """analyze_video tool returns status, project_id, and duration."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]

    result = await execute_tool(
        "analyze_video",
        {"project_id": project_id},
        db_session,
        SESSION_ID,
    )

    assert result["status"] == "done"
    assert result["project_id"] == project_id
    assert result["duration"] > 0
    assert result["fps_sampled"] == 1.0
    assert result["frame_count"] > 0
    assert "analysis" in result


@pytest.mark.asyncio
async def test_tool_analyze_video_custom_fps(client: AsyncClient, db_session):
    """analyze_video respects the fps argument."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]

    result = await execute_tool(
        "analyze_video",
        {"project_id": project_id, "fps": 2.0},
        db_session,
        SESSION_ID,
    )

    assert result["fps_sampled"] == 2.0
    assert result["frame_count"] == int(result["duration"] * 2.0)


@pytest.mark.asyncio
async def test_tool_get_timeline(client: AsyncClient, db_session):
    """get_timeline returns a segments list for a valid project."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]

    result = await execute_tool(
        "get_timeline",
        {"project_id": project_id},
        db_session,
        SESSION_ID,
    )

    assert result["project_id"] == project_id
    assert result["duration"] > 0
    assert isinstance(result["segments"], list)


@pytest.mark.asyncio
async def test_tool_generate_edit_segment_too_short(client: AsyncClient, db_session):
    """generate_edit raises ValueError when segment is less than 2s."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]

    with pytest.raises(ValueError, match="segment length must be 2-15s"):
        await execute_tool(
            "generate_edit",
            {
                "project_id": project_id,
                "start_ts": 0.0,
                "end_ts": 1.0,
                "bbox": {"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3},
                "prompt": "make it red",
            },
            db_session,
            SESSION_ID,
            runner=app.state.runner,
        )


@pytest.mark.asyncio
async def test_tool_generate_edit_segment_too_long(client: AsyncClient, db_session):
    """generate_edit raises ValueError when segment exceeds 5s."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]

    with pytest.raises(ValueError, match="segment length must be 2-15s"):
        await execute_tool(
            "generate_edit",
            {
                "project_id": project_id,
                "start_ts": 0.0,
                "end_ts": 16.0,
                "bbox": {"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3},
                "prompt": "make it blue",
            },
            db_session,
            SESSION_ID,
            runner=app.state.runner,
        )


@pytest.mark.asyncio
async def test_tool_generate_edit_invalid_project(db_session):
    """generate_edit with a nonexistent project_id raises ValueError."""
    with pytest.raises(ValueError, match="project not found or access denied"):
        await execute_tool(
            "generate_edit",
            {
                "project_id": "nonexistent-id",
                "start_ts": 0.0,
                "end_ts": 3.0,
                "bbox": {"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3},
                "prompt": "make it green",
            },
            db_session,
            SESSION_ID,
            runner=app.state.runner,
        )


@pytest.mark.asyncio
async def test_tool_generate_edit_valid(client: AsyncClient, db_session):
    """generate_edit with valid args creates a job and returns job_id."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]
    duration = upload["duration"]

    end_ts = min(3.0, duration)
    if end_ts - 0.0 < 2.0:
        pytest.skip("fixture video too short for a 2s segment")

    result = await execute_tool(
        "generate_edit",
        {
            "project_id": project_id,
            "start_ts": 0.0,
            "end_ts": end_ts,
            "bbox": {"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3},
            "prompt": "make the car red",
        },
        db_session,
        SESSION_ID,
        runner=app.state.runner,
    )

    assert "job_id" in result
    assert result["status"] == "pending"

    jobs = await client.get(
        f"/api/projects/{project_id}/generation-jobs",
        headers=headers(),
    )
    assert jobs.status_code == 200
    restored = next(job for job in jobs.json() if job["job_id"] == result["job_id"])
    assert restored["accepted"] is False
    assert restored["created_at"]


@pytest.mark.asyncio
async def test_tool_export_video(client: AsyncClient, db_session):
    """export_video creates an export job and returns export_job_id."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]

    result = await execute_tool(
        "export_video",
        {"project_id": project_id},
        db_session,
        SESSION_ID,
        runner=app.state.runner,
    )

    assert "export_job_id" in result
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_tool_ownership_check(client: AsyncClient, db_session):
    """Tool with the wrong session_id raises ValueError (access denied)."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]

    wrong_session = "wrong-session-id"
    with pytest.raises(ValueError, match="project not found or access denied"):
        await execute_tool(
            "analyze_video",
            {"project_id": project_id},
            db_session,
            wrong_session,
        )


@pytest.mark.asyncio
async def test_tool_ownership_check_timeline(client: AsyncClient, db_session):
    """get_timeline with wrong session_id raises ValueError."""
    upload = await upload_fixture_video(client)
    project_id = upload["project_id"]

    with pytest.raises(ValueError, match="project not found or access denied"):
        await execute_tool(
            "get_timeline",
            {"project_id": project_id},
            db_session,
            "imposter-session",
        )


@pytest.mark.asyncio
async def test_tool_get_job_status_not_found(db_session):
    """get_job_status with a nonexistent job_id raises ValueError."""
    with pytest.raises(ValueError, match="job not found"):
        await execute_tool(
            "get_job_status",
            {"job_id": "nonexistent-job-id"},
            db_session,
            SESSION_ID,
        )


@pytest.mark.asyncio
async def test_agent_chat_gateway_error(client: AsyncClient, live_agent_settings):
    """When the model gateway raises, the stream includes an error event."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=Exception("API rate limit exceeded")
    )

    with patch("app.api.routes.agent.AsyncOpenAI", return_value=mock_client):
        res = await client.post(
            "/api/agent/chat",
            json={
                "project_id": "p1",
                "message": "Hello",
                "conversation_id": "conv-err",
            },
            headers=headers(),
        )

    assert res.status_code == 200
    events = _parse_sse_events(res.text)
    event_types = [e["event"] for e in events]
    assert "error" in event_types
    assert "done" in event_types

    error_event = next(e for e in events if e["event"] == "error")
    assert "API rate limit exceeded" in error_event["data"]["message"]


@pytest.mark.asyncio
async def test_agent_chat_with_function_call(client: AsyncClient, live_agent_settings):
    """When the gateway returns a function call, tool events appear."""
    first_response = _make_mock_agent_response(
        "",
        tool_calls=[_make_tool_call("analyze_video", {"project_id": "test-proj"})],
    )
    text_response = _make_mock_agent_response("Analysis complete!")
    mock_client = _mock_agent_client(first_response, text_response)

    with patch("app.api.routes.agent.AsyncOpenAI", return_value=mock_client), \
         patch("app.api.routes.agent.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {
            "status": "done",
            "project_id": "test-proj",
            "duration": 5.0,
        }

        res = await client.post(
            "/api/agent/chat",
            json={
                "project_id": "test-proj",
                "message": "Analyze my video",
                "conversation_id": "conv-fc",
            },
            headers=headers(),
        )

    assert res.status_code == 200
    events = _parse_sse_events(res.text)
    event_types = [e["event"] for e in events]

    assert "tool_call_start" in event_types
    assert "tool_call_end" in event_types
    assert "token" in event_types
    assert "done" in event_types

    # verify tool_call_start has the right shape
    tc_start = next(e for e in events if e["event"] == "tool_call_start")
    assert tc_start["data"]["tool"] == "analyze_video"
    assert "id" in tc_start["data"]
    assert "args" in tc_start["data"]

    # verify tool_call_end has result
    tc_end = next(e for e in events if e["event"] == "tool_call_end")
    assert tc_end["data"]["status"] == "done"
    assert tc_end["data"]["result"]["project_id"] == "test-proj"


@pytest.mark.asyncio
async def test_agent_chat_recovers_raw_dsml_tool_call(client: AsyncClient, live_agent_settings):
    raw_call = """I will inspect it.
<｜DSML｜function_calls>
<｜DSML｜invoke name="analyze_video">
<｜DSML｜parameter name="project_id" string="true">test-proj</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜function_calls>"""
    mock_client = _mock_agent_client(
        _make_mock_agent_response(raw_call),
        _make_mock_agent_response("Inspection complete."),
    )

    with patch("app.api.routes.agent.AsyncOpenAI", return_value=mock_client), \
         patch("app.api.routes.agent.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {
            "status": "done",
            "project_id": "test-proj",
            "duration": 5.0,
        }
        res = await client.post(
            "/api/agent/chat",
            json={
                "project_id": "test-proj",
                "message": "Inspect this video",
                "conversation_id": "conv-dsml",
            },
            headers=headers(),
        )

    events = _parse_sse_events(res.text)
    visible_text = "".join(
        event["data"]["text"] for event in events if event["event"] == "token"
    )
    assert visible_text == "I will inspect it.Inspection complete."
    assert "DSML" not in res.text
    assert mock_exec.await_args.kwargs["args"] == {"project_id": "test-proj"}


@pytest.mark.asyncio
async def test_generate_tool_keeps_original_user_prompt(client: AsyncClient, live_agent_settings):
    """Model-authored generation text must not replace user attribution."""
    response = _make_mock_agent_response(
        "",
        tool_calls=[_make_tool_call("generate_edit", {
            "project_id": "test-proj",
            "start_ts": 0,
            "end_ts": 3,
            "bbox": {"x": 0, "y": 0, "w": 1, "h": 1},
            "prompt": "A lime-green automobile with stable geometry.",
        })],
    )
    mock_client = _mock_agent_client(response)

    async def no_plan_events(*_args, **_kwargs):
        if False:
            yield ""

    with patch("app.api.routes.agent.AsyncOpenAI", return_value=mock_client), \
         patch("app.api.routes.agent.execute_tool", new_callable=AsyncMock) as mock_exec, \
         patch("app.api.routes.agent._bridge_plan_events", no_plan_events):
        mock_exec.return_value = {"job_id": "job-1", "status": "pending"}
        res = await client.post(
            "/api/agent/chat",
            json={
                "project_id": "test-proj",
                "message": "make the car green",
                "conversation_id": "conv-original-prompt",
            },
            headers=headers(),
        )

    assert res.status_code == 200
    args = mock_exec.await_args.kwargs["args"]
    assert args["prompt"] == "A lime-green automobile with stable geometry."
    assert args["user_prompt"] == "make the car green"
