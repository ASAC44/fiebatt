from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Session(Base):
    __tablename__ = "sessions"

    # id format:
    #   • anon compatibility → random uuid (from X-Session-Id header)
    #   • signed-in users → "user:{user_id}" (stable across devices)
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # populated when the request carried a verified first-party jwt.
    # null for anon sessions.
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    projects: Mapped[list["Project"]] = relationship(  # noqa: F821
        back_populates="session", cascade="all, delete-orphan"
    )
