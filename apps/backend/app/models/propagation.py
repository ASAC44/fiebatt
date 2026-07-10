import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PropagationJob(Base):
    __tablename__ = "propagation_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    source_variant_url: Mapped[str] = mapped_column(String)
    prompt: Mapped[str] = mapped_column(Text)
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    results: Mapped[list["PropagationResult"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class PropagationResult(Base):
    __tablename__ = "propagation_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    propagation_job_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("propagation_jobs.id", ondelete="CASCADE"),
        index=True,
    )
    appearance_id: Mapped[str] = mapped_column(
        String, ForeignKey("entity_appearances.id", ondelete="CASCADE")
    )
    segment_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("segments.id", ondelete="SET NULL"), nullable=True
    )
    variant_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["PropagationJob"] = relationship(back_populates="results")
