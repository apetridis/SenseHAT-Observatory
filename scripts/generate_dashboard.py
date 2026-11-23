#!/usr/bin/env python3
"""Generate the provisioned SenseHAT Observatory Grafana dashboard JSON."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "deploy/grafana/dashboards/sensehat-observatory.json"
DATASOURCE = {"type": "influxdb", "uid": "sensehat-influxdb"}


def flux(bucket: str, measurement: str, fields: str, windowed: bool = True) -> str:
    """Create a Flux time-series query for a bucket and field regex."""
    pipeline = (
        f'from(bucket: "{bucket}")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n'
        f'  |> filter(fn: (r) => r["_field"] =~ /{fields}/)'
    )
    if windowed:
        pipeline += '\n  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)'
    return pipeline + '\n  |> yield(name: "mean")'


def last_value(bucket: str, measurement: str, field: str) -> str:
    return (
        f'from(bucket: "{bucket}")\n'
        "  |> range(start: -30d)\n"
        f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n'
        f'  |> filter(fn: (r) => r["_field"] == "{field}")\n'
        "  |> last()"
    )


def collector_status() -> str:
    """Report an offline status when the last heartbeat is stale."""
    return (
        'from(bucket: "health")\n'
        "  |> range(start: -30d)\n"
        '  |> filter(fn: (r) => r["_measurement"] == "collector_health")\n'
        '  |> filter(fn: (r) => r["_field"] == "status")\n'
        "  |> last()\n"
        '  |> map(fn: (r) => ({r with _value: if now() - r._time > 90s then "offline" else r._value}))'
    )


def panel(
    panel_id: int,
    title: str,
    query: str,
    x: int,
    y: int,
    width: int,
    height: int,
    visualization: str = "timeseries",
    unit: str = "short",
    description: str = "",
    thresholds: list[tuple[float, str]] | None = None,
    axis_label: str | None = None,
) -> dict:
    labels = {
        "accelerationg": "g",
        "celsius": "°C",
        "degree": "°",
        "microtesla": "µT",
        "percent": "%",
        "pressurehpa": "hPa",
        "radians": "rad/s",
        "short": "Value",
    }
    config = {
        "defaults": {
            "unit": unit,
            "color": {"mode": "palette-classic"},
            "custom": {
                "drawStyle": "line",
                "lineInterpolation": "smooth",
                "lineWidth": 2,
                "fillOpacity": 12,
                "showPoints": "never",
                "spanNulls": False,
                "axisLabel": axis_label or labels.get(unit, unit),
                "scaleDistribution": {"type": "linear"},
            },
        },
        "overrides": [],
    }
    if thresholds:
        config["defaults"]["thresholds"] = {
            "mode": "absolute",
            "steps": [{"color": "green", "value": None}]
            + [{"color": color, "value": value} for value, color in thresholds],
        }
    result = {
        "id": panel_id,
        "title": title,
        "description": description,
        "type": visualization,
        "datasource": DATASOURCE,
        "gridPos": {"h": height, "w": width, "x": x, "y": y},
        "fieldConfig": config,
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [{"query": query, "refId": "A", "queryType": "flux"}],
    }
    if visualization == "stat":
        result["options"] = {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
        }
        result["fieldConfig"]["defaults"]["custom"] = {"fillOpacity": 20}
    elif visualization == "table":
        result["options"] = {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["count"], "fields": ""},
        }
    return result


def row(panel_id: int, title: str, y: int) -> dict:
    return {
        "id": panel_id,
        "title": title,
        "type": "row",
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def build_dashboard() -> dict:
    panels: list[dict] = []
    next_id = 1

    def add(*args, **kwargs) -> None:
        nonlocal next_id
        panels.append(panel(next_id, *args, **kwargs))
        next_id += 1

    panels.append(row(100, "Overview", 0))
    add(
        "Temperature",
        last_value("environment", "environment", "temperature_humidity_c"),
        0,
        1,
        4,
        5,
        "stat",
        "celsius",
        "Most recent temperature from the humidity sensor.",
        [(-10, "blue"), (0, "green"), (30, "orange"), (40, "red")],
    )
    add(
        "Relative humidity",
        last_value("environment", "environment", "relative_humidity_percent"),
        4,
        1,
        4,
        5,
        "stat",
        "percent",
        "Most recent relative humidity.",
        [(30, "blue"), (60, "green"), (80, "orange")],
    )
    add(
        "Atmospheric pressure",
        last_value("environment", "environment", "pressure_hpa"),
        8,
        1,
        4,
        5,
        "stat",
        "pressurehpa",
        "Most recent atmospheric pressure.",
        [(950, "orange"), (1050, "red")],
    )
    add(
        "Dew point",
        last_value("environment", "environment", "dew_point_c"),
        12,
        1,
        4,
        5,
        "stat",
        "celsius",
        "Most recent calculated dew point.",
    )
    add(
        "Collector status",
        collector_status(),
        16,
        1,
        4,
        5,
        "stat",
        "string",
        "Collector-reported status.",
    )
    add(
        "Last sensor update",
        'from(bucket: "environment")\n'
        "  |> range(start: -30d)\n"
        '  |> filter(fn: (r) => r["_measurement"] == "environment")\n'
        '  |> filter(fn: (r) => r["_field"] == "temperature_humidity_c")\n'
        "  |> last()\n"
        "  |> map(fn: (r) => ({r with _value: int(v: r._time) / 1000000}))",
        20,
        1,
        4,
        5,
        "stat",
        "dateTimeAsIso",
        "Timestamp of the latest environment sample.",
    )

    panels.append(row(101, "Environmental monitoring", 6))
    env_panels = [
        ("Temperature history", "temperature_humidity_c", "celsius", "Humidity sensor temperature."),
        ("Temperature sensor comparison", "temperature_(humidity|pressure)_c", "celsius", "Humidity and pressure sensor temperatures."),
        ("Relative humidity history", "relative_humidity_percent", "percent", "Relative humidity."),
        ("Atmospheric pressure history", "pressure_hpa", "pressurehpa", "Atmospheric pressure in hPa."),
        ("Dew point", "dew_point_c", "celsius", "Calculated dew point in °C."),
        ("Absolute humidity", "absolute_humidity_g_m3", "short", "Absolute humidity in grams per cubic metre (g/m³)."),
        ("Daily temperature minimum / maximum / average", "temperature_humidity_c", "celsius", "Daily windows summarize minimum, maximum, and average temperature from both environmental sensors."),
        ("Daily humidity minimum / maximum / average", "relative_humidity_percent", "percent", "Daily windows summarize minimum, maximum, and average relative humidity."),
        ("Daily pressure minimum / maximum / average", "pressure_hpa", "pressurehpa", "Daily windows summarize minimum, maximum, and average atmospheric pressure."),
        ("Temperature difference (humidity - pressure sensor)", "temperature_difference_c", "celsius", "Humidity-sensor temperature minus pressure-sensor temperature."),
    ]
    positions = [
        (0, 7),
        (12, 7),
        (0, 14),
        (12, 14),
        (0, 21),
        (12, 21),
        (0, 28),
        (12, 28),
        (0, 35),
        (12, 35),
    ]
    for (title, fields, unit, description), (x, y) in zip(env_panels, positions):
        query = flux("environment", "environment", fields)
        if title.startswith("Daily"):
            field_filter = (
                'r["_field"] =~ /temperature_(humidity|pressure)_c/'
                if fields == "temperature_humidity_c"
                else f'r["_field"] == "{fields}"'
            )
            query = (
                'from(bucket: "environment")\n'
                "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
                '  |> filter(fn: (r) => r["_measurement"] == "environment")\n'
                f"  |> filter(fn: (r) => {field_filter})\n"
                '  |> aggregateWindow(every: 1d, fn: min, createEmpty: false)\n'
                '  |> yield(name: "daily_min")\n'
                'from(bucket: "environment")\n'
                "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
                '  |> filter(fn: (r) => r["_measurement"] == "environment")\n'
                f"  |> filter(fn: (r) => {field_filter})\n"
                '  |> aggregateWindow(every: 1d, fn: max, createEmpty: false)\n'
                '  |> yield(name: "daily_max")\n'
                'from(bucket: "environment")\n'
                "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
                '  |> filter(fn: (r) => r["_measurement"] == "environment")\n'
                f"  |> filter(fn: (r) => {field_filter})\n"
                '  |> aggregateWindow(every: 1d, fn: mean, createEmpty: false)\n'
                '  |> yield(name: "daily_mean")'
            )
        axis_label = "g/m³" if title == "Absolute humidity" else None
        add(
            title,
            query,
            x,
            y,
            12,
            6,
            "timeseries",
            unit,
            description,
            axis_label=axis_label,
        )

    panels.append(row(102, "Motion monitoring", 43))
    motion_charts = [
        ("Accelerometer X / Y / Z (window mean)", "acceleration_[xyz]_g_mean", "accelerationg", "Mean acceleration in g; raw samples are retained for the configured short-term period."),
        ("Gyroscope X / Y / Z (window mean)", "gyroscope_[xyz]_rad_s_mean", "radians", "Mean angular velocity in radians/second."),
        ("Magnetometer X / Y / Z (window mean)", "magnetometer_[xyz]_ut_mean", "microtesla", "Mean magnetic field in microtesla."),
        ("Pitch / roll / yaw (window mean)", "(pitch|roll|yaw)_deg_mean", "degree", "Mean orientation angles in degrees."),
    ]
    for index, (title, fields, unit, description) in enumerate(motion_charts):
        add(
            title,
            flux("motion_aggregate", "motion_aggregate", fields),
            12 * (index % 2),
            44 + (index // 2) * 7,
            12,
            6,
            "timeseries",
            unit,
            description,
        )
    add(
        "Compass heading",
        flux("motion_aggregate", "motion_aggregate", "compass_heading_deg_mean"),
        0,
        58,
        12,
        6,
        "timeseries",
        "degree",
        "Compass heading in degrees.",
    )
    add(
        "Movement detection",
        flux("motion_aggregate", "motion_aggregate", "movement_detected", windowed=False).replace(
            '  |> yield(name: "mean")',
            '  |> map(fn: (r) => ({r with _value: if r._value then 1.0 else 0.0}))\n'
            '  |> yield(name: "mean")',
        ),
        12,
        58,
        12,
        6,
        "timeseries",
        "short",
        "True indicates an acceleration change exceeding the configured threshold.",
        axis_label="Detection (1=yes)",
    )
    add(
        "Raw 10 Hz accelerometer (short-term retention)",
        flux("motion_raw", "motion", "acceleration_[xyz]_g"),
        0,
        65,
        24,
        6,
        "timeseries",
        "accelerationg",
        "High-resolution raw accelerometer samples. Raw IMU records expire after the configured retention period.",
    )

    panels.append(row(103, "Hardware and collector health", 72))
    event_query = (
        'from(bucket: "events")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        '  |> filter(fn: (r) => r["_measurement"] == "hardware_events")\n'
        '  |> pivot(rowKey: ["_time", "device_id"], columnKey: ["_field"], valueColumn: "_value")\n'
        "  |> sort(columns: [\"_time\"], desc: true)"
    )
    add(
        "Joystick, movement and orientation events",
        event_query,
        0,
        73,
        16,
        8,
        "table",
        "short",
        "Hardware events include joystick press/release, movement detection and orientation changes.",
    )
    add(
        "Sensor errors",
        last_value("health", "collector_health", "sensor_errors"),
        16,
        73,
        4,
        4,
        "stat",
        "short",
        "Cumulative sensor read errors since collector restart.",
    )
    add(
        "Dropped writes",
        last_value("health", "collector_health", "dropped_points"),
        20,
        73,
        4,
        4,
        "stat",
        "short",
        "Points discarded after queue overflow or exhausted write retries.",
    )
    add(
        "Data collection status",
        collector_status(),
        16,
        77,
        8,
        4,
        "stat",
        "string",
        "Collector status: ok, degraded, or stopping.",
    )
    add(
        "Writer queue depth",
        last_value("health", "collector_health", "queue_depth"),
        0,
        81,
        8,
        4,
        "timeseries",
        "short",
        "Points waiting to be written to InfluxDB.",
    )
    add(
        "InfluxDB write errors",
        last_value("health", "collector_health", "write_errors"),
        8,
        81,
        8,
        4,
        "timeseries",
        "short",
        "Cumulative failed write attempts since collector restart.",
    )

    return {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                }
            ]
        },
        "description": "Open-source environmental and motion monitoring for Raspberry Pi Sense HAT.",
        "editable": False,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 2,
        "id": None,
        "links": [],
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 39,
        "tags": ["sense-hat", "environment", "motion", "raspberry-pi"],
        "templating": {"list": []},
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {
            "refresh_intervals": ["30s", "1m", "5m", "15m", "30m"],
            "time_options": [
                "1h",
                "6h",
                "24h",
                "7d",
                "30d",
                "now-1h",
                "now-6h",
                "now-24h",
                "now-7d",
                "now-30d",
            ],
        },
        "timezone": "browser",
        "title": "SenseHAT Observatory",
        "uid": "sensehat-observatory",
        "version": 1,
        "weekStart": "",
    }


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build_dashboard(), indent=2) + "\n", encoding="utf-8")
