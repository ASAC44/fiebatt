import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class EditPlanRecord(Base):
    __tablename__ = "edit_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    selection_id: Mapped[str] = mapped_column(
        String, ForeignKey("selection_artifacts.id", ondelete="CASCADE"), index=True
    )
    raw_prompt: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String)
    intent_json: Mapped[dict] = mapped_column(JSON)
    range_json: Mapped[dict] = mapped_column(JSON)
    estimate_json: Mapped[dict] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String)
    provider_reason: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_revision: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="edit_plans")  # noqa: F821
    chunks: Mapped[list["GenerationChunk"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="GenerationChunk.index",
    )


class GenerationChunk(Base):
    __tablename__ = "generation_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(
        String, ForeignKey("edit_plans.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int] = mapped_column(Integer, default=0)
    edit_start: Mapped[float] = mapped_column(Float)
    edit_end: Mapped[float] = mapped_column(Float)
    context_start: Mapped[float] = mapped_column(Float)
    context_end: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String)
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="planned")

    plan: Mapped[EditPlanRecord] = relationship(back_populates="chunks")
