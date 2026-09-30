"""
User model — dashboard/human accounts, distinct from Device.

Backend concept: this project now has TWO separate authentication
subjects, and keeping them as separate tables/concepts (rather than one
"accounts" table with a type flag) matters because they have fundamentally
different trust models. A Device authenticates with a long-lived API key
to push sensor data over a WebSocket — it never "logs in" or has a
session. A User authenticates with a password to get a short-lived JWT for
browsing dashboards/history over REST. Conflating them would mean, for
example, building password-reset flows for devices or API-key rotation
for humans — neither of which makes sense.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
