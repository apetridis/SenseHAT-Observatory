"""IMU and orientation collection helpers."""

from __future__ import annotations

from .models import DataPoint
from .sensors import SensorInterface


def collect_motion(
    sensors: SensorInterface, tags: dict[str, str], threshold_g: float
) -> tuple[DataPoint, DataPoint]:
    """Read IMU and orientation values and return structured measurements."""
    motion = sensors.read_motion(threshold_g)
    orientation = sensors.read_orientation()
    return motion.to_point(tags), orientation.to_point(tags)
