"""
FastAPI application entrypoint.

Backend concept: this module creates the single `app` object that Uvicorn
(the ASGI server) actually runs. Everything else — routers, models, services
— gets wired into this app rather than each file running its own server.
"""
from fastapi import FastAPI
from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.devices import router as devices_router
from app.core.config import get_settings
from app.db.redis import redis_client
from app.db.session import engine
from app.ws.ingest import router as ingest_router

settings = get_settings()

app = FastAPI(
    title="Vehicle Telemetry & Sensor Fusion Backend",
    description="Ingests, fuses, stores, and serves multi-sensor vehicle telemetry (radar, ToF, GPS).",
    version="0.1.0",
)

app.include_router(auth_router)
app.include_router(devices_router)
app.include_router(ingest_router)


@app.get("/health", tags=["system"])
async def health_check():
    """
    Liveness/readiness probe.

    Checks that the API can actually reach Postgres and Redis — not just
    that the Python process is alive. This is what Docker/Kubernetes/CI
    would poll to decide "is this service actually ready to take traffic."
    """
    status = {"api": "ok", "postgres": "unknown", "redis": "unknown"}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["postgres"] = "ok"
    except Exception as e:
        status["postgres"] = f"error: {e}"

    try:
        await redis_client.ping()
        status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"error: {e}"

    return status


@app.get("/", tags=["system"])
async def root():
    return {"message": "Vehicle Telemetry & Sensor Fusion Backend — see /docs for API reference"}
