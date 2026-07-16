import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class SelectionArtifact(Base):
    """Reusable visual target selected on one immutable source frame."""

    __tablename__ = "selection_artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    frame_ts: Mapped[float] = mapped_column(Float)
    bbox_json: Mapped[dict[str, float]] = mapped_column(JSON)
    contours_json: Mapped[list[list[list[float]]]] = mapped_column(JSON, default=list)
    mask_url: Mapped[str] = mapped_column(String)
    subject_reference_url: Mapped[str | None] = mapped_column(String, nullable=True)
    sam_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_revision: Mapped[str] = mapped_column(String)
    entity_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="selection_artifacts")  # noqa: F821
