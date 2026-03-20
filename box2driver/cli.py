# Copyright (c) 2026 boxjod / Box2AI Team
# All Rights Reserved.
"""
CLI entry point for box2driver.

Usage:
    box2driver                          # Start Dashboard + STS
    box2driver --bridge                 # Also enable com0com/socat
    box2driver --no-web                 # STS only, no Web UI
    box2driver -p COM5                  # Specify serial port
    box2driver --list                   # List available serial ports
"""
import sys


def main():
    """Main CLI entry point — delegates to gateway module."""
    from box2driver.gateway import main as gateway_main
    gateway_main()


if __name__ == "__main__":
    main()
