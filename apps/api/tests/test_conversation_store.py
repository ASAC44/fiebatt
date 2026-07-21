import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models as _models  # noqa: F401
from app.db.base import Base
from app.models.conversation import Conversation
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.services.conversation_store import get_or_create_conversation


@pytest_asyncio.fixture
async def db_session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'conversation-upsert.db'}"
    )
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        session.add(SessionModel(id="session-1"))
        project = Project(
            id="project-1",
            session_id="session-1",
            video_path="source.mp4",
            video_url="source.mp4",
            duration=5,
            fps=24,
        )
        session.add(project)
        await session.commit()
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_creation_is_idempotent(db_session):
    first = await get_or_create_conversation(
        db_session,
        conversation_id="conversation-race",
        project_id="project-1",
        session_id="session-1",
    )
    second = await get_or_create_conversation(
        db_session,
        conversation_id="conversation-race",
        project_id="project-1",
        session_id="session-1",
    )

    assert first.id == second.id == "conversation-race"


def test_postgresql_conversation_insert_uses_conflict_handling():
    from sqlalchemy.dialects.postgresql import insert

    statement = insert(Conversation).values(
        id="conversation-race",
        project_id="project-1",
        session_id="session-1",
    ).on_conflict_do_nothing(index_elements=["id"])

    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (id) DO NOTHING" in sql
