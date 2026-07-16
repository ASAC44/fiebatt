import uuid
from datetime import datetime

from sqlalchemy import (
    Float,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Text,
    UniqueConstraint,
    func,
)
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


class GlobalEditPlan(Base):
    """Non-generating selection of confirmed occurrences and accepted reference."""

    __tablename__ = "global_edit_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    reference_segment_id: Mapped[str] = mapped_column(
        String, ForeignKey("segments.id", ondelete="CASCADE"), index=True
    )
    reference_variant_id: Mapped[str] = mapped_column(
        String, ForeignKey("variants.id", ondelete="CASCADE")
    )
    propagation_job_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("propagation_jobs.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    scope: Mapped[str] = mapped_column(String)
    requested_provider: Mapped[str] = mapped_column(String, default="auto")
    occurrence_ids_json: Mapped[list[str]] = mapped_column(JSON)
    estimate_json: Mapped[dict] = mapped_column(JSON)
    prompt: Mapped[str] = mapped_column(Text)
    source_revision: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    occurrence_plans: Mapped[list["GlobalOccurrencePlan"]] = relationship(
        back_populates="global_plan",
        cascade="all, delete-orphan",
        order_by="GlobalOccurrencePlan.index",
    )


class GlobalOccurrencePlan(Base):
    __tablename__ = "global_occurrence_plans"
    __table_args__ = (
        UniqueConstraint(
            "global_plan_id",
            "appearance_id",
            name="uq_global_plan_appearance",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    global_plan_id: Mapped[str] = mapped_column(
        String, ForeignKey("global_edit_plans.id", ondelete="CASCADE"), index=True
    )
    appearance_id: Mapped[str] = mapped_column(
        String, ForeignKey("entity_appearances.id", ondelete="CASCADE")
    )
    index: Mapped[int] = mapped_column(Integer)
    edit_start: Mapped[float] = mapped_column(Float)
    edit_end: Mapped[float] = mapped_column(Float)
    estimate_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="planned")

    global_plan: Mapped["GlobalEditPlan"] = relationship(
        back_populates="occurrence_plans"
    )
    chunks: Mapped[list["GlobalGenerationChunk"]] = relationship(
        back_populates="occurrence_plan",
        cascade="all, delete-orphan",
        order_by="GlobalGenerationChunk.index",
    )


class GlobalGenerationChunk(Base):
    __tablename__ = "global_generation_chunks"
    __table_args__ = (
        UniqueConstraint(
            "occurrence_plan_id",
            "index",
            name="uq_global_occurrence_chunk_index",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    occurrence_plan_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("global_occurrence_plans.id", ondelete="CASCADE"),
        index=True,
    )
    index: Mapped[int] = mapped_column(Integer)
    edit_start: Mapped[float] = mapped_column(Float)
    edit_end: Mapped[float] = mapped_column(Float)
    context_start: Mapped[float] = mapped_column(Float)
    context_end: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String)
    split_reason: Mapped[str] = mapped_column(String, default="provider_limit")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    input_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_json: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="planned")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    occurrence_plan: Mapped["GlobalOccurrencePlan"] = relationship(
        back_populates="chunks"
    )
