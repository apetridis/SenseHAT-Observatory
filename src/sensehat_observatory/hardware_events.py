"""Joystick and hardware state-change event conversion."""

from __future__ import annotations

from .models import DataPoint
from .sensors import SensorInterface


def collect_joystick_events(
    sensors: SensorInterface, tags: dict[str, str]
) -> list[DataPoint]:
    """Drain joystick events and represent each as a timestamped point."""
    return [
        DataPoint(
            "hardware_events",
            {"event_type": "joystick", "direction": direction, "action": action},
            tags,
        )
        for direction, action in sensors.read_joystick_events()
    ]
