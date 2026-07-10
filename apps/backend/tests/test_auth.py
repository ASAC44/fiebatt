import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ["USE_AI_STUBS"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_auth.db"
os.environ["AUTH_JWT_SECRET"] = "test-secret"

from app.db.init import create_all  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await create_all()
    yield
    try:
        os.unlink("./test_auth.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def signup(client: AsyncClient, email: str = "test@example.com") -> dict:
    res = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "password123"},
    )
    assert res.status_code == 201
    return res.json()


@pytest.mark.asyncio
async def test_signup_returns_jwt_and_me(client: AsyncClient):
    body = await signup(client)
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "test@example.com"

    me = await client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["signed_in"] is True
    assert me.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_duplicate_signup_conflicts(client: AsyncClient):
    await signup(client)
    res = await client.post(
        "/api/auth/signup",
        json={"email": "TEST@example.com", "password": "password123"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_login_success_and_failure(client: AsyncClient):
    await signup(client)
    bad = await client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "wrongpass"},
    )
    assert bad.status_code == 401

    good = await client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "password123"},
    )
    assert good.status_code == 200
    assert good.json()["access_token"]


@pytest.mark.asyncio
async def test_jwt_session_can_access_project_list(client: AsyncClient):
    body = await signup(client)
    res = await client.get(
        "/api/projects",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert res.status_code == 200
    assert res.json() == []
