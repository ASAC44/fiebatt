import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, String, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    video_path: Mapped[str] = mapped_column(String)
    video_url: Mapped[str] = mapped_column(String)
    duration: Mapped[float] = mapped_column(Float)
    fps: Mapped[float] = mapped_column(Float)
    width: Mapped[int] = mapped_column(default=0)
    height: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # persisted EDL (edit decision list). When null, timeline/export fall
    # back to reconstructing from Segment rows + implicit originals — that's
    # what first-opened projects do. Once the user touches anything in the
    # studio (split, trim, delete, reorder, volume, add-from-library, or
    # accept a variant), we snapshot the full frontend state here so reopens
    # and exports reflect exactly what they saw on screen.
    # Shape: { "clips": [...], "sources": [...], "updated_at": <epoch> }
    timeline_edl: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )

    session: Mapped["Session"] = relationship(back_populates="projects")  # noqa: F821
    segments: Mapped[list["Segment"]] = relationship(  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
    entities: Mapped[list["Entity"]] = relationship(  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
