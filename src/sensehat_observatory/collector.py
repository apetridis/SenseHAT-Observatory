"""Coordinated sensor sampling and graceful collector lifecycle."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from .aggregation import MotionAggregator
from .config import ObservatoryConfig
from .environment import collect_environment
from .hardware_events import collect_joystick_events
from .influx_writer import BufferedWriter
from .models import CollectorHealth, DataPoint, OrientationReading
from .motion import collect_motion
from .sensors import SensorInterface

LOGGER = logging.getLogger(__name__)


class ObservatoryCollector:
    """Run environment, IMU, orientation, events and health sampling."""

    def __init__(
        self,
        config: ObservatoryConfig,
        sensors: SensorInterface,
        writer: BufferedWriter,
    ) -> None:
        self._config = config
        self._sensors = sensors
        self._writer = writer
        self._tags = self._make_tags()
        self._aggregator = MotionAggregator(
            config.motion.aggregation_interval_seconds,
            config.influx.motion_aggregate_bucket,
        )
        self._sensor_errors = 0
        self._last_environment_update: datetime | None = None
        self._last_motion_update: datetime | None = None
        self._previous_orientation: OrientationReading | None = None
        self._previous_movement = False

    def run(self, stop_event: threading.Event) -> None:
        """Start writer and sample until asked to stop."""
        self._writer.start()
        now = time.monotonic()
        next_environment = now
        next_health = now
        next_motion = now
        motion_period = 1.0 / self._config.sampling.imu_frequency_hz
        LOGGER.info("SenseHAT Observatory collector started")
        self._update_display("ok")
        try:
            while not stop_event.is_set():
                now = time.monotonic()
                if now >= next_environment:
                    self._read_environment()
                    next_environment = now + self._config.sampling.environment_interval_seconds
                if now >= next_motion:
                    self._read_motion()
                    next_motion = now + motion_period
                self._read_joystick_events()
                if now >= next_health:
                    self._publish_health()
                    next_health = now + 30.0
                deadline = min(next_environment, next_motion, next_health)
                stop_event.wait(max(0.0, min(deadline - time.monotonic(), 0.1)))
        finally:
            partial_aggregate = self._aggregator.flush()
            if partial_aggregate is not None:
                self._writer.submit(partial_aggregate)
            self._publish_health("stopping")
            self._update_display("stopping")
            LOGGER.info("SenseHAT Observatory collector stopping")
            self._writer.stop()

    def _make_tags(self) -> dict[str, str]:
        location = self._config.location
        tags = {"device_id": self._config.device_id, "location": location.name}
        for key in ("building", "floor", "room"):
            value = getattr(location, key)
            if value:
                tags[key] = value
        return tags

    def _read_environment(self) -> None:
        try:
            point = collect_environment(self._sensors, self._tags)
        except Exception as exc:
            self._record_sensor_error("environment", exc)
            return
        self._last_environment_update = datetime.now(timezone.utc)
        self._writer.submit(point)

    def _read_motion(self) -> None:
        try:
            motion_point, orientation_point = collect_motion(
                self._sensors,
                self._tags,
                self._config.motion.movement_threshold_g,
            )
        except Exception as exc:
            self._record_sensor_error("motion", exc)
            return

        self._last_motion_update = datetime.now(timezone.utc)
        self._writer.submit(motion_point)
        self._writer.submit(orientation_point)
        completed_aggregate = self._aggregator.add(motion_point)
        if completed_aggregate is not None:
            self._writer.submit(completed_aggregate)
        completed_aggregate = self._aggregator.add(orientation_point)
        if completed_aggregate is not None:
            self._writer.submit(completed_aggregate)

        detected = bool(motion_point.fields["movement_detected"])
        if detected and not self._previous_movement:
            self._writer.submit(
                DataPoint(
                    "hardware_events",
                    {"event_type": "movement", "movement_detected": True},
                    self._tags,
                    bucket=self._config.influx.events_bucket,
                )
            )
        self._previous_movement = detected

        orientation = self._sensors_orientation(orientation_point)
        if orientation is not None:
            self._emit_orientation_change(orientation)

    def _sensors_orientation(self, point: DataPoint) -> OrientationReading | None:
        try:
            return OrientationReading(
                pitch_deg=float(point.fields["pitch_deg"]),
                roll_deg=float(point.fields["roll_deg"]),
                yaw_deg=float(point.fields["yaw_deg"]),
                compass_heading_deg=float(point.fields["compass_heading_deg"]),
            )
        except (KeyError, TypeError, ValueError):
            LOGGER.error("Orientation measurement lacks expected values")
            return None

    def _emit_orientation_change(self, current: OrientationReading) -> None:
        previous = self._previous_orientation
        self._previous_orientation = current
        if previous is None:
            return
        threshold = 5.0
        changes = {
            "pitch_change_deg": _angle_delta(current.pitch_deg, previous.pitch_deg),
            "roll_change_deg": _angle_delta(current.roll_deg, previous.roll_deg),
            "yaw_change_deg": _angle_delta(current.yaw_deg, previous.yaw_deg),
        }
        if max(abs(value) for value in changes.values()) < threshold:
            return
        fields: dict[str, int | float | str | bool] = {
            "event_type": "orientation_change",
            **changes,
            "pitch_deg": current.pitch_deg,
            "roll_deg": current.roll_deg,
            "yaw_deg": current.yaw_deg,
        }
        self._writer.submit(
            DataPoint(
                "hardware_events",
                fields,
                self._tags,
                bucket=self._config.influx.events_bucket,
            )
        )

    def _read_joystick_events(self) -> None:
        try:
            points = collect_joystick_events(self._sensors, self._tags)
        except Exception as exc:
            self._record_sensor_error("joystick", exc)
            return
        for point in points:
            self._writer.submit(
                DataPoint(
                    point.measurement,
                    point.fields,
                    point.tags,
                    point.timestamp,
                    self._config.influx.events_bucket,
                )
            )

    def _record_sensor_error(self, sensor: str, error: Exception) -> None:
        self._sensor_errors += 1
        LOGGER.error(
            "Sense HAT sensor read failed: %s",
            error,
            extra={"sensor": sensor},
            exc_info=(type(error), error, error.__traceback__),
        )
        self._update_display("degraded")

    def _update_display(self, status: str) -> None:
        if not self._config.display.enabled or status == getattr(self, "_display_status", None):
            return
        self._display_status = status
        try:
            self._sensors.display_status(status)
        except Exception:
            LOGGER.exception("Sense HAT status display update failed")

    def _publish_health(self, status: str | None = None) -> None:
        health_status = status or ("degraded" if self._sensor_errors else "ok")
        health = CollectorHealth(
            status=health_status,
            last_environment_update=self._last_environment_update,
            last_motion_update=self._last_motion_update,
            sensor_errors=self._sensor_errors,
            write_errors=self._writer.write_errors,
            queue_depth=self._writer.queue_depth,
            dropped_points=self._writer.dropped_points,
        )
        self._writer.submit(
            DataPoint(
                "collector_health",
                health.to_point(self._tags).fields,
                self._tags,
                bucket=self._config.influx.health_bucket,
            )
        )


def _angle_delta(current: float, previous: float) -> float:
    """Return shortest signed difference for circular angles."""
    return (current - previous + 180.0) % 360.0 - 180.0
