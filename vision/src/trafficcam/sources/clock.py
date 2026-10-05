"""Conversion of camera sensor timestamps to wall-clock time."""

from datetime import UTC, datetime, timedelta

EPOCH = datetime.fromtimestamp(0, tz=UTC)


def sensor_time_to_utc(sensor_ns: int, boottime_ns: int, realtime_ns: int) -> datetime:
    """Convert a boot-clock timestamp to UTC, given readings of both clocks taken together."""
    return EPOCH + timedelta(microseconds=(sensor_ns - boottime_ns + realtime_ns) // 1000)
