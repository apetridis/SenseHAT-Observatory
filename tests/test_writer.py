"""Tests for buffered InfluxDB writes and retry behavior."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from sensehat_observatory.config import InfluxConfig
from sensehat_observatory.influx_writer import BufferedWriter, InfluxDBSink, PointSink
from sensehat_observatory.models import DataPoint


class FakeSink(PointSink):
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.batches: list[list[DataPoint]] = []
        self.closed = False

    def write(self, points: list[DataPoint]) -> None:
        if self.failures:
            self.failures -= 1
            raise OSError("temporary outage")
        self.batches.append(list(points))

    def close(self) -> None:
        self.closed = True


class BufferedWriterTests(unittest.TestCase):
    def test_background_writer_retries_and_flushes(self) -> None:
        sink = FakeSink(failures=1)
        writer = BufferedWriter(
            sink, capacity=4, batch_size=1, retry_base_seconds=0.001
        )
        writer.start()
        self.assertTrue(writer.submit(DataPoint("environment", {"temperature_c": 20.0})))
        _wait_for(lambda: len(sink.batches) == 1)
        writer.stop()

        self.assertEqual(writer.write_errors, 1)
        self.assertEqual(writer.dropped_points, 0)
        self.assertEqual(len(sink.batches), 1)
        self.assertTrue(sink.closed)

    def test_queue_overflow_is_reported_without_blocking(self) -> None:
        sink = FakeSink()
        writer = BufferedWriter(sink, capacity=1, batch_size=10)
        writer._queue.put_nowait(DataPoint("environment", {"temperature_c": 1.0}))
        accepted = writer.submit(DataPoint("environment", {"temperature_c": 2.0}))

        self.assertFalse(accepted)
        self.assertEqual(writer.dropped_points, 1)


class InfluxDBSinkTests(unittest.TestCase):
    def test_routes_measurements_to_configured_buckets(self) -> None:
        class FakeWriteApi:
            def __init__(self) -> None:
                self.writes = []

            def write(self, **kwargs) -> None:
                self.writes.append(kwargs)

            def close(self) -> None:
                pass

        class FakeClient:
            def __init__(self, **kwargs) -> None:
                self.write_api_instance = FakeWriteApi()

            def write_api(self, **kwargs) -> FakeWriteApi:
                return self.write_api_instance

            def close(self) -> None:
                pass

        config = InfluxConfig(
            url="http://127.0.0.1:8086",
            org="test-org",
            bucket="environment",
            token="test-token",
        )
        with patch("influxdb_client.InfluxDBClient", FakeClient):
            sink = InfluxDBSink(config)
            sink.write(
                [
                    DataPoint("environment", {"temperature_c": 20.0}),
                    DataPoint("motion", {"acceleration_x_g": 0.1}),
                    DataPoint(
                        "hardware_events",
                        {"event_type": "joystick"},
                        bucket="events",
                    ),
                ]
            )
            writes = sink._write_api.writes
            sink.close()

        self.assertEqual(
            {write["bucket"] for write in writes},
            {"environment", "motion_raw", "events"},
        )
        self.assertTrue(all(write["org"] == "test-org" for write in writes))


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.01)
    raise AssertionError("condition was not met before timeout")


if __name__ == "__main__":
    unittest.main()
