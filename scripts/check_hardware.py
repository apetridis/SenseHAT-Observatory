#!/usr/bin/env python3
"""Smoke-test Sense HAT APIs without writing to the LED matrix."""

from sensehat_observatory.sensors import SenseHatSensors


def main() -> None:
    """Read each sensor API once so installation detects hardware problems."""
    sensors = SenseHatSensors()
    sensors.read_environment()
    sensors.read_motion(movement_threshold_g=0.25)
    sensors.read_orientation()
    sensors.read_joystick_events()
    print("Sense HAT environmental, IMU, orientation and joystick APIs are accessible.")


if __name__ == "__main__":
    main()
