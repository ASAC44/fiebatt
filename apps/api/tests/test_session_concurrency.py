import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models as _models  # noqa: F401
from app.db.base import Base
from app.deps import _ensure_session_row, _session_insert_statement
from app.models.session import Session as SessionModel


@pytest_asyncio.fixture
async def db_session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'session-upsert.db'}"
    )
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_anonymous_session_creation_is_idempotent(db_session):
    first = await _ensure_session_row(db_session, sid="same-anonymous-browser")
    await db_session.commit()
    second = await _ensure_session_row(db_session, sid="same-anonymous-browser")
    await db_session.commit()

    count = await db_session.scalar(
        select(func.count())
        .select_from(SessionModel)
        .where(SessionModel.id == "same-anonymous-browser")
    )
    assert first.id == second.id
    assert count == 1


def test_postgres_session_creation_uses_atomic_conflict_handling():
    statement = _session_insert_statement(
        "postgresql",
        {"id": "same-browser", "user_id": None, "email": None},
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "ON CONFLICT (id) DO NOTHING" in sql
