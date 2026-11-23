"""Sensor adapter protocols and the official Sense HAT implementation."""

from __future__ import annotations

import math
from typing import Protocol

from .models import EnvironmentReading, MotionReading, OrientationReading


class SensorInterface(Protocol):
    """Minimal interface needed by the collector, easy to mock in tests."""

    def read_environment(self) -> EnvironmentReading: ...

    def read_motion(self, movement_threshold_g: float) -> MotionReading: ...

    def read_orientation(self) -> OrientationReading: ...

    def read_joystick_events(self) -> list[tuple[str, str]]: ...

    def display_status(self, status: str) -> None: ...


class SenseHatSensors:
    """Read data using the official ``sense_hat`` Python library."""

    _STATUS_PATTERNS = {
        "ok": (
            "........",
            "......#.",
            ".....#..",
            "#...#...",
            ".#.#....",
            "..#.....",
            "........",
            "........",
        ),
        "degraded": (
            "........",
            "...###..",
            "...###..",
            "...###..",
            "........",
            "...###..",
            "...###..",
            "........",
        ),
        "stopping": (
            "........",
            "...##...",
            "..#..#..",
            ".#....#.",
            ".#....#.",
            "..#..#..",
            "...##...",
            "........",
        ),
    }

    def __init__(self) -> None:
        try:
            from sense_hat import SenseHat
        except ImportError as exc:
            raise RuntimeError(
                "The official sense-hat library is unavailable. Install "
                "Raspberry Pi OS package python3-sense-hat and use a venv "
                "created with --system-site-packages."
            ) from exc

        self._sense = SenseHat()
        self._previous_acceleration: tuple[float, float, float] | None = None

    def read_environment(self) -> EnvironmentReading:
        """Read temperature, humidity and pressure via official APIs."""
        from .calculations import (
            calculate_absolute_humidity_g_m3,
            calculate_dew_point_c,
        )

        temperature_humidity_c = float(self._sense.get_temperature_from_humidity())
        temperature_pressure_c = float(self._sense.get_temperature_from_pressure())
        humidity = float(self._sense.get_humidity())
        pressure = float(self._sense.get_pressure())
        values = (temperature_humidity_c, temperature_pressure_c, humidity, pressure)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Sense HAT environmental reading contains a non-finite value")
        if pressure <= 0:
            raise ValueError("Sense HAT atmospheric pressure must be positive")
        return EnvironmentReading(
            temperature_humidity_c=temperature_humidity_c,
            temperature_pressure_c=temperature_pressure_c,
            relative_humidity_percent=humidity,
            pressure_hpa=pressure,
            dew_point_c=calculate_dew_point_c(temperature_humidity_c, humidity),
            absolute_humidity_g_m3=calculate_absolute_humidity_g_m3(
                temperature_humidity_c, humidity
            ),
            temperature_difference_c=temperature_humidity_c - temperature_pressure_c,
        )

    def read_motion(self, movement_threshold_g: float) -> MotionReading:
        """Read IMU vectors and detect abrupt acceleration changes.

        ``get_accelerometer_raw`` reports acceleration in g, gyroscope raw
        values are radians/second, and magnetometer raw values are microtesla.
        """
        acceleration = self._sense.get_accelerometer_raw()
        gyroscope = self._sense.get_gyroscope_raw()
        magnetometer = self._sense.get_compass_raw()
        current = (float(acceleration["x"]), float(acceleration["y"]), float(acceleration["z"]))
        gyro = tuple(float(gyroscope[axis]) for axis in ("x", "y", "z"))
        magnetic_field = tuple(float(magnetometer[axis]) for axis in ("x", "y", "z"))
        if not all(math.isfinite(value) for value in (*current, *gyro, *magnetic_field)):
            raise ValueError("Sense HAT IMU reading contains a non-finite value")
        detected = False
        if self._previous_acceleration is not None:
            delta = math.sqrt(
                sum((current[index] - self._previous_acceleration[index]) ** 2 for index in range(3))
            )
            detected = delta >= movement_threshold_g
        self._previous_acceleration = current
        return MotionReading(
            acceleration_x_g=current[0],
            acceleration_y_g=current[1],
            acceleration_z_g=current[2],
            gyroscope_x_rad_s=gyro[0],
            gyroscope_y_rad_s=gyro[1],
            gyroscope_z_rad_s=gyro[2],
            magnetometer_x_ut=magnetic_field[0],
            magnetometer_y_ut=magnetic_field[1],
            magnetometer_z_ut=magnetic_field[2],
            movement_detected=detected,
        )

    def read_orientation(self) -> OrientationReading:
        """Read pitch, roll, yaw and compass heading from official APIs."""
        orientation = self._sense.get_orientation()
        values = (
            float(orientation["pitch"]),
            float(orientation["roll"]),
            float(orientation["yaw"]),
            float(self._sense.get_compass()),
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Sense HAT orientation contains a non-finite value")
        return OrientationReading(
            pitch_deg=values[0],
            roll_deg=values[1],
            yaw_deg=values[2],
            compass_heading_deg=values[3],
        )

    def read_joystick_events(self) -> list[tuple[str, str]]:
        """Return queued joystick events as ``(direction, action)`` pairs."""
        events = self._sense.stick.get_events()
        return [(event.direction, event.action) for event in events]

    def display_status(self, status: str) -> None:
        """Display a static status icon when explicitly enabled in YAML."""
        try:
            pattern = self._STATUS_PATTERNS[status]
        except KeyError as exc:
            raise ValueError(f"Unknown display status: {status}") from exc
        color = {
            "ok": (0, 180, 0),
            "degraded": (255, 96, 0),
            "stopping": (0, 64, 255),
        }[status]
        pixels = [color if cell == "#" else (0, 0, 0) for row in pattern for cell in row]
        self._sense.set_pixels(pixels)
