# Copyright (c) 2026 boxjod / Box2AI Team
# All Rights Reserved.
"""
Box2Driver - ESP32 multi-mode robotic arm controller tools.

Usage:
    box2driver                          # Start Dashboard + STS virtual servo
    box2driver --bridge                 # Also enable com0com/socat virtual COM
    box2driver --no-web                 # STS only, no Web UI
    box2driver --list                   # List available serial ports

Python API:
    from box2driver import Box2DriverClient
    client = Box2DriverClient()
    client.start()
    positions = client.get_all_positions()
"""

__version__ = "0.4.4"
__author__ = "boxjod / Box2AI Team"

from box2driver.client import Box2DriverClient

__all__ = ["Box2DriverClient"]
