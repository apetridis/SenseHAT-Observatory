"""Load and validate Observatory configuration."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

import yaml


class ConfigurationError(ValueError):
    """Raised when the Observatory configuration is invalid."""


@dataclass(frozen=True)
class LocationConfig:
    """Operator-provided location labels; this project does not infer coordinates."""

    name: str
    building: str | None = None
    floor: str | None = None
    room: str | None = None


@dataclass(frozen=True)
class SamplingConfig:
    """Sensor sampling intervals and rates."""

    environment_interval_seconds: int = 30
    imu_frequency_hz: float = 10.0


@dataclass(frozen=True)
class MotionConfig:
    """Motion detection and aggregation settings."""

    movement_threshold_g: float = 0.25
    aggregation_interval_seconds: int = 60


@dataclass(frozen=True)
class RetentionConfig:
    """Retention windows for each stored data class."""

    environment_days: int = 365
    motion_raw_days: int = 7
    motion_aggregate_days: int = 365
    hardware_events_days: int = 365
    health_days: int = 30


@dataclass(frozen=True)
class DisplayConfig:
    """Optional LED matrix behavior."""

    enabled: bool = False


@dataclass(frozen=True)
class InfluxConfig:
    """InfluxDB connection settings; credentials are environment-only."""

    url: str
    org: str | None = None
    bucket: str | None = None
    token: str | None = field(default=None, repr=False)
    motion_raw_bucket: str = "motion_raw"
    motion_aggregate_bucket: str = "motion_aggregate"
    events_bucket: str = "events"
    health_bucket: str = "health"


@dataclass(frozen=True)
class ObservatoryConfig:
    """Validated application configuration."""

    device_id: str
    location: LocationConfig
    sampling: SamplingConfig
    motion: MotionConfig
    retention: RetentionConfig
    display: DisplayConfig
    influx: InfluxConfig


_ROOT_KEYS = {
    "device_id",
    "location",
    "sampling",
    "motion",
    "retention",
    "display",
}
_SECTION_KEYS = {
    "location": {"name", "building", "floor", "room"},
    "sampling": {"environment_interval_seconds", "imu_frequency_hz"},
    "motion": {
        "movement_threshold_g",
        "aggregation_interval_seconds",
    },
    "retention": {
        "environment_days",
        "motion_raw_days",
        "motion_aggregate_days",
        "hardware_events_days",
        "health_days",
    },
    "display": {"enabled"},
}


def load_config(
    path: str | Path,
    environ: Mapping[str, str] | None = None,
) -> ObservatoryConfig:
    """Read YAML configuration and apply supported environment overrides.

    InfluxDB credentials and connection details are deliberately read only from
    environment variables, keeping secrets out of the YAML configuration.
    """
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {config_path}: {exc}") from exc

    root = _mapping(data, "configuration")
    _reject_unknown(root, _ROOT_KEYS, "configuration")

    location_data = _section(root, "location")
    sampling_data = _section(root, "sampling")
    motion_data = _section(root, "motion")
    retention_data = _section(root, "retention")
    display_data = _section(root, "display")
    for section_name, section_data in (
        ("location", location_data),
        ("sampling", sampling_data),
        ("motion", motion_data),
        ("retention", retention_data),
        ("display", display_data),
    ):
        _reject_unknown(section_data, _SECTION_KEYS[section_name], section_name)

    device_id = _string(root.get("device_id", "sensehat-observatory-01"), "device_id")
    location = LocationConfig(
        name=_string(location_data.get("name", "Unspecified"), "location.name"),
        building=_optional_string(location_data.get("building"), "location.building"),
        floor=_optional_string(location_data.get("floor"), "location.floor"),
        room=_optional_string(location_data.get("room"), "location.room"),
    )
    sampling = SamplingConfig(
        environment_interval_seconds=_positive_integer(
            sampling_data.get("environment_interval_seconds", 30),
            "sampling.environment_interval_seconds",
        ),
        imu_frequency_hz=_positive_number(
            sampling_data.get("imu_frequency_hz", 10),
            "sampling.imu_frequency_hz",
        ),
    )
    if sampling.imu_frequency_hz > 100:
        raise ConfigurationError("sampling.imu_frequency_hz must not exceed 100 Hz")
    if sampling.environment_interval_seconds > 86_400:
        raise ConfigurationError("sampling.environment_interval_seconds must not exceed 86400")
    motion = MotionConfig(
        movement_threshold_g=_positive_number(
            motion_data.get("movement_threshold_g", 0.25),
            "motion.movement_threshold_g",
        ),
        aggregation_interval_seconds=_positive_integer(
            motion_data.get("aggregation_interval_seconds", 60),
            "motion.aggregation_interval_seconds",
        ),
    )
    retention = RetentionConfig(
        environment_days=_positive_integer(
            retention_data.get("environment_days", 365), "retention.environment_days"
        ),
        motion_raw_days=_positive_integer(
            retention_data.get("motion_raw_days", 7), "retention.motion_raw_days"
        ),
        motion_aggregate_days=_positive_integer(
            retention_data.get("motion_aggregate_days", 365),
            "retention.motion_aggregate_days",
        ),
        hardware_events_days=_positive_integer(
            retention_data.get("hardware_events_days", 365),
            "retention.hardware_events_days",
        ),
        health_days=_positive_integer(
            retention_data.get("health_days", 30), "retention.health_days"
        ),
    )
    if motion.aggregation_interval_seconds > 86_400:
        raise ConfigurationError("motion.aggregation_interval_seconds must not exceed 86400")
    if max(
        retention.environment_days,
        retention.motion_raw_days,
        retention.motion_aggregate_days,
        retention.hardware_events_days,
        retention.health_days,
    ) > 36_500:
        raise ConfigurationError("retention periods must not exceed 36500 days")
    display_enabled = display_data.get("enabled", False)
    if not isinstance(display_enabled, bool):
        raise ConfigurationError("display.enabled must be a boolean")
    display = DisplayConfig(enabled=display_enabled)

    environment_vars = os.environ if environ is None else environ
    influx = _load_influx_config(environment_vars)

    return ObservatoryConfig(
        device_id=device_id,
        location=location,
        sampling=sampling,
        motion=motion,
        retention=retention,
        display=display,
        influx=influx,
    )


def _load_influx_config(environ: Mapping[str, str]) -> InfluxConfig:
    url = environ.get("INFLUX_URL", "http://127.0.0.1:8086").strip()
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ConfigurationError("INFLUX_URL must be an absolute http:// or https:// URL")

    org = _environment_string(environ, "INFLUX_ORG")
    bucket = _environment_string(environ, "INFLUX_BUCKET")
    token = _environment_string(environ, "INFLUX_TOKEN")
    return InfluxConfig(url=url, org=org, bucket=bucket, token=token)


def _environment_string(environ: Mapping[str, str], key: str) -> str | None:
    value = environ.get(key)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _section(root: dict[object, object], name: str) -> dict[object, object]:
    value = root.get(name, {})
    if value is None:
        return {}
    return _mapping(value, name)


def _mapping(value: object, path: str) -> dict[object, object]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must be a mapping")
    return value


def _reject_unknown(data: dict[object, object], allowed: set[str], path: str) -> None:
    unknown = [key for key in data if not isinstance(key, str) or key not in allowed]
    if unknown:
        names = ", ".join(sorted(repr(key) for key in unknown))
        raise ConfigurationError(f"Unknown key(s) in {path}: {names}")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{path} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"{path} must be a string or null")
    normalized = value.strip()
    return normalized or None


def _positive_integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{path} must be a positive integer")
    return value


def _positive_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{path} must be a positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ConfigurationError(f"{path} must be a finite positive number")
    return number
