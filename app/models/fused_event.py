"""
FusedEvent model — output of the sensor fusion logic (step 5): a decision
made by cross-validating multiple raw readings (e.g. "radar and ToF agree
on distance" vs. "radar and ToF disagree — flagged as an anomaly").
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class FusionStatus(str, enum.Enum):
    OK = "ok"                # sensors agree within tolerance
    ANOMALY = "anomaly"      # sensors disagree beyond tolerance
    INSUFFICIENT_DATA = "insufficient_data"  # not enough readings to fuse


class FusedEvent(Base):
    __tablename__ = "fused_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    # Same values_callable fix as SensorType — store "ok"/"anomaly"/
    # "insufficient_data" in Postgres, matching the Python .value and the
    # API's JSON wire format, instead of SQLAlchemy's default of storing
    # the uppercase Python member name.
    status: Mapped[FusionStatus] = mapped_column(
        Enum(FusionStatus, name="fusion_status_enum", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
    )

    # The fused/reconciled distance value the fusion logic settled on.
    # Nullable for INSUFFICIENT_DATA events, where there's no fused value.
    fused_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # How far apart the source readings were, in meters — the actual
    # number behind an ANOMALY classification. Lets a dashboard show "off
    # by 2.3m" instead of just a yes/no flag.
    discrepancy_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # IDs of the raw sensor_readings rows that went into this fusion,
    # stored as a comma-separated string of bigints.
    # Design tradeoff: a proper many-to-many join table (fused_event_id,
    # reading_id) is the "correct" normalized way to do this, and would let
    # you query "which fused events used reading X" efficiently. We're
    # using a simple string here because we only ever read this list one
    # direction (event -> its source readings, for audit/debugging), never
    # query it the other way — so the join table's extra complexity isn't
    # paying for itself yet. If that access pattern changes later, this is
    # a good candidate to refactor into a real join table.
    source_reading_ids: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    device: Mapped["Device"] = relationship()

    __table_args__ = (
        Index("ix_fused_events_device_time", "device_id", "event_time"),
    )
