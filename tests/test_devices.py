"""
Integration tests for /devices/* — registration, listing, 404 handling,
and the paginated history/fused-events endpoints.

Every endpoint under /devices now requires auth (step 6) — the
`auth_headers` fixture (tests/conftest.py) logs a fresh user in once per
test and hands back a ready `Authorization` header.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import AsyncSessionLocal
from app.models.fused_event import FusedEvent, FusionStatus
from app.models.sensor_reading import SensorReading, SensorType


async def test_register_device_returns_raw_api_key_once(client, auth_headers):
    resp = await client.post(
        "/devices",
        json={"name": "Front bumper radar", "device_code": "ESP32-A1"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["device_code"] == "ESP32-A1"
    assert body["api_key"].startswith("sk_live_")

    # The list/get endpoints return DeviceOut, which has no api_key or
    # api_key_hash field at all — confirm it actually stays hidden there.
    listed = await client.get("/devices", headers=auth_headers)
    assert "api_key" not in listed.json()[0]
    assert "api_key_hash" not in listed.json()[0]


async def test_register_device_without_auth_is_rejected(client):
    resp = await client.post("/devices", json={"name": "x", "device_code": "ESP32-NOAUTH"})
    assert resp.status_code == 401


async def test_register_device_duplicate_code_is_rejected(client, auth_headers):
    payload = {"name": "Dup", "device_code": "ESP32-DUP"}
    first = await client.post("/devices", json=payload, headers=auth_headers)
    assert first.status_code == 201

    second = await client.post("/devices", json=payload, headers=auth_headers)
    assert second.status_code == 409


async def test_get_unknown_device_is_404(client, auth_headers):
    resp = await client.get(f"/devices/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


async def test_get_device_returns_it(client, auth_headers, registered_device):
    resp = await client.get(f"/devices/{registered_device['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["device_code"] == registered_device["device_code"]


async def test_live_state_is_null_when_nothing_ingested_yet(client, auth_headers, registered_device):
    resp = await client.get(f"/devices/{registered_device['id']}/live", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["latest_reading"] is None


async def _seed_readings(device_id: str, count: int, sensor_type: SensorType = SensorType.RADAR):
    """Inserts `count` SensorReading rows directly via the ORM, spaced one
    minute apart — there's no REST endpoint for creating raw readings
    (only the WebSocket does that, tested separately), so history/
    pagination tests seed data this way instead of going through a live
    WebSocket connection just to get rows into the table."""
    base_time = datetime.now(timezone.utc) - timedelta(hours=1)
    async with AsyncSessionLocal() as db:
        for i in range(count):
            db.add(
                SensorReading(
                    device_id=uuid.UUID(device_id),
                    sensor_type=sensor_type,
                    recorded_at=base_time + timedelta(minutes=i),
                    distance_m=4.0 + i * 0.01,
                )
            )
        await db.commit()


async def test_history_pagination(client, auth_headers, registered_device):
    device_id = registered_device["id"]
    await _seed_readings(device_id, count=5)

    page1 = await client.get(f"/devices/{device_id}/history?page=1&page_size=2", headers=auth_headers)
    assert page1.status_code == 200
    body1 = page1.json()
    assert body1["total"] == 5
    assert body1["page"] == 1
    assert len(body1["items"]) == 2

    page2 = await client.get(f"/devices/{device_id}/history?page=2&page_size=2", headers=auth_headers)
    body2 = page2.json()
    assert len(body2["items"]) == 2

    # Pages shouldn't overlap: no reading id appears on both pages.
    ids_page1 = {item["id"] for item in body1["items"]}
    ids_page2 = {item["id"] for item in body2["items"]}
    assert ids_page1.isdisjoint(ids_page2)


async def test_history_filter_by_sensor_type(client, auth_headers, registered_device):
    device_id = registered_device["id"]
    await _seed_readings(device_id, count=3, sensor_type=SensorType.RADAR)
    await _seed_readings(device_id, count=2, sensor_type=SensorType.GPS)

    resp = await client.get(f"/devices/{device_id}/history?sensor_type=gps", headers=auth_headers)
    body = resp.json()
    assert body["total"] == 2
    assert all(item["sensor_type"] == "gps" for item in body["items"])


async def test_history_invalid_time_range_is_rejected(client, auth_headers, registered_device):
    device_id = registered_device["id"]
    resp = await client.get(
        f"/devices/{device_id}/history?from=2026-01-02T00:00:00&to=2026-01-01T00:00:00",
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def _seed_fused_event(device_id: str, status: FusionStatus):
    async with AsyncSessionLocal() as db:
        db.add(
            FusedEvent(
                device_id=uuid.UUID(device_id),
                status=status,
                fused_distance_m=4.1 if status != FusionStatus.INSUFFICIENT_DATA else None,
                discrepancy_m=0.2 if status == FusionStatus.OK else (2.0 if status == FusionStatus.ANOMALY else None),
                source_reading_ids="1,2",
                event_time=datetime.now(timezone.utc),
            )
        )
        await db.commit()


async def test_fused_events_filter_by_status(client, auth_headers, registered_device):
    device_id = registered_device["id"]
    await _seed_fused_event(device_id, FusionStatus.OK)
    await _seed_fused_event(device_id, FusionStatus.ANOMALY)
    await _seed_fused_event(device_id, FusionStatus.ANOMALY)

    resp = await client.get(f"/devices/{device_id}/fused-events?status=anomaly", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["status"] == "anomaly" for item in body["items"])
