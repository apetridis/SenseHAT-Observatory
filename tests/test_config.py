"""Tests for Observatory configuration loading and validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from sensehat_observatory.config import ConfigurationError, load_config


class LoadConfigTests(unittest.TestCase):
    def write_config(self, content: str) -> Path:
        temporary_file = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".yaml", delete=False
        )
        self.addCleanup(Path(temporary_file.name).unlink, missing_ok=True)
        with temporary_file:
            temporary_file.write(content)
        return Path(temporary_file.name)

    def test_loads_example_defaults_and_location_metadata(self) -> None:
        config_path = Path(__file__).parents[1] / "config" / "observatory.example.yaml"

        config = load_config(config_path, environ={})

        self.assertEqual(config.device_id, "sensehat-observatory-01")
        self.assertEqual(config.location.name, "Example location")
        self.assertIsNone(config.location.building)
        self.assertEqual(config.sampling.environment_interval_seconds, 30)
        self.assertEqual(config.sampling.imu_frequency_hz, 10.0)
        self.assertEqual(config.motion.aggregation_interval_seconds, 60)
        self.assertEqual(config.retention.environment_days, 365)
        self.assertEqual(config.retention.motion_raw_days, 7)
        self.assertEqual(config.retention.motion_aggregate_days, 365)
        self.assertEqual(config.retention.hardware_events_days, 365)
        self.assertEqual(config.retention.health_days, 30)
        self.assertFalse(config.display.enabled)
        self.assertEqual(config.influx.url, "http://127.0.0.1:8086")
        self.assertIsNone(config.influx.token)

    def test_reads_influx_credentials_from_environment_only(self) -> None:
        config_path = self.write_config("device_id: pi-observatory\n")

        config = load_config(
            config_path,
            environ={
                "INFLUX_URL": "https://influx.example.test:8086",
                "INFLUX_ORG": "observatory",
                "INFLUX_BUCKET": "environment",
                "INFLUX_TOKEN": "secret-token",
            },
        )

        self.assertEqual(config.influx.url, "https://influx.example.test:8086")
        self.assertEqual(config.influx.org, "observatory")
        self.assertEqual(config.influx.bucket, "environment")
        self.assertEqual(config.influx.token, "secret-token")
        self.assertNotIn("secret-token", repr(config))

    def test_rejects_coordinates_and_other_unknown_location_fields(self) -> None:
        config_path = self.write_config(
            yaml.safe_dump({"location": {"name": "Room", "latitude": 51.5}})
        )

        with self.assertRaisesRegex(ConfigurationError, "Unknown key"):
            load_config(config_path, environ={})

    def test_rejects_non_positive_sampling_rate(self) -> None:
        config_path = self.write_config(
            yaml.safe_dump({"sampling": {"imu_frequency_hz": 0}})
        )

        with self.assertRaisesRegex(ConfigurationError, "imu_frequency_hz"):
            load_config(config_path, environ={})

    def test_rejects_imu_rate_above_safe_configured_limit(self) -> None:
        config_path = self.write_config(
            yaml.safe_dump({"sampling": {"imu_frequency_hz": 101}})
        )

        with self.assertRaisesRegex(ConfigurationError, "must not exceed 100 Hz"):
            load_config(config_path, environ={})

    def test_rejects_non_positive_retention(self) -> None:
        config_path = self.write_config(
            yaml.safe_dump({"retention": {"motion_raw_days": 0}})
        )

        with self.assertRaisesRegex(ConfigurationError, "motion_raw_days"):
            load_config(config_path, environ={})

    def test_rejects_secret_fields_in_yaml(self) -> None:
        config_path = self.write_config(
            yaml.safe_dump({"influx": {"token": "must-not-be-in-yaml"}})
        )

        with self.assertRaisesRegex(ConfigurationError, "Unknown key"):
            load_config(config_path, environ={})

    def test_rejects_non_http_influx_url(self) -> None:
        config_path = self.write_config("device_id: pi-observatory\n")

        with self.assertRaisesRegex(ConfigurationError, "INFLUX_URL"):
            load_config(config_path, environ={"INFLUX_URL": "influxdb:8086"})

    def test_reports_yaml_syntax_errors(self) -> None:
        config_path = self.write_config("location: [")

        with self.assertRaisesRegex(ConfigurationError, "Invalid YAML"):
            load_config(config_path, environ={})


if __name__ == "__main__":
    unittest.main()
