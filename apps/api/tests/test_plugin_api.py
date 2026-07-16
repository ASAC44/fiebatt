import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models as _models  # noqa: E402, F401
from app.config.settings import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    # Collection order may import the application before this module sets its
    # settings. Install test configuration at fixture time and clear the shared
    # cache so these tests do not inherit configuration from another module.
    monkeypatch.setenv("USE_AI_STUBS", "true")
    monkeypatch.setenv("AUTH_JWT_SECRET", "plugin-test-secret")
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.test")
    get_settings.cache_clear()

    # Override the dependency so these tests always use their own database and
    # never inherit state from another module or a developer DB.
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'plugin_api.db'}"
    )
    test_sessions = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with test_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)
    await test_engine.dispose()
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


async def _signup(client: AsyncClient):
    response = await client.post(
        "/api/auth/signup",
        json={"email": "plugin@example.com", "password": "password123"},
    )
    assert response.status_code == 201
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_pkce_oauth_connects_codex_to_mcp(client: AsyncClient):
    await _signup(client)
    metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == "https://api.example.test/mcp"

    redirect_uri = "http://127.0.0.1:43123/callback"
    registered = await client.post(
        "/oauth/register",
        json={"client_name": "Codex test", "redirect_uris": [redirect_uri]},
    )
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]

    verifier = "codex-test-verifier-with-more-than-forty-three-characters"
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    approved = await client.post(
        "/oauth/authorize",
        data={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "fiebatt:edit projects:read projects:write media:write generation:write",
            "state": "test-state",
            "email": "plugin@example.com",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert approved.status_code == 303
    query = parse_qs(urlparse(approved.headers["location"]).query)
    assert query["state"] == ["test-state"]

    tokens = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": query["code"][0],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200
    oauth_headers = {"Authorization": f"Bearer {tokens.json()['access_token']}"}

    initialized = await client.post(
        "/mcp",
        headers=oauth_headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
    )
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "fiebatt"

    listed = await client.post(
        "/mcp",
        headers=oauth_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert {"account_status", "prepare_upload", "get_project", "generate_edit", "export_video"} <= names

    prepared = await client.post(
        "/mcp",
        headers=oauth_headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "prepare_upload",
                "arguments": {
                    "filename": "sample.mp4",
                    "content_type": "video/mp4",
                    "size_bytes": 12,
                },
            },
        },
    )
    upload_request = prepared.json()["result"]["structuredContent"]
    parsed_upload = urlparse(upload_request["url"])
    received = await client.put(
        f"{parsed_upload.path}?{parsed_upload.query}",
        headers=upload_request["headers"],
        content=b"hello world!",
    )
    assert received.status_code == 200
    assert received.json()["received_bytes"] == 12


@pytest.mark.asyncio
async def test_mcp_advertises_oauth_when_unauthenticated(client: AsyncClient):
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 401
    assert "oauth-protected-resource/mcp" in response.headers["www-authenticate"]
