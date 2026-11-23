"""Standard logging setup for the collector."""

from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    """Configure console logging with an environment-selectable level."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        raise ValueError(f"Invalid LOG_LEVEL: {level_name}")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
