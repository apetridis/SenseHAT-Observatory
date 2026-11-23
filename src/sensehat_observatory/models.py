"""Typed records shared by sensor readers and the InfluxDB writer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class DataPoint:
    """A single InfluxDB measurement with tags, fields and a UTC timestamp."""

    measurement: str
    fields: dict[str, int | float | str | bool]
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bucket: str | None = None


@dataclass(frozen=True)
class EnvironmentReading:
    """Raw and calculated environmental sensor values."""

    temperature_humidity_c: float
    temperature_pressure_c: float
    relative_humidity_percent: float
    pressure_hpa: float
    dew_point_c: float | None
    absolute_humidity_g_m3: float | None
    temperature_difference_c: float

    def to_point(self, tags: dict[str, str]) -> DataPoint:
        """Convert the reading to an InfluxDB environment point."""
        fields: dict[str, int | float | str | bool] = {
            "temperature_humidity_c": self.temperature_humidity_c,
            "temperature_pressure_c": self.temperature_pressure_c,
            "relative_humidity_percent": self.relative_humidity_percent,
            "pressure_hpa": self.pressure_hpa,
            "temperature_difference_c": self.temperature_difference_c,
        }
        if self.dew_point_c is not None:
            fields["dew_point_c"] = self.dew_point_c
        if self.absolute_humidity_g_m3 is not None:
            fields["absolute_humidity_g_m3"] = self.absolute_humidity_g_m3
        return DataPoint("environment", fields, tags)


@dataclass(frozen=True)
class MotionReading:
    """Sense HAT motion values using documented API units."""

    acceleration_x_g: float
    acceleration_y_g: float
    acceleration_z_g: float
    gyroscope_x_rad_s: float
    gyroscope_y_rad_s: float
    gyroscope_z_rad_s: float
    magnetometer_x_ut: float
    magnetometer_y_ut: float
    magnetometer_z_ut: float
    movement_detected: bool

    def to_point(self, tags: dict[str, str]) -> DataPoint:
        """Convert the reading to an InfluxDB motion point."""
        return DataPoint(
            "motion",
            {
                "acceleration_x_g": self.acceleration_x_g,
                "acceleration_y_g": self.acceleration_y_g,
                "acceleration_z_g": self.acceleration_z_g,
                "gyroscope_x_rad_s": self.gyroscope_x_rad_s,
                "gyroscope_y_rad_s": self.gyroscope_y_rad_s,
                "gyroscope_z_rad_s": self.gyroscope_z_rad_s,
                "magnetometer_x_ut": self.magnetometer_x_ut,
                "magnetometer_y_ut": self.magnetometer_y_ut,
                "magnetometer_z_ut": self.magnetometer_z_ut,
                "movement_detected": self.movement_detected,
            },
            tags,
        )


@dataclass(frozen=True)
class OrientationReading:
    """Orientation and compass values in degrees."""

    pitch_deg: float
    roll_deg: float
    yaw_deg: float
    compass_heading_deg: float

    def to_point(self, tags: dict[str, str]) -> DataPoint:
        """Convert the reading to an InfluxDB orientation point."""
        return DataPoint(
            "orientation",
            {
                "pitch_deg": self.pitch_deg,
                "roll_deg": self.roll_deg,
                "yaw_deg": self.yaw_deg,
                "compass_heading_deg": self.compass_heading_deg,
            },
            tags,
        )


@dataclass(frozen=True)
class CollectorHealth:
    """Current collector status and cumulative error counts."""

    status: str
    last_environment_update: datetime | None
    last_motion_update: datetime | None
    sensor_errors: int
    write_errors: int
    queue_depth: int
    dropped_points: int

    def to_point(self, tags: dict[str, str]) -> DataPoint:
        """Convert health to a point using epoch milliseconds for update times."""
        fields: dict[str, int | float | str | bool] = {
            "status": self.status,
            "sensor_errors": self.sensor_errors,
            "write_errors": self.write_errors,
            "queue_depth": self.queue_depth,
            "dropped_points": self.dropped_points,
        }
        if self.last_environment_update is not None:
            fields["last_environment_update_ms"] = int(
                self.last_environment_update.timestamp() * 1000
            )
        if self.last_motion_update is not None:
            fields["last_motion_update_ms"] = int(self.last_motion_update.timestamp() * 1000)
        return DataPoint("collector_health", fields, tags)
