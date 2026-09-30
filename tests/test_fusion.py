"""
Unit tests for the pure fusion logic in app/services/fusion.py.

These test ONLY fuse_distances() — no database, no async, no WebSocket.
This is exactly the benefit of separating pure logic from I/O described in
fusion.py's module docstring: these tests run in milliseconds and pin down
the actual business rule (what counts as agreement) independent of how
it's wired into the rest of the app.
"""
import pytest

from app.models.fused_event import FusionStatus
from app.services.fusion import fuse_distances, DISCREPANCY_TOLERANCE_M


def test_readings_within_tolerance_are_ok():
    result = fuse_distances(radar_distance_m=4.0, tof_distance_m=4.2)
    assert result.status == FusionStatus.OK
    # pytest.approx, not ==, because 4.2 - 4.0 in floating point is
    # 0.20000000000000018, not exactly 0.2 — a real gotcha caught by
    # actually running this test rather than eyeballing the arithmetic.
    assert result.discrepancy_m == pytest.approx(0.2)
    assert result.fused_distance_m == pytest.approx(4.1)


def test_readings_exactly_at_tolerance_boundary_are_ok():
    # <= tolerance counts as OK, not anomaly — boundary is inclusive
    result = fuse_distances(radar_distance_m=4.0, tof_distance_m=4.0 + DISCREPANCY_TOLERANCE_M)
    assert result.status == FusionStatus.OK


def test_readings_beyond_tolerance_are_anomaly():
    result = fuse_distances(radar_distance_m=4.0, tof_distance_m=5.0)
    assert result.status == FusionStatus.ANOMALY
    assert result.discrepancy_m == pytest.approx(1.0)
    assert result.fused_distance_m == pytest.approx(4.5)


def test_missing_radar_reading_is_insufficient_data():
    result = fuse_distances(radar_distance_m=None, tof_distance_m=4.2)
    assert result.status == FusionStatus.INSUFFICIENT_DATA
    assert result.fused_distance_m is None
    assert result.discrepancy_m is None


def test_missing_tof_reading_is_insufficient_data():
    result = fuse_distances(radar_distance_m=4.2, tof_distance_m=None)
    assert result.status == FusionStatus.INSUFFICIENT_DATA


def test_both_missing_is_insufficient_data():
    result = fuse_distances(radar_distance_m=None, tof_distance_m=None)
    assert result.status == FusionStatus.INSUFFICIENT_DATA


def test_discrepancy_direction_does_not_matter():
    # radar > tof and tof > radar by the same amount should give the same discrepancy
    a = fuse_distances(radar_distance_m=5.0, tof_distance_m=4.0)
    b = fuse_distances(radar_distance_m=4.0, tof_distance_m=5.0)
    assert a.discrepancy_m == pytest.approx(b.discrepancy_m) == pytest.approx(1.0)
    assert a.status == b.status == FusionStatus.ANOMALY
