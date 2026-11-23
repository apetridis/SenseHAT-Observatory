"""Tests for sensor transformations, calculations and aggregation."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sensehat_observatory.aggregation import MotionAggregator
from sensehat_observatory.calculations import (
    calculate_absolute_humidity_g_m3,
    calculate_dew_point_c,
)
from sensehat_observatory.environment import collect_environment
from sensehat_observatory.hardware_events import collect_joystick_events
from sensehat_observatory.models import (
    EnvironmentReading,
    MotionReading,
    OrientationReading,
)
from sensehat_observatory.motion import collect_motion
from sensehat_observatory.sensors import SenseHatSensors, SensorInterface


class FakeSensors:
    def read_environment(self) -> EnvironmentReading:
        return EnvironmentReading(20.0, 18.0, 50.0, 1013.2, 9.3, 8.6, 2.0)

    def read_motion(self, movement_threshold_g: float) -> MotionReading:
        return MotionReading(0.1, 0.2, 0.9, 0.01, 0.02, 0.03, 20, 30, 40, True)

    def read_orientation(self) -> OrientationReading:
        return OrientationReading(10.0, 20.0, 30.0, 30.0)

    def read_joystick_events(self) -> list[tuple[str, str]]:
        return [("up", "pressed"), ("up", "released")]

    def display_status(self, status: str) -> None:
        pass


class EnvironmentalCalculationTests(unittest.TestCase):
    def test_dew_point_and_absolute_humidity_reference_values(self) -> None:
        dew_point = calculate_dew_point_c(20.0, 50.0)
        absolute_humidity = calculate_absolute_humidity_g_m3(20.0, 50.0)

        self.assertAlmostEqual(dew_point or 0, 9.26, places=1)
        self.assertAlmostEqual(absolute_humidity or 0, 8.64, places=1)

    def test_zero_humidity_has_no_finite_dew_point_and_zero_absolute_humidity(self) -> None:
        self.assertIsNone(calculate_dew_point_c(20.0, 0.0))
        self.assertEqual(calculate_absolute_humidity_g_m3(20.0, 0.0), 0.0)

    def test_rejects_out_of_range_humidity(self) -> None:
        with self.assertRaises(ValueError):
            calculate_dew_point_c(20.0, 101.0)

    def test_collectors_keep_measurements_structured_and_units_in_field_names(self) -> None:
        sensors: SensorInterface = FakeSensors()
        tags = {"device_id": "test-pi", "room": "lab"}

        environment = collect_environment(sensors, tags)
        motion, orientation = collect_motion(sensors, tags, 0.25)
        events = collect_joystick_events(sensors, tags)

        self.assertEqual(environment.measurement, "environment")
        self.assertEqual(environment.fields["temperature_difference_c"], 2.0)
        self.assertEqual(motion.fields["acceleration_x_g"], 0.1)
        self.assertEqual(motion.fields["gyroscope_x_rad_s"], 0.01)
        self.assertEqual(motion.fields["magnetometer_x_ut"], 20.0)
        self.assertEqual(orientation.fields["compass_heading_deg"], 30.0)
        self.assertEqual([event.fields["action"] for event in events], ["pressed", "released"])
        self.assertEqual(environment.tags, tags)

    def test_aggregator_emits_mean_min_max_and_preserves_motion_flag(self) -> None:
        aggregator = MotionAggregator(60, "motion_aggregate")
        start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        first = aggregator.add(
            _motion_point(start, acceleration_x_g=1.0, movement_detected=False)
        )
        self.assertIsNone(first)
        self.assertIsNone(
            aggregator.add(
                _motion_point(
                    datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc),
                    acceleration_x_g=3.0,
                    movement_detected=True,
                )
            )
        )
        completed = aggregator.add(
            _motion_point(
                datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc),
                acceleration_x_g=5.0,
                movement_detected=False,
            )
        )
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.bucket, "motion_aggregate")
        self.assertEqual(completed.fields["acceleration_x_g_mean"], 2.0)
        self.assertEqual(completed.fields["acceleration_x_g_min"], 1.0)
        self.assertTrue(completed.fields["movement_detected"])

    def test_partial_aggregate_flush_is_not_timestamped_in_the_future(self) -> None:
        aggregator = MotionAggregator(60, "motion_aggregate")
        aggregator.add(
            _motion_point(
                datetime.now(timezone.utc),
                acceleration_x_g=1.0,
                movement_detected=False,
            )
        )

        partial = aggregator.flush()

        self.assertIsNotNone(partial)
        assert partial is not None
        self.assertLessEqual(partial.timestamp, datetime.now(timezone.utc))


class OfficialLibraryAdapterTests(unittest.TestCase):
    def test_uses_official_api_data_without_writing_to_leds(self) -> None:
        class FakeHat:
            def __init__(self) -> None:
                self.accelerations = [
                    {"x": 0.0, "y": 0.0, "z": 1.0},
                    {"x": 0.5, "y": 0.0, "z": 1.0},
                ]
                self.display_calls = 0
                self.stick = SimpleNamespace(get_events=lambda: [])

            def get_temperature_from_humidity(self) -> float:
                return 22.0

            def get_temperature_from_pressure(self) -> float:
                return 20.0

            def get_humidity(self) -> float:
                return 45.0

            def get_pressure(self) -> float:
                return 1011.0

            def get_accelerometer_raw(self) -> dict[str, float]:
                return self.accelerations.pop(0)

            def get_gyroscope_raw(self) -> dict[str, float]:
                return {"x": 0.1, "y": 0.2, "z": 0.3}

            def get_compass_raw(self) -> dict[str, float]:
                return {"x": 1.0, "y": 2.0, "z": 3.0}

            def get_orientation(self) -> dict[str, float]:
                return {"pitch": 10.0, "roll": 20.0, "yaw": 30.0}

            def get_compass(self) -> float:
                return 30.0

            def set_pixels(self, pixels) -> None:
                self.display_calls += 1

        fake_hat = FakeHat()
        fake_module = SimpleNamespace(SenseHat=lambda: fake_hat)
        with patch.dict("sys.modules", {"sense_hat": fake_module}):
            sensors = SenseHatSensors()
            environment = sensors.read_environment()
            first_motion = sensors.read_motion(0.25)
            second_motion = sensors.read_motion(0.25)
            orientation = sensors.read_orientation()

        self.assertEqual(environment.temperature_humidity_c, 22.0)
        self.assertEqual(environment.pressure_hpa, 1011.0)
        self.assertFalse(first_motion.movement_detected)
        self.assertTrue(second_motion.movement_detected)
        self.assertEqual(second_motion.gyroscope_x_rad_s, 0.1)
        self.assertEqual(second_motion.magnetometer_z_ut, 3.0)
        self.assertEqual(orientation.compass_heading_deg, 30.0)
        self.assertEqual(fake_hat.display_calls, 0)
        sensors.display_status("ok")
        self.assertEqual(fake_hat.display_calls, 1)


def _motion_point(
    timestamp: datetime, acceleration_x_g: float, movement_detected: bool
):
    from sensehat_observatory.models import DataPoint

    return DataPoint(
        "motion",
        {
            "acceleration_x_g": acceleration_x_g,
            "movement_detected": movement_detected,
        },
        {"device_id": "test"},
        timestamp,
    )


if __name__ == "__main__":
    unittest.main()
