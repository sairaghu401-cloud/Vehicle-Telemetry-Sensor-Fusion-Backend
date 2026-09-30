"""
Importing all models here means a single `import app.models` (which
alembic/env.py does) registers every table on Base.metadata — Alembic's
autogenerate needs every model imported somewhere to "see" it when diffing
against the database.
"""
from app.models.device import Device
from app.models.sensor_reading import SensorReading, SensorType
from app.models.fused_event import FusedEvent, FusionStatus
from app.models.user import User

__all__ = ["Device", "SensorReading", "SensorType", "FusedEvent", "FusionStatus", "User"]
