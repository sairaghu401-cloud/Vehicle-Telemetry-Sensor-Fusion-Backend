"""Pydantic schemas for the SensorReading resource (historical queries)."""
import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from app.models.sensor_reading import SensorType

T = TypeVar("T")


class SensorReadingOut(BaseModel):
    id: int
    device_id: uuid.UUID
    sensor_type: SensorType
    recorded_at: datetime
    distance_m: float | None
    latitude: float | None
    longitude: float | None
    ingested_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Generic pagination envelope, reused for any list endpoint.

    Backend concept: instead of ever returning a bare JSON array for a
    list endpoint, we wrap it with metadata about the page itself. A bare
    array can't tell the client "there are 400 more rows after this" — the
    client would have no way to know whether to ask for more. `total`,
    `page`, and `page_size` together let a client compute how many pages
    exist and build "next/previous" controls.
    """

    items: list[T]
    total: int
    page: int
    page_size: int
