English | [中文](README_zh.md)

# LeRobot-ESP32: Fully Wireless LeRobot Arm Control

**Cut the cables. Set your LeRobot free.**

LeRobot-ESP32 uses ESP-NOW wireless protocol to deliver fully wireless teleoperation, data collection, and AI deployment for LeRobot robot arms. No cables between Leader and Follower — just 30Hz sync with <5ms latency, plus a complete PC toolchain from data collection to model deployment.

## Why Go Wireless?

- Cables clutter your desk and limit arm placement
- Wired connections suffer from disconnections caused by cable tugging
- Stock LeRobot requires one USB cable per arm to the PC — poor scalability
- **With wireless**: Leader and Follower are independently powered, placed anywhere, auto-connect on boot

## System Architecture

```
┌─────────┐   ESP-NOW 30Hz   ┌──────────┐
│ Leader  │ ──────────────→  │ Follower │
│(read pos)│ ←────────────── │(write srv)│
└─────────┘   feedback        └──────────┘
                                   ↑
┌─────────┐   ESP-NOW            │
│ Gateway │ ←────────────────────┘
│(USB→PC) │ ──→ Serial JSON ──→ PC
└─────────┘
     ↓
┌─────────────────────────────────────┐
│ PC Toolchain                         │
│ - Web Dashboard (monitor & control)  │
│ - Virtual serial bridge (FD direct)  │
│ - LeRobot data collection & deploy   │
│ - Python API / Keyboard / JoyCon     │
└─────────────────────────────────────┘
```

## Key Features

- **ESP-NOW Wireless Sync** — Leader→Follower 30Hz real-time position sync, no USB cables
- **Virtual Servo Serial Bridge** — Map ESP32 devices to virtual COM ports, use with FD software directly
- **Gateway Dashboard** — Browser-based real-time monitoring, control, and waveform display
- **LeRobot Integration** — Wireless dataset collection + model inference deployment
- **Python Client API** — Data reading, control, recording & playback
- **Keyboard IK Control** — Cartesian / joint-space keyboard teleoperation
- **JoyCon IK Bridge** — Joy-Con controller pose → IK → robot arm
- **Pre-built Firmware** — Flash and go, no build environment needed

## Quick Start

### 1. Install

```bash
conda create -n box2driver python=3.11 -y
conda activate box2driver
pip install dist_pkg/box2driver-0.4.4-py3-none-any.whl
```

### 2. Flash Firmware

Pre-built firmware binaries are in the `bin/` directory. No build environment required.

**Update firmware (factory-flashed devices)**

If the device was previously flashed, only the firmware.bin file is needed:

```bash
pip install esptool
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x10000 bin/box2driver_v0.4.4_firmware.bin
```

Or use the Espressif Flash Download Tool: firmware.bin → address 0x10000

**First-time full flash (new boards)**

All 3 files are required:

| File | Address | Description |
|------|---------|-------------|
| box2driver_v0.4.4_bootloader.bin | 0x1000 | Bootloader |
| box2driver_v0.4.4_partitions.bin | 0x8000 | Partition table |
| box2driver_v0.4.4_firmware.bin | 0x10000 | Application firmware |

```bash
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x1000 bin/box2driver_v0.4.4_bootloader.bin \
    0x8000 bin/box2driver_v0.4.4_partitions.bin \
    0x10000 bin/box2driver_v0.4.4_firmware.bin
```

Or use `flash_download_tool/flash_download_tool_3.9.9_R2.exe` (Windows GUI).

### 3. Launch

Connect a Gateway-mode ESP32 to your PC via USB:

```bash
box2driver                     # Auto-detect serial port, start Web + STS virtual serial
box2driver -p COM5             # Specify serial port
box2driver --bridge            # Also start com0com/socat virtual COM port
box2driver --no-web            # No web UI, virtual serial only
box2driver --list              # List available serial ports
```

Or run the script directly:

```bash
python scripts/gateway_dashboard.py            # Auto-detect CP210x serial
python scripts/gateway_dashboard.py -p COM5    # Specify port
python scripts/gateway_dashboard.py --bridge   # Also start virtual serial bridge
```

After launch, the system automatically:
1. Detects platform and virtual serial driver (Windows: com0com / Linux: socat / macOS: socat)
2. Connects to Gateway WebSocket
3. Discovers all ESP32 devices (Follower, Leader, etc.)
4. Creates an independent virtual serial port for each device
5. Prints the port mapping table

```
  Device      | MAC           | Servos    | Port
  ------------|---------------|-----------|--------------------
  Follower    | ...2D:B6:94 | ID=2      | COM51
  Leader      | ...2C:3E:28 | ID=1-6    | socket://localhost:6570
```

**Windows**: Open FD software → select COM51 → baud rate 1000000 → scan servos
**Linux/macOS**: Use `PortHandler('/tmp/vservo0')` with `scservo_sdk`

Virtual serial driver prerequisites:

| Platform | Installation |
|----------|-------------|
| Windows | Install [com0com](https://sourceforge.net/projects/com0com/), run as admin: `setupc install PortName=COM50 PortName=COM51` |
| Ubuntu | `sudo apt install -y socat` |
| macOS | `brew install socat` |

### 4. Python API

```python
from box2driver_client import Box2DriverClient

# Snapshot mode
client = Box2DriverClient()
client.start()
positions = client.get_all_positions()
client.send_positions([{"id": 1, "pos": 2048}])
client.stop()

# Streaming mode
for dev_id, frame in client.stream():
    print(dev_id, frame['servos'])
```

## Feature Details

### Gateway Dashboard

Web-based visualization panel:
- Multi-arm real-time waveform charts
- JoyCon 6DOF pose display
- Device list and status monitoring
- Control panel (torque toggle, position commands)
- Trajectory recording and playback

```bash
box2driver -p COM5 --port 8080
```

### Keyboard Control (IK / Joint Space)

```bash
python examples/keyboard_ik_control.py               # IK mode
python examples/keyboard_ik_control.py --mode joint   # Joint mode
python examples/keyboard_ik_control.py --mac AA:BB:CC:DD:EE:FF  # Specify target
```

**IK mode key bindings:**

| Key | Function | Key | Function |
|-----|----------|-----|----------|
| W/S | X forward/backward | Q/E | Roll +/- |
| A/D | Y left/right | G/T | Pitch +/- |
| R/F | Z up/down | Z/C | Gripper open/close |
| 0 | Home position | ESC | Quit |

**Joint mode key bindings:**

| Key | Function | Key | Function |
|-----|----------|-----|----------|
| 1/Q | Joint 1 base +/- | 4/R | Joint 4 wrist pitch +/- |
| 2/W | Joint 2 shoulder +/- | 5/T | Joint 5 wrist roll +/- |
| 3/E | Joint 3 elbow +/- | 6/Y | Joint 6 gripper +/- |

IK mode additional dependencies:
```bash
pip install pynput
git clone https://github.com/box-robotics/lerobot-kinematics.git
cd lerobot-kinematics && pip install -e .
```

### LeRobot Dataset Collection

```bash
python scripts/gateway_dashboard.py -p COM5
python scripts/lerobot_collect.py --repo-id box2driver/pick_cup
python scripts/lerobot_collect.py --repo-id box2driver/pick_cup --duration 10 --num-episodes 5
```

Full dependencies:
```bash
pip install -r requirements-lerobot.txt
git clone https://github.com/huggingface/lerobot.git
cd lerobot && pip install -e .
```

### LeRobot Model Deployment

```bash
python scripts/lerobot_deploy.py \
    --policy-path ./outputs/train/act_box2driver/checkpoints/last/pretrained_model
```

### JoyCon IK Bridge

```bash
python scripts/joycon_ik_bridge.py
python scripts/joycon_ik_bridge.py --no-ik
```

## 5 Device Modes

Switch by long-pressing the BOOT button. Last selection is saved in NVS:

| Mode | Servos | Function |
|------|--------|----------|
| Follower | Read/Write | Receive sync → write servos → feedback |
| Leader | Read-only | Read positions → sync → feedback |
| M-Leader | Read-only | Same as Leader but broadcast to multiple Followers |
| Gateway | None | ESP-NOW ↔ Serial JSON bridge |
| JoyCon | None | Bluetooth controller → IK → sync |

## LED Indicators

Two onboard WS2812 RGB LEDs (GPIO23). Left LED (LED0) shows **current mode**, right LED (LED1) shows **current status**.

**LED0 — Mode (solid):**

| Mode | Color |
|------|-------|
| Follower | Green |
| Leader | Blue |
| M-Leader | Dark blue |
| Gateway | Purple |
| JoyCon | Gray |

**LED1 — Status:**

| Status | Color | Pattern | Trigger |
|--------|-------|---------|---------|
| Searching / Disconnected | Orange | Blinking | Scanning servos, Leader disconnected |
| Waiting | Blue | Solid | Idle, waiting for connection |
| Pending confirmation | Dark blue | Blinking | Received Leader handshake, awaiting button press |
| Connected | Green | Solid | Bound and syncing |
| Taken over | Purple | Solid | Controlled by Gateway/PC |
| Overloaded | Red | Both LEDs blinking | Torque protection triggered |

> Design principle: Red = fault, Green = normal, Blue = waiting, Purple = external control.

## Directory Structure

```
lerobot-esp32/
├── scripts/                          # Core scripts
│   ├── gateway_dashboard.py          # Gateway Web Dashboard server
│   ├── gateway_dashboard.html        # Dashboard frontend
│   ├── virtual_servo_bridge.py       # Virtual servo serial bridge
│   ├── start_servo_bridge.bat        # Windows launcher
│   ├── start_servo_bridge.sh         # Linux/macOS launcher
│   ├── box2driver_client.py          # Python Client API
│   ├── lerobot_collect.py            # LeRobot dataset collection
│   ├── lerobot_deploy.py             # LeRobot model deployment
│   ├── joycon_ik_bridge.py           # JoyCon → IK → Follower
│   ├── compare_servo_protocol.py     # STS protocol debug tool
│   ├── gateway_recv.py               # Simple serial receiver (debug)
│   ├── example_collect.py            # Data collection example
│   ├── generate_manual.py            # Manual generator
│   └── check_env.py                  # Environment checker
├── examples/                         # Example scripts
│   └── keyboard_ik_control.py        # Keyboard IK / joint-space control
├── bin/                              # Pre-built firmware (v0.4.4)
│   ├── box2driver_v0.4.4_firmware.bin
│   ├── box2driver_v0.4.4_bootloader.bin
│   └── box2driver_v0.4.4_partitions.bin
├── dist_pkg/                         # Pre-built Python package
│   └── box2driver-0.4.4-py3-none-any.whl
├── flash_download_tool/              # Espressif Flash Download Tool
├── ESP32-CAM/                        # ESP32-CAM camera resources
├── requirements.txt                  # Basic dependencies
├── requirements-lerobot.txt          # LeRobot full dependencies
└── VERSION
```

## Changelog

| Version | Date | Notes |
|---------|------|-------|
| v0.4.4 | 2026-03-19 | Dual RGB LED system (mode+status), RMT channel conflict fix, mode-switch torque race fix, integrated STS TCP virtual serial |
| v0.4.3 | 2026-03-18 | Full STS protocol for virtual COM bridge, Gateway control stability, WS disconnect protection |
| v0.4.2 | 2026-03-18 | Virtual servo serial bridge (cross-platform), multi-device auto-detection, com0com/socat support |
| v0.4.1 | 2026-03-17 | 30Hz parameter tuning, quick-start section, keyboard IK control example |

## License

Apache 2.0 License

## Links

- [Box2Driver D1 Firmware Source](https://github.com/nicekwell/Box2Driver_D1_joycon)
- [LeRobot](https://github.com/huggingface/lerobot)
- [lerobot-kinematics](https://github.com/box-robotics/lerobot-kinematics)
