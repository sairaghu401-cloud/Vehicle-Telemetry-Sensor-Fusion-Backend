"""
SensorReading model — raw, unprocessed readings pushed by devices.

This is the highest-volume table in the system (every radar/ToF/GPS sample
lands here), so its design choices are about write throughput and query
speed at scale, not just correctness.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Float, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class SensorType(str, enum.Enum):
    RADAR = "radar"
    TOF = "tof"
    GPS = "gps"


class SensorReading(Base):
    __tablename__ = "sensor_readings"

    # Plain auto-incrementing BigInteger here, unlike Device's UUID.
    # Why the difference: this table can grow into the tens of millions of
    # rows, and every row gets inserted by our own server (never generated
    # independently offline), so there's no coordination problem to solve —
    # we just want the smallest, fastest-to-index key. A sequential
    # BigInteger is 8 bytes, sorts naturally by insertion order, and is
    # cheaper for Postgres to index than a random UUID at this volume.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    # values_callable tells SQLAlchemy to store each enum member's .value
    # ("radar") in Postgres, instead of its default of storing the Python
    # member NAME ("RADAR"). This matters because our API will accept and
    # return JSON like {"sensor_type": "radar"} — lowercase is the natural
    # REST/JSON convention — and we want the DB's stored values, the
    # Python .value, and the API's wire format to all agree, rather than
    # silently diverging (a real bug we hit and fixed while building this).
    sensor_type: Mapped[SensorType] = mapped_column(
        Enum(SensorType, name="sensor_type_enum", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
    )

    # The device's own timestamp for when the reading was taken (may differ
    # slightly from when it arrived at the server — network/queueing delay).
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Distance reading in meters, used by radar and ToF sensors.
    # Nullable because GPS readings don't populate this field.
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # GPS fields — nullable because radar/ToF readings don't populate these.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Server-side arrival time — always set by the DB, useful for spotting
    # ingestion lag (recorded_at vs. ingested_at).
    # server_default must be wrapped in text() so SQLAlchemy/Alembic emit
    # it as the literal SQL function call now() — passing a plain string
    # instead makes Postgres store it as a quoted literal timestamp (frozen
    # to whenever the migration ran), not a live "current time" default.
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    device: Mapped["Device"] = relationship(back_populates="readings")

    __table_args__ = (
        # Composite index: our main query pattern (step 3) is "give me all
        # readings for device X between time A and B" — this index lets
        # Postgres jump straight to the right device's rows already sorted
        # by time, instead of scanning the whole table and sorting after.
        Index("ix_sensor_readings_device_time", "device_id", "recorded_at"),
    )
