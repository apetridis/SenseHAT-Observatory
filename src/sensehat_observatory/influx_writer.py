"""Bounded asynchronous batching and retrying writes to InfluxDB 2.x."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict

from .config import InfluxConfig
from .models import DataPoint

LOGGER = logging.getLogger(__name__)


class PointSink:
    """Interface implemented by the InfluxDB sink and deterministic test fakes."""

    def write(self, points: list[DataPoint]) -> None:
        """Write a batch or raise an exception for retry."""
        raise NotImplementedError

    def close(self) -> None:
        """Release sink resources."""


class InfluxDBSink(PointSink):
    """Translate points and write batches using the official v2 client."""

    def __init__(self, config: InfluxConfig) -> None:
        if not config.token or not config.org or not config.bucket:
            raise ValueError(
                "INFLUX_TOKEN, INFLUX_ORG and INFLUX_BUCKET must be configured"
            )
        try:
            from influxdb_client import InfluxDBClient, Point, WritePrecision
            from influxdb_client.client.write_api import SYNCHRONOUS
        except ImportError as exc:
            raise RuntimeError(
                "influxdb-client is missing; install the project dependencies first"
            ) from exc

        self._config = config
        self._point_type = Point
        self._write_precision = WritePrecision
        self._client = InfluxDBClient(
            url=config.url,
            token=config.token,
            org=config.org,
            timeout=10_000,
        )
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)

    def write(self, points: list[DataPoint]) -> None:
        """Group measurements by bucket and synchronously send each bucket batch."""
        grouped: dict[str, list[object]] = defaultdict(list)
        for data_point in points:
            bucket = data_point.bucket or self._bucket_for(data_point.measurement)
            point = self._point_type(data_point.measurement)
            for name, value in data_point.tags.items():
                point.tag(name, value)
            for name, value in data_point.fields.items():
                point.field(name, value)
            point.time(data_point.timestamp, self._write_precision.NS)
            grouped[bucket].append(point)

        for bucket, records in grouped.items():
            self._write_api.write(bucket=bucket, org=self._config.org, record=records)

    def _bucket_for(self, measurement: str) -> str:
        buckets = {
            "environment": self._config.bucket,
            "motion": self._config.motion_raw_bucket,
            "motion_aggregate": self._config.motion_aggregate_bucket,
            "orientation": self._config.motion_raw_bucket,
            "hardware_events": self._config.events_bucket,
            "collector_health": self._config.health_bucket,
        }
        try:
            bucket = buckets[measurement]
        except KeyError as exc:
            raise ValueError(f"No InfluxDB bucket configured for {measurement}") from exc
        if not bucket:
            raise ValueError(f"No InfluxDB bucket configured for {measurement}")
        return bucket

    def close(self) -> None:
        """Close the write API before its parent client."""
        self._write_api.close()
        self._client.close()


class BufferedWriter:
    """Bounded producer queue with a background batch and retry worker."""

    def __init__(
        self,
        sink: PointSink,
        capacity: int = 20_000,
        batch_size: int = 250,
        max_attempts: int = 8,
        retry_base_seconds: float = 1.0,
    ) -> None:
        self._sink = sink
        self._queue: queue.Queue[DataPoint] = queue.Queue(maxsize=capacity)
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._write_errors = 0
        self._dropped_points = 0

    def start(self) -> None:
        """Start the single writer worker."""
        if self._thread is not None:
            raise RuntimeError("writer has already been started")
        self._thread = threading.Thread(
            target=self._run, name="influx-writer", daemon=True
        )
        self._thread.start()

    def submit(self, point: DataPoint) -> bool:
        """Queue a point without waiting; return false when the queue is full."""
        try:
            self._queue.put_nowait(point)
        except queue.Full:
            with self._lock:
                self._dropped_points += 1
            LOGGER.error(
                "InfluxDB queue is full; point dropped",
                extra={"measurement": point.measurement},
            )
            return False
        return True

    @property
    def queue_depth(self) -> int:
        """Current number of queued points."""
        return self._queue.qsize()

    @property
    def write_errors(self) -> int:
        """Cumulative failed write attempts."""
        with self._lock:
            return self._write_errors

    @property
    def dropped_points(self) -> int:
        """Cumulative points dropped on queue overflow or exhausted retries."""
        with self._lock:
            return self._dropped_points

    def stop(self, timeout: float = 30.0) -> None:
        """Signal a graceful drain, then close the sink."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                LOGGER.error(
                    "Writer did not drain before shutdown",
                    extra={"queued_points": self.queue_depth},
                )
                return
        self._sink.close()

    def _run(self) -> None:
        batch: list[DataPoint] = []
        while not self._stop.is_set() or not self._queue.empty():
            try:
                first = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            batch.append(first)
            flush_at = time.monotonic() + 0.1
            while len(batch) < self._batch_size:
                timeout = max(0.0, flush_at - time.monotonic())
                if timeout == 0:
                    break
                try:
                    batch.append(self._queue.get(timeout=timeout))
                except queue.Empty:
                    break
            self._write_batch(batch)
            for _ in batch:
                self._queue.task_done()
            batch.clear()

    def _write_batch(self, batch: list[DataPoint]) -> None:
        for attempt in range(1, self._max_attempts + 1):
            try:
                self._sink.write(batch)
                return
            except Exception:
                with self._lock:
                    self._write_errors += 1
                LOGGER.exception(
                    "InfluxDB batch write failed",
                    extra={"attempt": attempt, "batch_size": len(batch)},
                )
                if attempt == self._max_attempts or self._stop.is_set():
                    with self._lock:
                        self._dropped_points += len(batch)
                    LOGGER.error(
                        "Discarding failed InfluxDB batch",
                        extra={"batch_size": len(batch)},
                    )
                    return
                delay = min(self._retry_base_seconds * (2 ** (attempt - 1)), 30.0)
                self._stop.wait(delay)


def create_sink(config: InfluxConfig) -> PointSink:
    """Create the configured InfluxDB sink."""
    return InfluxDBSink(config)
