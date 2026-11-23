"""Command-line entry point for SenseHAT Observatory."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from pathlib import Path

from .collector import ObservatoryCollector
from .config import load_config
from .influx_writer import BufferedWriter, create_sink
from .logging_config import configure_logging
from .sensors import SenseHatSensors


def main() -> None:
    """Load configuration and run the collector until interrupted."""
    parser = argparse.ArgumentParser(description="SenseHAT Observatory collector")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/etc/sensehat-observatory/observatory.yaml"),
        help="YAML configuration path",
    )
    arguments = parser.parse_args()
    configure_logging()
    logger = logging.getLogger(__name__)
    config = load_config(arguments.config)
    sensors = SenseHatSensors()
    writer = BufferedWriter(create_sink(config.influx))
    collector = ObservatoryCollector(config, sensors, writer)
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
    try:
        collector.run(stop_event)
    except Exception:
        logger.exception("Collector terminated unexpectedly")
        raise


if __name__ == "__main__":
    main()
