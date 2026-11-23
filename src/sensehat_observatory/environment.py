"""Environmental sensor collection."""

from __future__ import annotations

from .models import DataPoint
from .sensors import SensorInterface


def collect_environment(
    sensors: SensorInterface, tags: dict[str, str]
) -> DataPoint:
    """Read environmental sensors and return one enriched measurement."""
    return sensors.read_environment().to_point(tags)
