"""
Manual test client for the WebSocket ingestion endpoint.

Usage:
    python scripts/test_ingest_client.py <api_key>

Sends one reading of each sensor type, prints the server's ack for each,
then exits. Useful for confirming the ingestion pipeline works end-to-end
without needing a separate WebSocket CLI tool (wscat, etc.) installed.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone

import websockets


async def main(api_key: str, host: str = "localhost", port: int = 8000):
    uri = f"ws://{host}:{port}/ws/ingest?api_key={api_key}"
    print(f"Connecting to {uri} ...")

    async with websockets.connect(uri) as ws:
        print("Connected. Sending one reading of each sensor type.\n")

        messages = [
            {
                "sensor_type": "radar",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "distance_m": 4.2,
            },
            {
                "sensor_type": "tof",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "distance_m": 4.35,
            },
            {
                "sensor_type": "gps",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "latitude": 16.4419,
                "longitude": 80.6206,
            },
        ]

        for msg in messages:
            print(f"-> sending: {msg}")
            await ws.send(json.dumps(msg))
            response = await ws.recv()
            print(f"<- received: {response}\n")

    print("Done. Connection closed cleanly.")
    print("\nNow check the live state with:")
    print("  curl http://localhost:8000/devices/{device_id}/live")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_ingest_client.py <api_key>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
