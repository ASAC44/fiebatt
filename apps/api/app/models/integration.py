from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String, primary_key=True)
    client_name: Mapped[str] = mapped_column(String, default="Codex")
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_authorization_codes"

    code_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(String, ForeignKey("oauth_clients.client_id", ondelete="CASCADE"))
    redirect_uri: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class OAuthRefreshToken(Base):
    __tablename__ = "oauth_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(String, ForeignKey("oauth_clients.client_id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UploadIntent(Base):
    __tablename__ = "upload_intents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    object_key: Mapped[str] = mapped_column(String, unique=True)
    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
