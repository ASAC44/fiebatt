import uuid
from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, JSON, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # 'generate' | 'entity' | 'propagate' | 'export'
    kind: Mapped[str] = mapped_column(String)
    # 'pending' | 'processing' | 'done' | 'error'
    status: Mapped[str] = mapped_column(String, default="pending")

    # generate-specific fields; nullable for other kinds
    start_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_frame_ts: Mapped[float | None] = mapped_column(Float, nullable=True)

    # generic payload for other job kinds
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    project: Mapped["Project"] = relationship(back_populates="jobs")  # noqa: F821
    variants: Mapped[list["Variant"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Variant.index",
    )


class Variant(Base):
    __tablename__ = "variants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String, ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_coherence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_adherence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="variants")
