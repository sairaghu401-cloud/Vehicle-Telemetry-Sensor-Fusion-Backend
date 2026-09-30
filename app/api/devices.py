"""
Device management + historical query endpoints.

Backend concept — path params vs. query params, used throughout this file:
  - Path params (/devices/{device_id}) identify WHICH resource you mean.
    They're part of the URL's identity — /devices/abc and /devices/xyz are
    two different things.
  - Query params (?from=...&page=...) modify HOW you want that resource
    presented — filtering, sorting, pagination. They're optional by nature
    and don't change WHICH resource you're asking about.
  "Give me device abc's history, filtered to last week, page 2" is
  naturally: path param for "device abc", query params for the rest.
"""
import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import generate_api_key, hash_api_key
from app.db.redis import redis_client
from app.db.session import get_db
from app.models.device import Device
from app.models.fused_event import FusedEvent, FusionStatus
from app.models.sensor_reading import SensorReading, SensorType
from app.models.user import User
from app.schemas.device import DeviceCreate, DeviceCreateResponse, DeviceOut
from app.schemas.fused_event import FusedEventOut
from app.schemas.sensor_reading import PaginatedResponse, SensorReadingOut
from app.services.live_state import get_latest_reading

router = APIRouter(prefix="/devices", tags=["devices"])


async def _get_device_or_404(device_id: uuid.UUID, db: AsyncSession) -> Device:
    """
    Shared lookup used by every endpoint that takes a device_id path param.
    Factored out so "device not found" is handled identically everywhere,
    rather than each endpoint reimplementing its own 404 logic.
    """
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Device {device_id} not found")
    return device


@router.post("", response_model=DeviceCreateResponse, status_code=status.HTTP_201_CREATED)
async def register_device(
    payload: DeviceCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Register a new device and issue it an API key.

    Requires a logged-in dashboard user (step 6) — registering a device is
    an administrative action, not something anonymous callers should be
    able to do. The raw API key is returned ONLY in this response. From
    this point on, the server only ever sees the hash — there is no
    "forgot my key" recovery, only issuing a new one.
    """
    existing = await db.execute(select(Device).where(Device.device_code == payload.device_code))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"device_code '{payload.device_code}' is already registered",
        )

    raw_key = generate_api_key()
    device = Device(
        name=payload.name,
        device_code=payload.device_code,
        api_key_hash=hash_api_key(raw_key),
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)

    return DeviceCreateResponse(
        id=device.id,
        name=device.name,
        device_code=device.device_code,
        api_key=raw_key,
        created_at=device.created_at,
    )


@router.get("", response_model=list[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Device).order_by(Device.created_at.desc()))
    return result.scalars().all()


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    return await _get_device_or_404(device_id, db)


@router.get("/{device_id}/live")
async def get_device_live_state(
    device_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    The device's most recent reading, from Redis — not Postgres.

    This is the "current state" endpoint a dashboard would poll for a
    live view. It answers in roughly constant time regardless of how much
    history the device has, because it's a single Redis key lookup, not a
    query over sensor_readings. Returns null if the device hasn't sent
    anything in the last 5 minutes (see LATEST_READING_TTL_SECONDS) — that
    absence is itself meaningful: "no recent data," not "value is zero."
    """
    await _get_device_or_404(device_id, db)
    latest = await get_latest_reading(redis_client, str(device_id))
    return {"device_id": device_id, "latest_reading": latest}


@router.get("/{device_id}/history", response_model=PaginatedResponse[SensorReadingOut])
async def get_device_history(
    device_id: uuid.UUID,
    from_: datetime | None = Query(None, alias="from", description="Inclusive lower bound on recorded_at (ISO 8601)"),
    to: datetime | None = Query(None, description="Inclusive upper bound on recorded_at (ISO 8601)"),
    sensor_type: SensorType | None = Query(None, description="Filter to one sensor type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Paginated historical sensor readings for a device.

    Backend concept — offset/limit pagination, and why it's the right
    choice here (with a caveat):

    `page`/`page_size` translate to SQL's OFFSET/LIMIT: "skip the first N
    rows, then give me the next page_size rows." It's simple, and it lets a
    client jump straight to page 7 or ask "how many pages exist" (via
    `total`) — good for a UI with page-number controls.

    The caveat: OFFSET gets slower the deeper you page, because Postgres
    still has to scan and discard every skipped row. For a table that only
    ever grows (like sensor_readings) and where clients mostly page through
    recent data, "cursor-based" pagination (e.g. "give me readings with id
    less than X") scales better at very deep pages. We're using
    offset/limit now because it's simpler to reason about and query, and
    because the `from`/`to` time filters mean callers usually aren't paging
    through millions of unfiltered rows — but it's worth knowing this
    tradeoff exists if a "jump to page 10,000" use case shows up later.
    """
    await _get_device_or_404(device_id, db)

    if from_ is not None and to is not None and from_ > to:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'from' must not be after 'to'")

    filters = [SensorReading.device_id == device_id]
    if from_ is not None:
        filters.append(SensorReading.recorded_at >= from_)
    if to is not None:
        filters.append(SensorReading.recorded_at <= to)
    if sensor_type is not None:
        filters.append(SensorReading.sensor_type == sensor_type)

    count_result = await db.execute(select(func.count()).select_from(SensorReading).where(*filters))
    total = count_result.scalar_one()

    result = await db.execute(
        select(SensorReading)
        .where(*filters)
        .order_by(SensorReading.recorded_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    readings = result.scalars().all()

    return PaginatedResponse(items=readings, total=total, page=page, page_size=page_size)


@router.get("/{device_id}/fused-events", response_model=PaginatedResponse[FusedEventOut])
async def get_device_fused_events(
    device_id: uuid.UUID,
    status_filter: FusionStatus | None = Query(
        None, alias="status", description="Filter to one fusion status: ok, anomaly, or insufficient_data"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Paginated fused events for a device — the output of cross-validating
    radar vs. ToF readings (see app/services/fusion.py). Most useful
    filtered to status=anomaly: "show me every time this device's sensors
    disagreed," which is the actual point of doing sensor fusion at all.
    """
    await _get_device_or_404(device_id, db)

    filters = [FusedEvent.device_id == device_id]
    if status_filter is not None:
        filters.append(FusedEvent.status == status_filter)

    count_result = await db.execute(select(func.count()).select_from(FusedEvent).where(*filters))
    total = count_result.scalar_one()

    result = await db.execute(
        select(FusedEvent)
        .where(*filters)
        .order_by(FusedEvent.event_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    events = result.scalars().all()

    return PaginatedResponse(items=events, total=total, page=page, page_size=page_size)
