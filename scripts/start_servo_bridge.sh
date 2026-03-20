#!/bin/bash
# Box2Driver Virtual Servo Bridge - Linux/macOS Launcher
#
# Prerequisites:
#   pip install pyserial websockets
#   sudo apt install -y socat    (Ubuntu)
#   brew install socat            (macOS)
#
# Usage:
#   ./start_servo_bridge.sh                      # Full auto
#   ./start_servo_bridge.sh -v                   # Verbose
#   ./start_servo_bridge.sh --ws ws://host:8765  # Custom gateway

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check Python
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "Error: Python not found. Install Python 3.8+"
    exit 1
fi

# Check socat
if ! command -v socat &>/dev/null; then
    echo "Warning: socat not found. Virtual serial ports won't work."
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "  Install: brew install socat"
    else
        echo "  Install: sudo apt install -y socat"
    fi
    echo "  Falling back to TCP mode."
    echo ""
fi

exec "$PYTHON" -u virtual_servo_bridge.py "$@"
