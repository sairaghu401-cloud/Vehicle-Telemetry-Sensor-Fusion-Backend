"""
Integration tests for the /ws/ingest WebSocket endpoint (step 4) and its
interaction with sensor fusion (step 5) and Redis live-state.

Uses httpx-ws's `aconnect_ws(url, client)` against a client opened with
`ws_capable_client()` (see conftest.py for why that's a plain context
manager opened inline in each test, not a shared fixture).

`registered_device` still comes from the normal `client`/`auth_headers`
fixtures (registering a device is a plain REST call) — only the actual
WebSocket connection needs the WS-capable client.
"""
import pytest
from datetime import datetime, timezone

from httpx_ws import WebSocketDisconnect, aconnect_ws

from app.db.session import AsyncSessionLocal
from app.db.redis import redis_client
from tests.conftest import ws_capable_client


async def test_valid_api_key_can_ingest_and_gets_acked(registered_device):
    api_key = registered_device["api_key"]
    device_id = registered_device["id"]

    async with ws_capable_client() as ws_client:
        async with aconnect_ws(f"/ws/ingest?api_key={api_key}", ws_client) as ws:
            await ws.send_json(
                {
                    "sensor_type": "radar",
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "distance_m": 4.2,
                }
            )
            ack = await ws.receive_json()
            assert ack["status"] == "ok"
            assert isinstance(ack["reading_id"], int)

    # The reading should be durably in Postgres...
    from sqlalchemy import select
    from app.models.sensor_reading import SensorReading

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SensorReading).where(SensorReading.device_id == device_id))
        readings = result.scalars().all()
    assert len(readings) == 1
    assert readings[0].distance_m == 4.2

    # ...and Redis's "current state" should reflect it too (see
    # app/services/live_state.py — updated only AFTER the DB commit).
    raw = await redis_client.get(f"device:{device_id}:latest")
    assert raw is not None


async def test_invalid_api_key_is_rejected_at_handshake():
    async with ws_capable_client() as ws_client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            async with aconnect_ws("/ws/ingest?api_key=sk_live_not_a_real_key", ws_client):
                pass
        # 1008 = WS_1008_POLICY_VIOLATION — see app/ws/ingest.py's
        # docstring on why auth is checked before accept().
        assert exc_info.value.code == 1008


async def test_malformed_message_gets_error_ack_but_connection_stays_open(registered_device):
    api_key = registered_device["api_key"]

    async with ws_capable_client() as ws_client:
        async with aconnect_ws(f"/ws/ingest?api_key={api_key}", ws_client) as ws:
            # Missing distance_m for a radar reading — fails the
            # cross-field validator in app/schemas/ingestion.py.
            await ws.send_json(
                {"sensor_type": "radar", "recorded_at": datetime.now(timezone.utc).isoformat()}
            )
            error = await ws.receive_json()
            assert error["status"] == "error"

            # The connection is still alive — prove it by sending a GOOD
            # message right after and getting a normal ack back.
            await ws.send_json(
                {
                    "sensor_type": "radar",
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "distance_m": 4.2,
                }
            )
            ack = await ws.receive_json()
            assert ack["status"] == "ok"


async def test_radar_and_tof_within_tolerance_produce_an_ok_fused_event(registered_device):
    """
    End-to-end check that ingestion actually triggers fusion (step 5) —
    not just that each reading gets stored, but that sending radar then
    ToF within the match window produces a FusedEvent with status=ok.
    """
    api_key = registered_device["api_key"]
    device_id = registered_device["id"]
    now = datetime.now(timezone.utc).isoformat()

    async with ws_capable_client() as ws_client:
        async with aconnect_ws(f"/ws/ingest?api_key={api_key}", ws_client) as ws:
            await ws.send_json({"sensor_type": "radar", "recorded_at": now, "distance_m": 4.0})
            await ws.receive_json()
            await ws.send_json({"sensor_type": "tof", "recorded_at": now, "distance_m": 4.2})
            await ws.receive_json()

    from sqlalchemy import select
    from app.models.fused_event import FusedEvent, FusionStatus

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(FusedEvent).where(FusedEvent.device_id == device_id))
        events = result.scalars().all()

    assert len(events) == 1
    assert events[0].status == FusionStatus.OK
