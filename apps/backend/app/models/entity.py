import uuid
from datetime import datetime

from sqlalchemy import String, Float, DateTime, ForeignKey, JSON, Text, func
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
