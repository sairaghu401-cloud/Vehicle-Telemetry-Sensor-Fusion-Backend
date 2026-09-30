"""
WebSocket ingestion endpoint — where devices push live sensor readings.

Backend concept — the WebSocket lifecycle, since it's structurally
different from a REST endpoint:

  1. `websocket.accept()` completes the handshake (like returning 200 on
     a normal request, but the connection then STAYS OPEN).
  2. We loop `await websocket.receive_json()` — each call blocks until the
     client sends another message, or the connection closes. This is the
     "full-duplex, long-lived" part: one coroutine, parked here, handling
     an arbitrary number of messages over however long the device stays
     connected — versus a REST handler, which runs once per request and
     returns.
  3. `WebSocketDisconnect` is how FastAPI tells us the client hung up
     (device rebooted, lost signal, etc.) — not an error condition to log
     loudly, just the normal way this loop ends.
"""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from app.api.deps import authenticate_device_by_api_key
from app.db.redis import redis_client
from app.db.session import AsyncSessionLocal
from app.models.sensor_reading import SensorReading
from app.schemas.ingestion import IngestAck, IngestError, IngestReading
from app.services.fusion import try_fuse_reading
from app.services.live_state import set_latest_reading

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingestion"])


@router.websocket("/ws/ingest")
async def ingest_readings(websocket: WebSocket, api_key: str):
    """
    Devices connect to ws://host/ws/ingest?api_key=sk_live_...

    Backend concept — why authenticate BEFORE accept(), not after: a
    WebSocket handshake is itself an HTTP request that gets "upgraded."
    Rejecting bad credentials with `close(code=...)` before ever calling
    `accept()` means a misconfigured/malicious client never gets a live
    connection at all — it fails at the handshake, the same way a REST
    endpoint would return 401 without doing any work.

    We open ONE database session for the whole connection's lifetime
    (not one per message) because sessions/connections are relatively
    expensive to set up — reusing one across many small writes from the
    same device is the efficient pattern, mirroring how the connection
    itself is long-lived.
    """
    async with AsyncSessionLocal() as db:
        device = await authenticate_device_by_api_key(api_key, db)
        if device is None:
            # 1008 = "Policy Violation" — the standard WebSocket close code
            # for "your credentials/behavior aren't acceptable," roughly
            # analogous to HTTP 401/403.
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or unknown API key")
            return

        await websocket.accept()
        logger.info(f"Device {device.device_code} ({device.id}) connected for ingestion")

        try:
            while True:
                raw_message = await websocket.receive_json()

                try:
                    reading_in = IngestReading.model_validate(raw_message)
                except ValidationError as e:
                    # A malformed message from THIS device doesn't kill the
                    # connection — we tell it what was wrong and keep
                    # listening, the same way a REST 422 doesn't end the
                    # client's session. Only a transport-level problem
                    # (WebSocketDisconnect, below) ends the loop.
                    await websocket.send_json(IngestError(detail=str(e)).model_dump())
                    continue

                reading = SensorReading(
                    device_id=device.id,
                    sensor_type=reading_in.sensor_type,
                    recorded_at=reading_in.recorded_at,
                    distance_m=reading_in.distance_m,
                    latitude=reading_in.latitude,
                    longitude=reading_in.longitude,
                )
                db.add(reading)
                await db.commit()
                await db.refresh(reading)

                # Update Redis's "current state" AFTER the Postgres commit
                # succeeds — Postgres is the durable source of truth, so we
                # only advertise a reading as "current" once it's safely
                # persisted. If Redis were updated first and the DB write
                # then failed, a dashboard could show a reading that was
                # never actually saved.
                await set_latest_reading(
                    redis_client,
                    device_id=str(device.id),
                    sensor_type=reading.sensor_type.value,
                    recorded_at=reading.recorded_at,
                    distance_m=reading.distance_m,
                    latitude=reading.latitude,
                    longitude=reading.longitude,
                )

                await websocket.send_json(IngestAck(reading_id=reading.id).model_dump())

                # Attempt fusion AFTER the ack is sent — the device doesn't
                # need to wait on fusion to know its reading was stored;
                # fusion is a downstream enrichment of already-durable
                # data, not a precondition for acknowledging receipt. If
                # fusion finds a matching reading from the other sensor
                # within the time window, it writes a FusedEvent; if not
                # (e.g. this is the very first reading from this sensor),
                # try_fuse_reading just returns None and we move on.
                await try_fuse_reading(db, reading)

        except WebSocketDisconnect:
            logger.info(f"Device {device.device_code} ({device.id}) disconnected")
        except json.JSONDecodeError:
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA, reason="Message was not valid JSON")
