"""
Device model — one row per physical device registered with the system.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Device(Base):
    __tablename__ = "devices"

    # Why UUID instead of an auto-incrementing integer as the primary key:
    # devices are created/registered by clients (e.g. a device provisioning
    # a itself on first boot) rather than always going through one central
    # API call that could hand out sequential IDs. UUIDs can be generated
    # independently (even offline) with no coordination and no risk of two
    # devices colliding on the same ID. The tradeoff: UUIDs are 16 bytes vs
    # 4-8 bytes for an int, and don't sort chronologically — a fine trade
    # here since we're not indexing millions of devices, unlike readings
    # below where we DO want a cheap auto-incrementing key.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # A short human-readable identifier separate from the UUID, e.g. "ESP32-A1".
    # Unique so two devices can't register with the same external label.
    device_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    # Hashed API key used to authenticate this device's ingestion requests
    # (step 6). We store a hash, never the raw key — same principle as
    # password storage: if the DB ever leaks, raw keys don't leak with it.
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # One device -> many readings. `relationship` doesn't create a DB
    # column; it's a Python-level convenience so you can do
    # `device.readings` in code and SQLAlchemy fetches the related rows.
    readings: Mapped[list["SensorReading"]] = relationship(back_populates="device")
