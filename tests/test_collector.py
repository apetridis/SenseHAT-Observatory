"""Tests for collector lifecycle and hardware event derivation."""

from __future__ import annotations

import threading
import unittest
from pathlib import Path

from sensehat_observatory.collector import ObservatoryCollector
from sensehat_observatory.config import load_config
from sensehat_observatory.influx_writer import BufferedWriter, PointSink
from sensehat_observatory.models import EnvironmentReading, MotionReading, OrientationReading


class EventingSensors:
    def __init__(self) -> None:
        self.orientation_reads = 0
        self.joystick_events = [("right", "pressed")]
        self.display_statuses: list[str] = []

    def read_environment(self) -> EnvironmentReading:
        return EnvironmentReading(20, 19, 50, 1012, 9, 8, 1)

    def read_motion(self, movement_threshold_g: float) -> MotionReading:
        return MotionReading(0.1, 0.2, 0.9, 0.0, 0.0, 0.0, 10, 20, 30, True)

    def read_orientation(self) -> OrientationReading:
        self.orientation_reads += 1
        return OrientationReading(
            0.0 if self.orientation_reads == 1 else 12.0,
            0.0,
            0.0,
            180.0,
        )

    def read_joystick_events(self) -> list[tuple[str, str]]:
        events = self.joystick_events
        self.joystick_events = []
        return events

    def display_status(self, status: str) -> None:
        self.display_statuses.append(status)


class CollectingSink(PointSink):
    def __init__(self) -> None:
        self.points = []

    def write(self, points) -> None:
        self.points.extend(points)

    def close(self) -> None:
        pass


class CollectorTests(unittest.TestCase):
    def test_publishes_sensor_data_health_and_event_transitions(self) -> None:
        example = Path(__file__).parents[1] / "config" / "observatory.example.yaml"
        config = load_config(example, environ={})
        sensors = EventingSensors()
        sink = CollectingSink()
        writer = BufferedWriter(sink, capacity=100, batch_size=25)
        stop = threading.Event()
        timer = threading.Timer(0.25, stop.set)
        timer.start()

        ObservatoryCollector(config, sensors, writer).run(stop)
        timer.join()

        measurements = [point.measurement for point in sink.points]
        self.assertIn("environment", measurements)
        self.assertIn("motion", measurements)
        self.assertIn("orientation", measurements)
        self.assertIn("motion_aggregate", measurements)
        self.assertIn("collector_health", measurements)
        event_points = [point for point in sink.points if point.measurement == "hardware_events"]
        event_types = [point.fields["event_type"] for point in event_points]
        self.assertIn("movement", event_types)
        self.assertIn("orientation_change", event_types)
        self.assertEqual(
            sum(point.fields.get("direction") == "right" for point in event_points),
            1,
        )
        self.assertEqual(sensors.display_statuses, [])


if __name__ == "__main__":
    unittest.main()
