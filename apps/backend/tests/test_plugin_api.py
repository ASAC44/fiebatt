import base64
import hashlib
import os
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ["USE_AI_STUBS"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_plugin_api.db"
os.environ["AUTH_JWT_SECRET"] = "plugin-test-secret"
os.environ["PUBLIC_API_URL"] = "https://api.example.test"

from app.db.init import create_all  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


async def _remove_test_database():
    # SQLite keeps deleted files alive through pooled connections. Dispose the
    # pool first so every test really starts with an empty database.
    await engine.dispose()
    try:
        os.unlink("./test_plugin_api.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await _remove_test_database()
    await create_all()
    yield
    await _remove_test_database()


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
async def test_provider_keys_are_write_only(client: AsyncClient):
    token = await _signup(client)
    headers = {"Authorization": f"Bearer {token}"}
    saved = await client.put(
        "/api/providers/gemini",
        headers=headers,
        json={"api_key": "secret-provider-key-1234"},
    )
    assert saved.status_code == 200
    assert saved.json()["key_hint"] == "1234"

    listed = await client.get("/api/providers", headers=headers)
    payload = listed.json()
    assert listed.status_code == 200
    assert next(item for item in payload if item["provider"] == "gemini")["configured"] is True
    assert "secret-provider-key" not in listed.text


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
