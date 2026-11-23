"""Pure environmental calculation helpers."""

from __future__ import annotations

import math


def calculate_dew_point_c(temperature_c: float, relative_humidity_percent: float) -> float | None:
    """Calculate dew point using the Magnus approximation.

    Returns ``None`` for zero relative humidity, where the approximation is
    undefined. Input humidity must be within its physical 0..100 percent range.
    """
    _validate_humidity(relative_humidity_percent)
    if relative_humidity_percent == 0:
        return None
    if not math.isfinite(temperature_c):
        raise ValueError("temperature must be finite")
    if temperature_c <= -243.12:
        raise ValueError("temperature is outside the Magnus approximation domain")
    gamma = math.log(relative_humidity_percent / 100.0) + (
        17.62 * temperature_c / (243.12 + temperature_c)
    )
    return 243.12 * gamma / (17.62 - gamma)


def calculate_absolute_humidity_g_m3(
    temperature_c: float, relative_humidity_percent: float
) -> float | None:
    """Calculate absolute humidity in g/m³ using saturation vapor pressure."""
    _validate_humidity(relative_humidity_percent)
    if not math.isfinite(temperature_c):
        raise ValueError("temperature must be finite")
    if temperature_c <= -243.12:
        raise ValueError("temperature is outside the saturation pressure model domain")
    if relative_humidity_percent == 0:
        return 0.0
    saturation_pressure_hpa = 6.112 * math.exp(
        (17.67 * temperature_c) / (temperature_c + 243.5)
    )
    vapor_pressure_hpa = saturation_pressure_hpa * relative_humidity_percent / 100.0
    return 216.7 * vapor_pressure_hpa / (temperature_c + 273.15)


def _validate_humidity(relative_humidity_percent: float) -> None:
    if not math.isfinite(relative_humidity_percent) or not (
        0 <= relative_humidity_percent <= 100
    ):
        raise ValueError("relative humidity must be between 0 and 100 percent")
