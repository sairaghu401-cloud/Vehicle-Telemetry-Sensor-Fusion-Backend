"""
Pydantic schemas for the Device resource.

Backend concept: why not just return the SQLAlchemy Device model directly
from an endpoint? Two reasons.

1. Leakage: Device has an `api_key_hash` column. If FastAPI serializes the
   ORM object straight to JSON, that hash goes out over the wire — even
   though it's a hash, it's still an internal implementation detail that
   should never be handed to an API consumer.

2. Coupling: the ORM model's shape IS your database schema. If the API's
   response shape is exactly that schema, you can never change one without
   also changing the other. A dedicated "response schema" is a deliberate,
   separate contract — you choose exactly which fields are public.

The convention: an "In" schema shapes what the CLIENT sends you (a
request body); an "Out" (or unqualified) schema shapes what you send BACK.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceCreate(BaseModel):
    """Request body for POST /devices."""

    name: str = Field(..., min_length=1, max_length=100, examples=["Front bumper radar unit"])
    device_code: str = Field(..., min_length=1, max_length=50, examples=["ESP32-A1"])


class DeviceCreateResponse(BaseModel):
    """
    Response for POST /devices — the ONLY time the raw API key is ever
    shown. After this, only its hash exists in the database, so if the
    caller loses it, the only fix is generating a new one (not implemented
    yet — a good step 6 addition).
    """

    id: uuid.UUID
    name: str
    device_code: str
    api_key: str = Field(..., description="Store this now — it will never be shown again.")
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeviceOut(BaseModel):
    """
    Public representation of a Device — used by GET /devices and
    GET /devices/{id}. Deliberately excludes api_key_hash.
    """

    id: uuid.UUID
    name: str
    device_code: str
    is_active: bool
    created_at: datetime

    # from_attributes=True lets Pydantic build this schema directly from a
    # SQLAlchemy ORM object's attributes (device.id, device.name, ...)
    # instead of requiring a dict — this is what makes
    # `DeviceOut.model_validate(some_orm_device)` work.
    model_config = ConfigDict(from_attributes=True)
