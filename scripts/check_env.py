#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
Environment check - verifies all dependencies are installed correctly.

Usage:
    python scripts/check_env.py
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

errors = []
ok = []

# 1. box2driver_client
try:
    from box2driver_client import Box2DriverClient
    client = Box2DriverClient("ws://localhost:8765")
    assert hasattr(client, 'send_positions')
    ok.append("box2driver_client")
except Exception as e:
    errors.append(f"box2driver_client: {e}")

# 2. numpy
try:
    import numpy as np
    ok.append(f"numpy {np.__version__}")
except Exception as e:
    errors.append(f"numpy: {e}")

# 3. pyserial
try:
    import serial
    ok.append(f"pyserial {serial.__version__}")
except Exception as e:
    errors.append(f"pyserial: {e}")

# 4. websockets
try:
    import websockets
    ok.append(f"websockets {websockets.__version__}")
except Exception as e:
    errors.append(f"websockets: {e}")

# 5. pynput (optional)
try:
    from pynput import keyboard
    ok.append("pynput (keyboard control)")
except ImportError:
    ok.append("pynput: NOT installed (keyboard control unavailable)")

# 6. kinematics (lerobot-kinematics C扩展 或 so100_kinematics 纯Python)
try:
    from lerobot_kinematics import lerobot_IK, lerobot_FK, get_robot
    ok.append("lerobot-kinematics C扩展 (IK available)")
except ImportError:
    try:
        from so100_kinematics import lerobot_IK, lerobot_FK, get_robot
        ok.append("so100_kinematics 纯Python (IK available)")
    except ImportError:
        ok.append("kinematics: NOT installed (joint mode only)")

# 7. lerobot (optional)
try:
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    ok.append("lerobot (dataset collection available)")
except ImportError:
    ok.append("lerobot: NOT installed (dataset collection unavailable)")

# 8. torch (optional)
try:
    import torch
    ok.append(f"torch {torch.__version__} (model deployment available)")
except ImportError:
    ok.append("torch: NOT installed (model deployment unavailable)")

# Report
print("=" * 50)
print("Box2Driver Lerobot-ESP32 Environment Check")
print("=" * 50)
print(f"Python: {sys.version}")
print(f"Platform: {sys.platform}")
print()
for item in ok:
    print(f"  [OK] {item}")
print()
if errors:
    for item in errors:
        print(f"  [FAIL] {item}")
    print(f"\n{len(errors)} error(s). Install: pip install -r requirements.txt")
    sys.exit(1)
else:
    print("All base checks passed!")
