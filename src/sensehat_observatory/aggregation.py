"""In-process fixed-window motion aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import DataPoint


class MotionAggregator:
    """Keep only the current motion window and emit its summary on rollover."""

    def __init__(self, interval_seconds: int, aggregate_bucket: str) -> None:
        self._interval = interval_seconds
        self._bucket = aggregate_bucket
        self._window_start: datetime | None = None
        self._numeric: dict[str, list[float]] = {}
        self._movement_detected = False
        self._tags: dict[str, str] = {}

    def add(self, point: DataPoint) -> DataPoint | None:
        """Add a raw motion point and return a completed previous window, if any."""
        window_start = self._floor(point.timestamp)
        completed = None
        if self._window_start is not None and window_start > self._window_start:
            completed = self._finish()
        if self._window_start != window_start:
            self._window_start = window_start
            self._numeric = {}
            self._movement_detected = False
        self._tags = point.tags
        for field_name, value in point.fields.items():
            if isinstance(value, bool):
                if field_name == "movement_detected":
                    self._movement_detected = self._movement_detected or value
            elif isinstance(value, (int, float)):
                self._numeric.setdefault(field_name, []).append(float(value))
        return completed

    def flush(self) -> DataPoint | None:
        """Emit any partial window during a graceful shutdown."""
        return self._finish(partial=True)

    def _floor(self, timestamp: datetime) -> datetime:
        timestamp = timestamp.astimezone(timezone.utc)
        seconds = int(timestamp.timestamp())
        floored = seconds - seconds % self._interval
        return datetime.fromtimestamp(floored, timezone.utc)

    def _finish(self, partial: bool = False) -> DataPoint | None:
        if self._window_start is None or not self._numeric:
            self._window_start = None
            return None
        fields: dict[str, int | float | str | bool] = {}
        for name, values in self._numeric.items():
            fields[f"{name}_mean"] = sum(values) / len(values)
            fields[f"{name}_min"] = min(values)
            fields[f"{name}_max"] = max(values)
        fields["movement_detected"] = self._movement_detected
        timestamp = (
            datetime.now(timezone.utc)
            if partial
            else self._window_start + timedelta(seconds=self._interval)
        )
        result = DataPoint(
            "motion_aggregate",
            fields,
            self._tags,
            timestamp,
            self._bucket,
        )
        self._window_start = None
        self._numeric = {}
        self._movement_detected = False
        return result
