"""Tests for POST /devices/{id}/rotate-key."""
import uuid
from datetime import datetime, timezone

import pytest
from httpx_ws import WebSocketDisconnect, aconnect_ws

from tests.conftest import ws_capable_client


async def test_rotate_returns_a_new_key(client, auth_headers, registered_device):
    resp = await client.post(f"/devices/{registered_device['id']}/rotate-key", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == registered_device["id"]
    assert body["api_key"].startswith("sk_live_")
    assert body["api_key"] != registered_device["api_key"]


async def test_rotate_requires_auth(client, registered_device):
    resp = await client.post(f"/devices/{registered_device['id']}/rotate-key")
    assert resp.status_code == 401


async def test_rotate_unknown_device_is_404(client, auth_headers):
    resp = await client.post(f"/devices/{uuid.uuid4()}/rotate-key", headers=auth_headers)
    assert resp.status_code == 404


async def test_old_key_is_rejected_and_new_key_works(client, auth_headers, registered_device):
    old_key = registered_device["api_key"]
    rotated = await client.post(f"/devices/{registered_device['id']}/rotate-key", headers=auth_headers)
    new_key = rotated.json()["api_key"]

    async with ws_capable_client() as ws_client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            async with aconnect_ws(f"/ws/ingest?api_key={old_key}", ws_client):
                pass
        assert exc_info.value.code == 1008

        async with aconnect_ws(f"/ws/ingest?api_key={new_key}", ws_client) as ws:
            await ws.send_json(
                {"sensor_type": "radar", "recorded_at": datetime.now(timezone.utc).isoformat(), "distance_m": 4.2}
            )
            assert (await ws.receive_json())["status"] == "ok"


async def test_rotated_key_is_not_exposed_by_device_endpoints(client, auth_headers, registered_device):
    await client.post(f"/devices/{registered_device['id']}/rotate-key", headers=auth_headers)
    got = await client.get(f"/devices/{registered_device['id']}", headers=auth_headers)
    assert "api_key" not in got.json() and "api_key_hash" not in got.json()
