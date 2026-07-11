"""Conversation persistence routes.

CRUD for agent chat conversations and messages, stored in Postgres.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.deps import get_session
from app.models.conversation import Conversation, ChatMessage
from app.models.project import Project
from app.models.session import Session as SessionModel

log = logging.getLogger("iris.conversations")
router = APIRouter(tags=["conversations"])


# ---- schemas ----

class MessageIn(BaseModel):
    role: str
    content: dict[str, Any]


class BulkMessagesIn(BaseModel):
    messages: list[MessageIn]


class ConversationOut(BaseModel):
    id: str
    project_id: str
    title: str | None
    created_at: str
    updated_at: str
    message_count: int


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: dict[str, Any]
    created_at: str


# ---- helpers ----

async def _own_project(db: AsyncSession, project_id: str, session_id: str) -> Project:
    proj = await db.get(Project, project_id)
    if proj is None or proj.session_id != session_id:
        from fastapi import HTTPException
        raise HTTPException(404, "project not found")
    return proj


# ---- routes ----

@router.get("/projects/{project_id}/conversations")
async def list_conversations(
    project_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationOut]:
    """List all conversations for a project, newest first."""
    await _own_project(db, project_id, session.id)

    result = await db.execute(
        select(Conversation)
        .where(Conversation.project_id == project_id, Conversation.session_id == session.id)
        .options(selectinload(Conversation.messages))
        .order_by(Conversation.updated_at.desc())
    )
    convos = result.scalars().all()

    return [
        ConversationOut(
            id=c.id,
            project_id=c.project_id,
            title=c.title,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
            message_count=len(c.messages),
        )
        for c in convos
    ]


@router.post("/projects/{project_id}/conversations")
async def create_conversation(
    project_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """Create a new conversation for a project."""
    await _own_project(db, project_id, session.id)

    convo = Conversation(
        project_id=project_id,
        session_id=session.id,
    )
    db.add(convo)
    await db.commit()
    await db.refresh(convo)

    return ConversationOut(
        id=convo.id,
        project_id=convo.project_id,
        title=convo.title,
        created_at=convo.created_at.isoformat(),
        updated_at=convo.updated_at.isoformat(),
        message_count=0,
    )


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    """Get all messages in a conversation, ordered by time."""
    convo = await db.get(Conversation, conversation_id)
    if convo is None or convo.session_id != session.id:
        from fastapi import HTTPException
        raise HTTPException(404, "conversation not found")

    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at)
    )
    msgs = result.scalars().all()

    return [
        MessageOut(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            created_at=m.created_at.isoformat(),
        )
        for m in msgs
    ]


@router.post("/conversations/{conversation_id}/messages")
async def add_messages(
    conversation_id: str,
    body: BulkMessagesIn,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    """Append one or more messages to a conversation."""
    convo = await db.get(Conversation, conversation_id)
    if convo is None or convo.session_id != session.id:
        from fastapi import HTTPException
        raise HTTPException(404, "conversation not found")

    created: list[ChatMessage] = []
    for msg in body.messages:
        m = ChatMessage(
            conversation_id=conversation_id,
            role=msg.role,
            content=msg.content,
        )
        db.add(m)
        created.append(m)

    await db.commit()
    for m in created:
        await db.refresh(m)

    return [
        MessageOut(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            created_at=m.created_at.isoformat(),
        )
        for m in created
    ]


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: dict[str, Any],
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """Update conversation metadata (title, etc.)."""
    convo = (
        await db.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages))
        )
    ).scalar_one_or_none()

    if convo is None or convo.session_id != session.id:
        from fastapi import HTTPException
        raise HTTPException(404, "conversation not found")

    if "title" in body:
        convo.title = body["title"]

    await db.commit()
    await db.refresh(convo)

    return ConversationOut(
        id=convo.id,
        project_id=convo.project_id,
        title=convo.title,
        created_at=convo.created_at.isoformat(),
        updated_at=convo.updated_at.isoformat(),
        message_count=len(convo.messages),
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Delete a conversation and all its messages."""
    convo = await db.get(Conversation, conversation_id)
    if convo is None or convo.session_id != session.id:
        from fastapi import HTTPException
        raise HTTPException(404, "conversation not found")

    await db.delete(convo)
    await db.commit()

    return {"status": "deleted", "conversation_id": conversation_id}
