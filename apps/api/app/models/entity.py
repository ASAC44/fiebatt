import uuid
from datetime import datetime

from sqlalchemy import (
    String,
    Float,
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


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_segment_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("segments.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    attributes_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reference_crop_url: Mapped[str | None] = mapped_column(String, nullable=True)
    reference_variant_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="entities")  # noqa: F821
    appearances: Mapped[list["EntityAppearance"]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
        order_by="EntityAppearance.start_ts",
    )
    occurrence_candidates: Mapped[list["OccurrenceCandidate"]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
        order_by="OccurrenceCandidate.keyframe_ts",
    )
    occurrence_tracks: Mapped[list["OccurrenceTrack"]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
        order_by="OccurrenceTrack.start_ts",
    )


class EntityAppearance(Base):
    __tablename__ = "entity_appearances"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    segment_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("segments.id", ondelete="SET NULL"), nullable=True
    )
    start_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float] = mapped_column(Float)
    keyframe_url: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    entity: Mapped["Entity"] = relationship(back_populates="appearances")


class OccurrenceCandidate(Base):
    """Coarse, cached identity hit awaiting dense track confirmation."""

    __tablename__ = "occurrence_candidates"
    __table_args__ = (
        UniqueConstraint("entity_id", "cache_key", name="uq_occurrence_candidate_cache"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    source_revision: Mapped[str] = mapped_column(String)
    cache_key: Mapped[str] = mapped_column(String, index=True)
    keyframe_ts: Mapped[float] = mapped_column(Float)
    start_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float] = mapped_column(Float)
    keyframe_url: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="candidate")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    entity: Mapped["Entity"] = relationship(back_populates="occurrence_candidates")
    track: Mapped["OccurrenceTrack | None"] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        uselist=False,
    )


class OccurrenceTrack(Base):
    """Dense SAM2 confirmation and bounded per-frame tracking evidence."""

    __tablename__ = "occurrence_tracks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("occurrence_candidates.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    source_revision: Mapped[str] = mapped_column(String)
    seed_ts: Mapped[float] = mapped_column(Float)
    start_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    tracker: Mapped[str] = mapped_column(String)
    frames_json: Mapped[list[dict]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="confirmed")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    entity: Mapped["Entity"] = relationship(back_populates="occurrence_tracks")
    candidate: Mapped["OccurrenceCandidate"] = relationship(back_populates="track")
