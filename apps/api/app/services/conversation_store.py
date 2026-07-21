"""Concurrency-safe conversation lookup for detached agent turns."""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


async def get_or_create_conversation(
    db: AsyncSession,
    *,
    conversation_id: str,
    project_id: str,
    session_id: str,
) -> Conversation:
    values = {
        "id": conversation_id,
        "project_id": project_id,
        "session_id": session_id,
    }
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "postgresql":
        statement = postgresql_insert(Conversation).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["id"])
    elif dialect == "sqlite":
        statement = sqlite_insert(Conversation).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["id"])
    else:
        existing = await db.get(Conversation, conversation_id)
        if existing is None:
            existing = Conversation(**values)
            db.add(existing)
            await db.flush()
        return existing

    await db.execute(statement)
    await db.commit()
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise RuntimeError("conversation could not be created")
    if conversation.project_id != project_id or conversation.session_id != session_id:
        raise ValueError("conversation does not belong to this project session")
    return conversation
