"""
Schema for one inbound WebSocket ingestion message.

This is deliberately the same shape a device would send whether it's
radar, ToF, or GPS — one message = one reading, matching how a real sensor
actually produces data (each sample arrives on its own, not pre-grouped).
"""
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.sensor_reading import SensorType


class IngestReading(BaseModel):
    sensor_type: SensorType
    recorded_at: datetime = Field(..., description="Timestamp the DEVICE took this reading (its own clock)")

    distance_m: float | None = Field(None, description="Required for radar/tof, omitted for gps")
    latitude: float | None = Field(None, description="Required for gps, omitted for radar/tof")
    longitude: float | None = Field(None, description="Required for gps, omitted for radar/tof")

    @model_validator(mode="after")
    def check_fields_match_sensor_type(self) -> "IngestReading":
        """
        Cross-field validation: Pydantic can check each field in isolation
        (is distance_m a float?), but "distance_m is required WHEN
        sensor_type is radar" spans two fields, so it needs a
        model-level validator that runs after individual fields are
        parsed. This catches a malformed device payload (e.g. firmware
        bug sending GPS coords under a "radar" reading) before it reaches
        the database at all, rather than silently storing nonsense.
        """
        if self.sensor_type in (SensorType.RADAR, SensorType.TOF):
            if self.distance_m is None:
                raise ValueError(f"distance_m is required for sensor_type={self.sensor_type.value}")
        elif self.sensor_type == SensorType.GPS:
            if self.latitude is None or self.longitude is None:
                raise ValueError("latitude and longitude are required for sensor_type=gps")
        return self


class IngestAck(BaseModel):
    """Sent back to the device after each reading is successfully stored."""

    status: str = "ok"
    reading_id: int


class IngestError(BaseModel):
    """Sent back to the device if a message couldn't be processed."""

    status: str = "error"
    detail: str
