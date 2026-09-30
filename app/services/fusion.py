"""
Sensor fusion logic: cross-validates radar vs. ToF distance readings for a
device and produces a FusedEvent recording whether they agree or not.

Backend concept — why this logic lives in its own module, separate from
the WebSocket endpoint that triggers it: the fusion RULES (what counts as
agreement, what to do when data's missing) are business logic that has
nothing to do with WebSockets, JSON parsing, or HTTP. Keeping it in
app/services/ means it can be tested in complete isolation (no server, no
network, no database even — see the pure function below) and reused from
anywhere: the WebSocket endpoint now, a batch reprocessing script later, a
REST endpoint if we add one.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fused_event import FusedEvent, FusionStatus
from app.models.sensor_reading import SensorReading, SensorType

# How close in time two readings must be to be considered "the same
# moment" for fusion purposes. Radar and ToF readings rarely arrive at the
# exact same millisecond even if a device samples both "simultaneously,"
# so we need SOME window rather than requiring an exact timestamp match.
MATCH_WINDOW = timedelta(seconds=2)

# Distance disagreement beyond this, in meters, is flagged as an anomaly.
# See README for the tradeoff of a fixed vs. percentage-based tolerance.
DISCREPANCY_TOLERANCE_M = 0.5


@dataclass
class FusionResult:
    """
    Plain-data result of running the fusion RULE (no I/O). Kept separate
    from FusedEvent (the ORM model) so the core decision logic — "given
    these two distances, what's the status and discrepancy?" — can be
    unit-tested with plain numbers, no database or async machinery
    required at all.
    """

    status: FusionStatus
    fused_distance_m: float | None
    discrepancy_m: float | None


def fuse_distances(radar_distance_m: float | None, tof_distance_m: float | None) -> FusionResult:
    """
    Pure function: the actual fusion RULE, with no dependency on the
    database, SQLAlchemy, or anything async. Given two distance readings
    (either may be missing), decide whether they agree.

    This is deliberately the smallest possible unit of the fusion logic —
    testable with a one-line assertion like
    `fuse_distances(4.0, 4.2).status == FusionStatus.OK`, with no test
    database or event loop needed.
    """
    if radar_distance_m is None or tof_distance_m is None:
        return FusionResult(status=FusionStatus.INSUFFICIENT_DATA, fused_distance_m=None, discrepancy_m=None)

    discrepancy = abs(radar_distance_m - tof_distance_m)
    # The fused value: simple average of the two agreeing (or disagreeing)
    # readings. A more sophisticated fusion (e.g. weighting by each
    # sensor's known accuracy) is a reasonable future improvement — this
    # is a deliberately simple starting point, documented as such.
    fused_distance = (radar_distance_m + tof_distance_m) / 2

    if discrepancy <= DISCREPANCY_TOLERANCE_M:
        return FusionResult(status=FusionStatus.OK, fused_distance_m=fused_distance, discrepancy_m=discrepancy)
    else:
        return FusionResult(status=FusionStatus.ANOMALY, fused_distance_m=fused_distance, discrepancy_m=discrepancy)


async def try_fuse_reading(db: AsyncSession, new_reading: SensorReading) -> FusedEvent | None:
    """
    Called after a new reading is stored. If the new reading is radar or
    ToF, look for the most recent reading of the OTHER type from the same
    device within MATCH_WINDOW, and if found, fuse them into a FusedEvent.

    Returns None (does nothing) when:
      - the new reading is GPS (fusion here is specifically radar-vs-ToF)
      - no matching reading from the other sensor exists within the window

    That second case is deliberate, not an error: it just means "we don't
    have enough data to fuse yet" — for example, the very first reading
    from a newly connected device, before its paired sensor has reported
    anything. We don't create an INSUFFICIENT_DATA event for every single
    unmatched reading, which would flood the table; INSUFFICIENT_DATA is
    reserved for cases where fusion was attempted with a real gap (see
    fuse_distances above) rather than "nothing to try yet."
    """
    if new_reading.sensor_type not in (SensorType.RADAR, SensorType.TOF):
        return None

    other_type = SensorType.TOF if new_reading.sensor_type == SensorType.RADAR else SensorType.RADAR

    window_start = new_reading.recorded_at - MATCH_WINDOW
    window_end = new_reading.recorded_at + MATCH_WINDOW

    # Bug fixed while building this: without excluding already-fused
    # readings, a later reading could match a partner that was ALREADY
    # consumed by an earlier fusion, double-counting it into two separate
    # FusedEvents. Concretely: radar A + tof B fuse into event 1; then
    # radar C arrives, still within B's time window, and — without this
    # exclusion — matches tof B again, creating a second, spurious event
    # for a pairing that was never actually simultaneous. Excluding any
    # reading ID that already appears in an existing FusedEvent's
    # source_reading_ids ensures each reading is used in at most one
    # fusion, matching the real-world intent of "these two SPECIFIC
    # samples, taken together, agreed or disagreed" rather than every
    # reading being fair game to re-match indefinitely.
    # Scoped to this device (not a full-table scan) to keep this cheap —
    # still O(recent events for this device) rather than O(1), which is a
    # real cost worth knowing about. The source_reading_ids-as-CSV-string
    # design (see fused_event.py's comment on that field) is exactly what
    # makes this an app-side scan instead of an indexed lookup; if fusion
    # volume ever gets large enough for this to matter, switching to a
    # real join table (fused_event_id, reading_id) — flagged as a future
    # option when that field was designed — would let this become an
    # indexed SQL query instead.
    already_fused_ids_result = await db.execute(
        select(FusedEvent.source_reading_ids).where(FusedEvent.device_id == new_reading.device_id)
    )
    already_fused_ids: set[int] = set()
    for (ids_str,) in already_fused_ids_result:
        already_fused_ids.update(int(x) for x in ids_str.split(",") if x)

    result = await db.execute(
        select(SensorReading)
        .where(
            SensorReading.device_id == new_reading.device_id,
            SensorReading.sensor_type == other_type,
            SensorReading.recorded_at >= window_start,
            SensorReading.recorded_at <= window_end,
        )
        .order_by(SensorReading.recorded_at.desc())
    )
    candidates = result.scalars().all()
    matching_reading = next((r for r in candidates if r.id not in already_fused_ids), None)

    if matching_reading is None:
        return None

    radar_reading = new_reading if new_reading.sensor_type == SensorType.RADAR else matching_reading
    tof_reading = new_reading if new_reading.sensor_type == SensorType.TOF else matching_reading

    fusion = fuse_distances(radar_reading.distance_m, tof_reading.distance_m)

    event = FusedEvent(
        device_id=new_reading.device_id,
        status=fusion.status,
        fused_distance_m=fusion.fused_distance_m,
        discrepancy_m=fusion.discrepancy_m,
        source_reading_ids=f"{radar_reading.id},{tof_reading.id}",
        event_time=new_reading.recorded_at,
        notes=(
            f"Fused radar (id={radar_reading.id}, {radar_reading.distance_m}m) "
            f"with ToF (id={tof_reading.id}, {tof_reading.distance_m}m)"
        ),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event
