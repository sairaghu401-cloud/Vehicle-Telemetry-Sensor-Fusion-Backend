"""Pydantic schema for the FusedEvent resource."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.fused_event import FusionStatus


class FusedEventOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    status: FusionStatus
    fused_distance_m: float | None
    discrepancy_m: float | None
    source_reading_ids: str
    notes: str | None
    event_time: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
