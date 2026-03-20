#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
Box2Driver Virtual Servo Bridge

Auto-detects ESP32 devices via Gateway WebSocket and creates virtual serial ports,
letting any STS/SCS servo software (FD, scservo_sdk, LeRobot) transparently control
remote servos over ESP-NOW.

Cross-platform:
  Windows  - com0com virtual serial port pairs
  Linux    - socat PTY pairs -> /dev/ttyACM* (sudo) or /tmp/ttyACM* (fallback)
  macOS    - socat PTY pairs

Usage:
  python virtual_servo_bridge.py                          # Full auto (recommended)
  python virtual_servo_bridge.py --ws ws://host:8765      # Custom gateway
  python virtual_servo_bridge.py --ports COM50,COM52      # Manual COM mode
  python virtual_servo_bridge.py --tcp-base 6570          # Manual TCP mode
  python virtual_servo_bridge.py -p COM50 --real COM23    # Direct real servo

Dependencies:
  pip install pyserial websockets
"""

import argparse
import asyncio
import atexit
import json
import os
import platform
import re
import socket
import struct
import subprocess
import shutil
import sys
import threading
import time
from collections import defaultdict

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Error: pip install pyserial")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("Error: pip install websockets")
    sys.exit(1)


# ============================================
# === STS/SCS Protocol Constants
# ============================================

HEADER = b'\xff\xff'

INST_PING       = 0x01
INST_READ       = 0x02
INST_WRITE      = 0x03
INST_REG_WRITE  = 0x04
INST_ACTION     = 0x05
INST_SYNC_READ  = 0x82
INST_SYNC_WRITE = 0x83

ADDR_TORQUE_ENABLE      = 40
ADDR_GOAL_POSITION_L    = 42
ADDR_GOAL_POSITION_H    = 43
ADDR_PRESENT_POSITION_L = 56
ADDR_PRESENT_POSITION_H = 57
ADDR_PRESENT_SPEED_L    = 58
ADDR_PRESENT_SPEED_H    = 59
ADDR_PRESENT_LOAD_L     = 60
ADDR_PRESENT_LOAD_H     = 61
ADDR_PRESENT_VOLTAGE    = 62
ADDR_PRESENT_TEMPERATURE = 63
ADDR_MOVING             = 66
ADDR_PRESENT_CURRENT_L  = 69
ADDR_PRESENT_CURRENT_H  = 70


def checksum(data: bytes) -> int:
    return (~sum(data)) & 0xFF


def build_status_packet(servo_id: int, error: int, data: bytes = b'') -> bytes:
    length = len(data) + 2
    pkt = bytes([servo_id, length, error]) + data
    cs = checksum(pkt)
    return HEADER + pkt + bytes([cs])


# ============================================
# === Virtual Servo Memory
# ============================================

class VirtualServoMemory:
    """Per-device servo memory table, fed by Gateway WebSocket feedback."""

    def __init__(self):
        self.lock = threading.Lock()
        self._mem = defaultdict(lambda: defaultdict(int))
        self.known_ids = set()
        self._pending_goals = {}
        self._reg_goals = {}

    _ST3215_EPROM_DEFAULTS = [
        0x03, 0x0A, 0x00,       # [0-2]  fw 3.10
        0x09, 0x03,             # [3-4]  model 777 (ST3215)
        0x00,                   # [5]    ID
        0x00,                   # [6]    baud (1Mbps)
        0x00,                   # [7]    return_delay
        0x01,                   # [8]    response_level
        0x00, 0x00,             # [9-10]  min_angle
        0xFF, 0x0F,             # [11-12] max_angle 4095
        0x46,                   # [13]   max_temp 70
        0x8C,                   # [14]   max_volt 140
        0x28,                   # [15]   min_volt 40
        0xE8, 0x03,             # [16-17] max_torque 1000
        0x0C,                   # [18]   phase
        0x2C,                   # [19]   unloading_cond
        0x2F,                   # [20]   led_alarm
        0x20,                   # [21]   P
        0x20,                   # [22]   D
        0x00,                   # [23]   I
        0x10, 0x00,             # [24-25] min_startup
        0x01,                   # [26]   CW_dead
        0x01,                   # [27]   CCW_dead
        0x36, 0x01,             # [28-29] prot_current
        0x01,                   # [30]   angular_res
        0x00, 0x00,             # [31-32] offset
        0x00,                   # [33]   mode
        0x14,                   # [34]   prot_torque
        0xC8,                   # [35]   prot_time
        0x50,                   # [36]   overload_torque
        0x0A,                   # [37]   speed_P
        0xC8,                   # [38]   overcur_time
        0xC8,                   # [39]   vel_I
        0x01,                   # [40]   torque_enable
        0x00,                   # [41]   acceleration
        0x00, 0x00,             # [42-43] goal_pos
        0x00, 0x00,             # [44-45] goal_time
        0x00, 0x00,             # [46-47] goal_speed
        0xE8, 0x03,             # [48-49] torque_limit 1000
        0x00, 0x00, 0x00, 0x00, 0x00,  # [50-54]
    ]

    _ST3215_SRAM_DEFAULTS = [
        0x01,                   # [55]   lock
        0x00, 0x00,             # [56-57] present_pos
        0x00, 0x00,             # [58-59] present_speed
        0x00, 0x00,             # [60-61] present_load
        0x7D,                   # [62]   voltage 12.5V
        0x24,                   # [63]   temperature 36
        0x00,                   # [64]
        0x00,                   # [65]   status
        0x00,                   # [66]   moving
        0x00, 0x00,             # [67-68] goal_pos echo
        0x00, 0x00,             # [69-70] current
        0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00,
        0xFF, 0xFF,
        0x01, 0x14, 0x32,
        0x01, 0x41,
    ]

    def _init_servo_eprom(self, sid: int):
        mem = self._mem[sid]
        if mem.get(255) == 0xAA:
            return
        mem[255] = 0xAA
        for i, v in enumerate(self._ST3215_EPROM_DEFAULTS):
            mem[i] = v
        mem[5] = sid
        for i, v in enumerate(self._ST3215_SRAM_DEFAULTS):
            mem[55 + i] = v

    def update_from_feedback(self, servos: list):
        with self.lock:
            for s in servos:
                sid = s.get("id", 0)
                pos = s.get("pos", 0)
                spd = s.get("spd", 0)
                load = s.get("load", 0)
                if sid not in self.known_ids:
                    self.known_ids.add(sid)
                    self._init_servo_eprom(sid)
                mem = self._mem[sid]
                mem[ADDR_PRESENT_POSITION_L] = pos & 0xFF
                mem[ADDR_PRESENT_POSITION_H] = (pos >> 8) & 0xFF
                goal = mem[ADDR_GOAL_POSITION_L] | (mem[ADDR_GOAL_POSITION_H] << 8)
                if goal == 0:
                    mem[ADDR_GOAL_POSITION_L] = pos & 0xFF
                    mem[ADDR_GOAL_POSITION_H] = (pos >> 8) & 0xFF
                mem[ADDR_PRESENT_SPEED_L] = spd & 0xFF
                mem[ADDR_PRESENT_SPEED_H] = (spd >> 8) & 0xFF
                mem[ADDR_PRESENT_LOAD_L] = load & 0xFF
                mem[ADDR_PRESENT_LOAD_H] = (load >> 8) & 0xFF
                mem[ADDR_PRESENT_VOLTAGE] = s.get("volt", 125)
                mem[ADDR_PRESENT_TEMPERATURE] = s.get("temp", 36)
                goal = mem[ADDR_GOAL_POSITION_L] | (mem[ADDR_GOAL_POSITION_H] << 8)
                mem[67] = goal & 0xFF
                mem[68] = (goal >> 8) & 0xFF
                mem[ADDR_MOVING] = 1 if abs(spd) > 0 else 0
                cur = abs(load) * 10 // 1000
                mem[ADDR_PRESENT_CURRENT_L] = cur & 0xFF
                mem[ADDR_PRESENT_CURRENT_H] = (cur >> 8) & 0xFF

    def read_bytes(self, servo_id: int, start_addr: int, length: int) -> bytes:
        with self.lock:
            mem = self._mem[servo_id]
            return bytes([mem.get(start_addr + i, 0) for i in range(length)])

    def write_bytes(self, servo_id: int, start_addr: int, data: bytes):
        with self.lock:
            mem = self._mem[servo_id]
            for i, b in enumerate(data):
                mem[start_addr + i] = b

    def has_servo(self, servo_id: int) -> bool:
        with self.lock:
            return servo_id in self.known_ids


# ============================================
# === Cross-platform Virtual Port Manager
# ============================================

class VirtualPortManager:
    """Auto-create virtual serial port pairs.

    Windows : com0com (pre-installed, pairs auto-detected)
    Linux   : socat PTY pairs -> /dev/ttyACM* (sudo) or /tmp/ttyACM* (fallback)
    macOS   : socat PTY pairs -> /tmp/vservo0, /tmp/vservo1, ...
    """

    def __init__(self):
        self._system = platform.system()   # Windows, Linux, Darwin
        self._socat_procs = []
        self._pairs = []                   # [(bridge, user), ...]
        self._used_bridges = set()
        self._pair_idx = 0
        atexit.register(self.cleanup)

    @property
    def driver_name(self) -> str:
        if self._system == 'Windows':
            return 'com0com' if self._find_setupc() else 'TCP-only'
        return 'socat' if shutil.which('socat') else 'TCP-only'

    def check(self) -> tuple:
        """Returns (can_create_serial_ports: bool, status_message: str)"""
        if self._system == 'Windows':
            pairs = self._list_com0com_pairs()
            if pairs:
                pair_strs = [f'{a}<->{b}' for a, b in pairs]
                return True, f'com0com: {", ".join(pair_strs)}'
            if self._find_setupc():
                return False, 'com0com installed but no pairs. Run as admin: setupc install PortName=COM50 PortName=COM51'
            return False, 'com0com not installed (https://sourceforge.net/projects/com0com/)'
        else:
            if shutil.which('socat'):
                return True, 'socat ready'
            pkg = 'brew install socat' if self._system == 'Darwin' else 'sudo apt install -y socat'
            return False, f'socat not found. Install: {pkg}'

    def create_pair(self) -> tuple:
        """Allocate/create one virtual port pair.
        Returns (bridge_path, user_path) or (None, None).
        """
        if self._system == 'Windows':
            return self._alloc_com0com()
        return self._create_socat()

    def release_pair(self, bridge_path: str):
        self._used_bridges.discard(bridge_path)

    # ---- Windows: com0com ----

    def _find_setupc(self):
        for p in [r'C:\Program Files (x86)\com0com\setupc.exe',
                   r'C:\Program Files\com0com\setupc.exe']:
            if os.path.exists(p):
                return p
        return shutil.which('setupc')

    def _list_com0com_pairs(self) -> list:
        """Detect com0com pairs via Windows registry (no admin needed)."""
        if self._system != 'Windows':
            return []
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r'HARDWARE\DEVICEMAP\SERIALCOMM')
            # Collect all com0com entries: \Device\com0comXY = COMnn
            # Pairing: com0com10 <-> com0com20, com0com11 <-> com0com21, etc.
            entries = {}  # device_name -> COM port
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    if 'com0com' in name.lower():
                        entries[name] = value
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)

            # Group by pair index: com0comN0 and com0comN1 are a pair
            # Convention: \Device\com0com10 (CNCA) <-> \Device\com0com20 (CNCB)
            # Or: entries sorted, consecutive pairs
            ports = sorted(entries.values(),
                           key=lambda x: int(re.search(r'\d+', x).group()))
            return [(ports[i], ports[i + 1]) for i in range(0, len(ports) - 1, 2)]
        except Exception:
            return []

    def _alloc_com0com(self):
        for bridge, user in self._list_com0com_pairs():
            if bridge not in self._used_bridges:
                self._used_bridges.add(bridge)
                self._pairs.append((bridge, user))
                return bridge, user
        return None, None

    # ---- Linux / macOS: socat ----

    def _find_free_ttyACM(self):
        """Find the next available /dev/ttyACM* number."""
        used = set()
        for i in range(100):
            if os.path.exists(f'/dev/ttyACM{i}'):
                used.add(i)
        for i in range(100):
            if i not in used:
                return i
        return None

    def _create_socat(self):
        idx = self._pair_idx
        self._pair_idx += 1
        bridge_link = f'/tmp/vservo{idx}_bridge'

        # Linux: create /dev/ttyACM* symlink for FD software compatibility
        # macOS: keep /tmp/vservo* (no /dev/ttyACM convention)
        socat_user_link = f'/tmp/vservo{idx}'  # socat always creates here first
        user_link = socat_user_link
        dev_ttyACM_link = None

        for p in [bridge_link, socat_user_link]:
            try:
                if os.path.exists(p) or os.path.islink(p):
                    os.remove(p)
            except OSError:
                pass

        proc = subprocess.Popen(
            ['socat', '-d', '-d',
             f'PTY,raw,echo=0,link={bridge_link}',
             f'PTY,raw,echo=0,link={socat_user_link}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._socat_procs.append(proc)
        for _ in range(30):
            if os.path.exists(bridge_link) and os.path.exists(socat_user_link):
                break
            time.sleep(0.1)
        else:
            proc.kill()
            return None, None

        # On Linux, create /dev/ttyACM* symlink pointing to the socat PTY
        if self._system == 'Linux':
            acm_idx = self._find_free_ttyACM()
            if acm_idx is not None:
                dev_ttyACM_link = f'/dev/ttyACM{acm_idx}'
                # Resolve the real PTY device path behind the socat symlink
                real_pty = os.path.realpath(socat_user_link)
                try:
                    subprocess.run(
                        ['sudo', 'ln', '-sf', real_pty, dev_ttyACM_link],
                        check=True, timeout=5)
                    subprocess.run(
                        ['sudo', 'chmod', '666', dev_ttyACM_link],
                        check=False, timeout=5)
                    user_link = dev_ttyACM_link
                    if not hasattr(self, '_dev_links'):
                        self._dev_links = []
                    self._dev_links.append(dev_ttyACM_link)
                    print(f"[VPort] Created {dev_ttyACM_link} -> {real_pty}")
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                    print(f"[VPort] Cannot create {dev_ttyACM_link} (sudo failed: {e})")
                    print(f"[VPort] Falling back to {socat_user_link}")
                    print(f"[VPort] Tip: run with sudo, or manually:")
                    print(f"[VPort]   sudo ln -sf {real_pty} {dev_ttyACM_link}")
                    dev_ttyACM_link = None
                    user_link = socat_user_link

        self._used_bridges.add(bridge_link)
        self._pairs.append((bridge_link, user_link))
        return bridge_link, user_link

    def cleanup(self):
        for proc in self._socat_procs:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # Clean up /dev/ttyACM* symlinks (need sudo)
        for link in getattr(self, '_dev_links', []):
            try:
                subprocess.run(['sudo', 'rm', '-f', link],
                               check=False, timeout=5)
            except Exception:
                pass
        # Clean up /tmp socat links
        for bridge, user in self._pairs:
            for p in [bridge, user]:
                if p.startswith('/tmp/vservo') or p.startswith('/tmp/ttyACM'):
                    try:
                        os.remove(p)
                    except OSError:
                        pass


# ============================================
# === Device Slot & WebSocket Bridge
# ============================================

ROLE_NAMES = {0: "Follower", 1: "Leader", 2: "M-Leader", 3: "Gateway", 4: "JoyCon"}


class DeviceSlot:
    """One ESP32 device's virtual serial port slot."""

    def __init__(self, mac, dev_id, role, ws_bridge):
        self.mac = mac
        self.dev_id = dev_id
        self.role = role
        self.role_name = ROLE_NAMES.get(role, f"role={role}")
        self.memory = VirtualServoMemory()
        self.handler = STSProtocolHandler(self.memory, ws_bridge, target_mac=mac)
        self.last_seen = time.time()
        self.com_bridge = None
        self.com_user = None
        self.tcp_port = None
        self._serial = None
        self._thread = None
        self._running = False

    def start_com(self, bridge_port: str, baud: int, verbose: bool,
                  user_port: str = None) -> bool:
        self.com_bridge = bridge_port
        if user_port:
            self.com_user = user_port
        else:
            port_num = int(''.join(filter(str.isdigit, bridge_port)))
            self.com_user = f"COM{port_num + 1}"
        try:
            ser = serial.Serial()
            ser.port = bridge_port
            ser.baudrate = baud
            ser.timeout = 0
            ser.write_timeout = 0.1
            ser.open()
            self._serial = ser
        except serial.SerialException as e:
            print(f"[Error] Cannot open {bridge_port}: {e}")
            return False
        self._running = True
        self._thread = threading.Thread(
            target=serial_loop, args=(ser, self.handler, verbose),
            daemon=True, name=f"serial-{self.mac[-5:]}")
        self._thread.start()
        return True

    def start_tcp(self, tcp_port: int, verbose: bool) -> bool:
        self.tcp_port = tcp_port
        self._running = True
        self._thread = threading.Thread(
            target=tcp_server_loop, args=(tcp_port, self.handler, verbose),
            daemon=True, name=f"tcp-{self.mac[-5:]}")
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        label = self.com_user or f"tcp:{self.tcp_port}"
        print(f"[Port] {self.role_name}({self.mac[-5:]}) closed {label}")


class WsBridge:
    """WebSocket client + multi-device port manager."""

    DEVICE_TTL = 120.0

    def __init__(self, ws_url: str, com_ports: list = None, tcp_base: int = 0,
                 baud: int = 1000000, verbose: bool = False,
                 port_manager: VirtualPortManager = None,
                 interactive: bool = False):
        self.ws_url = ws_url
        self._com_pool = list(com_ports or [])
        self._tcp_base = tcp_base
        self._tcp_next = tcp_base
        self._baud = baud
        self._verbose = verbose
        self._port_manager = port_manager
        self._interactive = interactive
        self._ws = None
        self._loop = None
        self._thread = None
        self._connected = threading.Event()
        self._cmd_queue = []
        self._cmd_lock = threading.Lock()
        self._cmd_event = None  # asyncio.Event, set in _ws_loop
        self.slots = {}           # mac -> DeviceSlot
        self._slots_lock = threading.Lock()
        self._pending_devices = {}    # mac -> {dev_id, role, first_seen}
        self._pending_lock = threading.Lock()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._connected.wait(timeout=8):
            print("[WS] Warning: not connected after 8s")

    def _run(self):
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._ws_loop())

    def _ensure_slot(self, mac: str, dev_id, role: int) -> DeviceSlot:
        with self._slots_lock:
            if mac in self.slots:
                slot = self.slots[mac]
                slot.last_seen = time.time()
                return slot

            # Interactive mode: queue device, don't auto-create port
            if self._interactive:
                with self._pending_lock:
                    if mac not in self._pending_devices:
                        rn = ROLE_NAMES.get(role, f"role={role}")
                        self._pending_devices[mac] = {
                            'dev_id': dev_id, 'role': role,
                            'first_seen': time.time()
                        }
                        print(f"\n[NEW] {rn}({mac[-5:]}) discovered  (MAC: {mac})")
                # Still need a slot to receive data, but without port
                slot = DeviceSlot(mac, dev_id, role, self)
                self.slots[mac] = slot
                slot.last_seen = time.time()
                return slot

            slot = DeviceSlot(mac, dev_id, role, self)
            self.slots[mac] = slot
            rn = ROLE_NAMES.get(role, f"role={role}")

            assigned = False

            # 1. Try explicit COM pool
            if self._com_pool:
                bridge_port = self._com_pool.pop(0)
                assigned = slot.start_com(bridge_port, self._baud, self._verbose)
                if assigned:
                    print(f"[Port] {rn}({mac[-5:]}) -> {slot.com_user}  (servo {self._fmt_ids(slot)})")
                else:
                    self._com_pool.append(bridge_port)

            # 2. Try port manager (auto-create)
            if not assigned and self._port_manager:
                bridge, user = self._port_manager.create_pair()
                if bridge:
                    assigned = slot.start_com(bridge, self._baud, self._verbose,
                                              user_port=user)
                    if assigned:
                        print(f"[Port] {rn}({mac[-5:]}) -> {slot.com_user}  (servo {self._fmt_ids(slot)})")

            # 3. Fallback to TCP
            if not assigned and self._tcp_base > 0:
                slot.start_tcp(self._tcp_next, self._verbose)
                print(f"[Port] {rn}({mac[-5:]}) -> socket://localhost:{self._tcp_next}  (servo {self._fmt_ids(slot)})")
                self._tcp_next += 1
                assigned = True

            if not assigned:
                print(f"[Warning] {rn}({mac[-5:]}) no port available!")
            return slot

    def activate_device(self, mac: str) -> bool:
        """Manually activate a pending device: create virtual port for it."""
        with self._slots_lock:
            slot = self.slots.get(mac)
            if not slot:
                return False
            if slot.com_user or slot.tcp_port:
                return True  # already active

            rn = slot.role_name
            assigned = False

            if self._port_manager:
                bridge, user = self._port_manager.create_pair()
                if bridge:
                    assigned = slot.start_com(bridge, self._baud, self._verbose,
                                              user_port=user)
                    if assigned:
                        print(f"[Port] {rn}({mac[-5:]}) -> {slot.com_user}  (servo {self._fmt_ids(slot)})")

            if not assigned and self._tcp_base > 0:
                slot.start_tcp(self._tcp_next, self._verbose)
                print(f"[Port] {rn}({mac[-5:]}) -> socket://localhost:{self._tcp_next}  (servo {self._fmt_ids(slot)})")
                self._tcp_next += 1
                assigned = True

            if not assigned:
                print(f"[Warning] {rn}({mac[-5:]}) no port available!")
            return assigned

    @staticmethod
    def _fmt_ids(slot):
        ids = sorted(slot.memory.known_ids)
        return ','.join(str(i) for i in ids) if ids else '...'

    def _purge_stale(self):
        now = time.time()
        with self._slots_lock:
            stale = [mac for mac, slot in self.slots.items()
                     if now - slot.last_seen > self.DEVICE_TTL]
            for mac in stale:
                slot = self.slots.pop(mac)
                age = now - slot.last_seen
                print(f"[Purge] {slot.role_name}({mac[-5:]}) offline {age:.0f}s")
                # 释放 GW_CONTROL，让 Follower 恢复 IDLE
                if slot.handler._control_requested:
                    self.release_control(mac)
                slot.stop()
                if slot.com_bridge:
                    if self._port_manager:
                        self._port_manager.release_pair(slot.com_bridge)
                    else:
                        self._com_pool.append(slot.com_bridge)

    async def _ws_loop(self):
        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    self._cmd_event = asyncio.Event()
                    self._connected.set()
                    print(f"[WS] Connected: {self.ws_url}")
                    sender = asyncio.create_task(self._cmd_sender(ws))
                    purger = asyncio.create_task(self._purge_loop())
                    async for msg in ws:
                        try:
                            data = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        if data.get("type") == "snapshot":
                            # Snapshot is historical cache — ignore it.
                            # Only create ports from live feedback data below.
                            pass
                        elif "dev" in data and "servos" in data:
                            mac = data.get("mac")
                            if mac:
                                role = data.get("role", -1)
                                slot = self._ensure_slot(mac, data["dev"], role)
                                slot.memory.update_from_feedback(data["servos"])
                    sender.cancel()
                    purger.cancel()
            except Exception as e:
                self._ws = None
                self._connected.clear()
                print(f"[WS] Disconnected: {e}, reconnecting in 2s...")
                await asyncio.sleep(2)

    async def _cmd_sender(self, ws):
        _send_count = 0
        while True:
            try:
                await asyncio.wait_for(self._cmd_event.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass
            self._cmd_event.clear()
            with self._cmd_lock:
                cmds = list(self._cmd_queue)
                self._cmd_queue.clear()
            if cmds:
                t0 = time.perf_counter()
            for cmd in cmds:
                try:
                    await ws.send(json.dumps(cmd, ensure_ascii=False))
                except Exception:
                    pass
            if cmds:
                dt = (time.perf_counter() - t0) * 1000
                _send_count += 1
                if _send_count % 30 == 1 or dt > 10:
                    types = [c.get("type", "?") for c in cmds]
                    print(f"[T-WS_SEND] {dt:.1f}ms  n={len(cmds)} types={types}")

    async def _purge_loop(self):
        while True:
            await asyncio.sleep(5)
            self._purge_stale()

    def _enqueue_cmd(self, cmd):
        with self._cmd_lock:
            self._cmd_queue.append(cmd)
        if self._cmd_event and self._loop:
            self._loop.call_soon_threadsafe(self._cmd_event.set)

    def send_control(self, servos: list, mac: str = None):
        cmd = {"type": "control", "servos": servos}
        if mac:
            cmd["mac"] = mac
        self._enqueue_cmd(cmd)

    def send_torque(self, servo_id: int, enable: bool, mac: str = None):
        cmd = {"type": "torque", "id": servo_id, "enable": 1 if enable else 0}
        if mac:
            cmd["mac"] = mac
        self._enqueue_cmd(cmd)

    def send_torque_all(self, enable: bool):
        cmd = {"type": "torque_all", "enable": 1 if enable else 0}
        self._enqueue_cmd(cmd)

    def request_control(self, mac: str):
        """Request GW_CONTROL for a Follower MAC (activates keep-alive on dashboard)."""
        cmd = {"type": "request_control", "mac": mac}
        self._enqueue_cmd(cmd)
        print(f"[Bridge] request_control -> {mac[-5:]}")

    def release_control(self, mac: str = None):
        """Release GW_CONTROL. If mac=None, release all."""
        cmd = {"type": "ctrl_release"}
        if mac:
            cmd["mac"] = mac
        self._enqueue_cmd(cmd)
        print(f"[Bridge] ctrl_release -> {mac[-5:] if mac else 'ALL'}")


# ============================================
# === Real Servo Bridge (direct USB2TTL)
# ============================================

class RealServoBridge:
    """Connect to real servo via USB2TTL, poll positions, forward control."""

    def __init__(self, port: str, baud: int, servo_ids: list, memory: VirtualServoMemory):
        self.port = port
        self.baud = baud
        self.servo_ids = servo_ids
        self.memory = memory
        self._ser = None
        self._running = False
        self._lock = threading.Lock()

    def _checksum(self, data):
        return (~sum(data)) & 0xFF

    def _build_packet(self, servo_id, instruction, params=b''):
        length = len(params) + 2
        pkt = bytes([0xFF, 0xFF, servo_id, length, instruction]) + params
        return pkt + bytes([self._checksum(pkt[2:])])

    def _send_recv(self, pkt, timeout=0.01):
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(pkt)
            self._ser.flush()
            time.sleep(timeout)
            return self._ser.read(self._ser.in_waiting or 64)

    def start(self):
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0.05)
        except serial.SerialException as e:
            print(f"[Real] Cannot open {self.port}: {e}")
            return False
        for sid in self.servo_ids:
            pkt = self._build_packet(sid, 0x02, bytes([0, 55]))
            resp = self._send_recv(pkt, timeout=0.02)
            if resp and len(resp) >= 6 + 55:
                data = resp[5:-1]
                self.memory.write_bytes(sid, 0, data)
                self.memory.known_ids.add(sid)
                self.memory._init_servo_eprom(sid)
                for i, b in enumerate(data):
                    self.memory._mem[sid][i] = b
                print(f"[Real] ID={sid} EPROM read ({len(data)}B)")
            else:
                print(f"[Real] ID={sid} no response, skipping")
        for sid in list(self.memory.known_ids):
            pkt = self._build_packet(sid, 0x02, bytes([55, 31]))
            resp = self._send_recv(pkt, timeout=0.02)
            if resp and len(resp) >= 6 + 2:
                data = resp[5:-1]
                for i, b in enumerate(data):
                    self.memory._mem[sid][55 + i] = b
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print(f"[Real] Polling started: {self.port} IDs={list(self.memory.known_ids)}")
        return True

    def _poll_loop(self):
        while self._running:
            for sid in list(self.memory.known_ids):
                pkt = self._build_packet(sid, 0x02, bytes([56, 8]))
                resp = self._send_recv(pkt, timeout=0.005)
                if resp and len(resp) >= 6 + 8:
                    data = resp[5:-1]
                    mem = self.memory._mem[sid]
                    for i in range(min(8, len(data))):
                        mem[56 + i] = data[i]
                    mem[ADDR_MOVING] = 1 if (data[2] | (data[3] << 8)) > 0 else 0
            time.sleep(0.025)

    def write_goal(self, servo_id, pos):
        data = bytes([ADDR_GOAL_POSITION_L, pos & 0xFF, (pos >> 8) & 0xFF])
        pkt = self._build_packet(servo_id, 0x03, data)
        self._send_recv(pkt, timeout=0.003)

    def write_torque(self, servo_id, enable):
        pkt = self._build_packet(servo_id, 0x03,
                                 bytes([ADDR_TORQUE_ENABLE, 1 if enable else 0]))
        self._send_recv(pkt, timeout=0.003)

    def write_raw(self, servo_id, start_addr, data):
        pkt = self._build_packet(servo_id, 0x03, bytes([start_addr]) + data)
        self._send_recv(pkt, timeout=0.003)

    def stop(self):
        self._running = False
        if self._ser and self._ser.is_open:
            self._ser.close()


# ============================================
# === STS Protocol Handler
# ============================================

class STSProtocolHandler:
    """Parse STS binary protocol from serial, generate responses."""

    def __init__(self, memory: VirtualServoMemory, ws_bridge=None,
                 target_mac: str = None):
        self.memory = memory
        self.ws = ws_bridge
        self.target_mac = target_mac
        self._real_servo = None
        self._control_requested = False  # 是否已自动请求 GW_CONTROL
        self._stats = {"ping": 0, "read": 0, "write": 0,
                       "sync_write": 0, "sync_read": 0, "unknown": 0}
        self._timing_count = 0  # 计时打印计数

    def process_packet(self, servo_id, instruction, params):
        # 首次收到 STS 流量时，自动激活 GW_CONTROL（让 Follower 进入被控状态）
        if not self._control_requested and self.ws and self.target_mac:
            self._control_requested = True
            self.ws.request_control(self.target_mac)

        if instruction == INST_PING:
            return self._handle_ping(servo_id)
        elif instruction == INST_READ:
            return self._handle_read(servo_id, params)
        elif instruction == INST_WRITE:
            return self._handle_write(servo_id, params)
        elif instruction == INST_REG_WRITE:
            return self._handle_reg_write(servo_id, params)
        elif instruction == INST_ACTION:
            return self._handle_action(servo_id)
        elif instruction == INST_SYNC_WRITE:
            return self._handle_sync_write(params)
        elif instruction == INST_SYNC_READ:
            return self._handle_sync_read(params)
        else:
            self._stats["unknown"] += 1
            return b''

    def _handle_ping(self, servo_id):
        self._stats["ping"] += 1
        if servo_id == 0xFE:
            return b''
        if not self.memory.has_servo(servo_id):
            return b''
        return build_status_packet(servo_id, 0)

    def _handle_read(self, servo_id, params):
        self._stats["read"] += 1
        if len(params) < 2:
            return build_status_packet(servo_id, 0x03)
        if servo_id != 0xFE and not self.memory.has_servo(servo_id):
            return b''
        start_addr = params[0]
        length = params[1]
        data = self.memory.read_bytes(servo_id, start_addr, length)
        return build_status_packet(servo_id, 0, data)

    def _handle_write(self, servo_id, params):
        self._stats["write"] += 1
        if len(params) < 2:
            return build_status_packet(servo_id, 0x03)
        start_addr = params[0]
        data = params[1:]
        self.memory.write_bytes(servo_id, start_addr, data)
        self._translate_write(servo_id, start_addr, data)
        if servo_id == 0xFE:
            return b''
        return build_status_packet(servo_id, 0)

    def _handle_reg_write(self, servo_id, params):
        self._stats["write"] += 1
        if len(params) < 2:
            return build_status_packet(servo_id, 0x03)
        start_addr = params[0]
        data = params[1:]
        self.memory.write_bytes(servo_id, start_addr, data)
        if start_addr <= ADDR_GOAL_POSITION_L and start_addr + len(data) > ADDR_GOAL_POSITION_L:
            offset = ADDR_GOAL_POSITION_L - start_addr
            if offset + 1 < len(data):
                pos = data[offset] | (data[offset + 1] << 8)
                self.memory._reg_goals[servo_id] = pos
        if servo_id == 0xFE:
            return b''
        return build_status_packet(servo_id, 0)

    def _handle_action(self, servo_id):
        goals = dict(self.memory._reg_goals)
        self.memory._reg_goals.clear()
        if goals:
            if self._real_servo:
                for sid, pos in goals.items():
                    self._real_servo.write_goal(sid, pos)
            elif self.ws:
                servos = [{"id": sid, "pos": pos} for sid, pos in goals.items()]
                self.ws.send_control(servos, mac=self.target_mac)
        return b''

    def _handle_sync_write(self, params):
        t0 = time.perf_counter()
        self._stats["sync_write"] += 1
        if len(params) < 2:
            return b''
        start_addr = params[0]
        data_len = params[1]
        payload = params[2:]
        chunk_size = 1 + data_len
        servos_to_control = []
        i = 0
        while i + chunk_size <= len(payload):
            sid = payload[i]
            sdata = payload[i + 1:i + chunk_size]
            i += chunk_size
            self.memory.write_bytes(sid, start_addr, sdata)
            if self._real_servo:
                self._real_servo.write_raw(sid, start_addr, sdata)
            if not self._real_servo and start_addr <= ADDR_GOAL_POSITION_L:
                pos_offset = ADDR_GOAL_POSITION_L - start_addr
                if pos_offset + 1 < len(sdata):
                    pos = sdata[pos_offset] | (sdata[pos_offset + 1] << 8)
                    servos_to_control.append({"id": sid, "pos": pos})
        if servos_to_control and self.ws:
            self.ws.send_control(servos_to_control, mac=self.target_mac)
        dt = (time.perf_counter() - t0) * 1000
        self._timing_count += 1
        if self._timing_count % 30 == 1 or dt > 10:
            pos_str = ','.join(f"{s['id']}:{s['pos']}" for s in servos_to_control[:3])
            qlen = len(self.ws._cmd_queue) if self.ws else 0
            print(f"[T-SYNCW] {dt:.1f}ms  addr={start_addr} servos={len(servos_to_control)} "
                  f"q={qlen} pos=[{pos_str}]")
        return b''

    def _handle_sync_read(self, params):
        t0 = time.perf_counter()
        self._stats["sync_read"] += 1
        if len(params) < 2:
            return b''
        start_addr = params[0]
        read_len = params[1]
        ids = params[2:]
        response = b''
        for sid in ids:
            data = self.memory.read_bytes(sid, start_addr, read_len)
            response += build_status_packet(sid, 0, data)
        dt = (time.perf_counter() - t0) * 1000
        if self._timing_count % 30 == 1 or dt > 5:
            print(f"[T-SYNCR] {dt:.1f}ms  addr={start_addr} len={read_len} ids={list(ids)} "
                  f"resp={len(response)}B")
        return response

    def _translate_write(self, servo_id, start_addr, data):
        if self._real_servo and servo_id != 0xFE:
            self._real_servo.write_raw(servo_id, start_addr, data)
            return
        if start_addr <= ADDR_GOAL_POSITION_L and start_addr + len(data) > ADDR_GOAL_POSITION_L:
            offset = ADDR_GOAL_POSITION_L - start_addr
            if offset + 1 < len(data):
                pos = data[offset] | (data[offset + 1] << 8)
                if servo_id != 0xFE and self.ws:
                    self.ws.send_control([{"id": servo_id, "pos": pos}],
                                         mac=self.target_mac)
        if start_addr <= ADDR_TORQUE_ENABLE and start_addr + len(data) > ADDR_TORQUE_ENABLE:
            offset = ADDR_TORQUE_ENABLE - start_addr
            enable = data[offset]
            if servo_id == 0xFE and self.ws:
                self.ws.send_torque_all(bool(enable))
            elif self.ws:
                self.ws.send_torque(servo_id, bool(enable), mac=self.target_mac)

    def print_stats(self):
        ids = sorted(self.memory.known_ids)
        print(f"  [{self.target_mac or 'N/A'}] ids={ids} "
              f"ping={self._stats['ping']} read={self._stats['read']} "
              f"write={self._stats['write']} sync_w={self._stats['sync_write']} "
              f"sync_r={self._stats['sync_read']}")


# ============================================
# === Serial / TCP Loop
# ============================================

def serial_loop(ser, handler, verbose=False):
    """Read STS frames from serial, respond."""
    buf = bytearray()
    pkt_count = 0
    last_stats = time.time()
    err_count = 0
    # --- 延迟检测 ---
    loop_count = 0
    idle_loops = 0  # 连续空读次数
    last_pkt_time = time.perf_counter()

    while True:
        try:
            if not ser.is_open:
                time.sleep(1)
                try:
                    ser.open()
                    err_count = 0
                except Exception:
                    time.sleep(2)
                    continue

            waiting = ser.in_waiting
            if waiting == 0:
                time.sleep(0.0001)
                idle_loops += 1
                err_count = 0
                continue

            t_read = time.perf_counter()
            gap_ms = (t_read - last_pkt_time) * 1000
            buf.extend(ser.read(waiting))
            err_count = 0

            pkts_this_batch = 0
            while len(buf) >= 6:
                idx = buf.find(b'\xff\xff')
                if idx < 0:
                    buf.clear()
                    break
                if idx > 0:
                    buf = buf[idx:]
                if len(buf) < 4:
                    break
                servo_id = buf[2]
                pkt_len = buf[3]
                total_len = 4 + pkt_len
                if len(buf) < total_len:
                    break
                frame = bytes(buf[:total_len])
                buf = buf[total_len:]
                cs_data = frame[2:-1]
                if checksum(cs_data) != frame[-1]:
                    if verbose:
                        print(f"[CRC err] {frame.hex()}")
                    continue
                instruction = frame[4]
                params = frame[5:-1]
                if verbose:
                    print(f"[RX] ID={servo_id} INST={instruction:#04x} p={params.hex()}")
                t_proc = time.perf_counter()
                response = handler.process_packet(servo_id, instruction, params)
                t_done = time.perf_counter()
                if response:
                    ser.write(response)
                    ser.flush()
                    t_sent = time.perf_counter()
                    if verbose:
                        print(f"[TX] {response.hex()}")
                    # 对 SyncRead/SyncWrite 打印耗时
                    proc_ms = (t_done - t_proc) * 1000
                    write_ms = (t_sent - t_done) * 1000
                    total_ms = (t_sent - t_read) * 1000
                    inst_name = {0x83: "SyncRead", 0x82: "SyncWrite"}.get(instruction, f"0x{instruction:02x}")
                    loop_count += 1
                    if loop_count % 30 == 1 or total_ms > 10:
                        print(f"[T-LOOP] {inst_name} gap={gap_ms:.1f}ms idle={idle_loops} "
                              f"proc={proc_ms:.1f}ms write={write_ms:.1f}ms total={total_ms:.1f}ms "
                              f"buf_in={waiting}B resp={len(response)}B")
                else:
                    proc_ms = (t_done - t_proc) * 1000
                    inst_name = {0x83: "SyncRead", 0x82: "SyncWrite"}.get(instruction, f"0x{instruction:02x}")
                    loop_count += 1
                    if loop_count % 30 == 1 or proc_ms > 10:
                        print(f"[T-LOOP] {inst_name} gap={gap_ms:.1f}ms idle={idle_loops} "
                              f"proc={proc_ms:.1f}ms (no resp) buf_in={waiting}B")
                pkt_count += 1
                pkts_this_batch += 1

            last_pkt_time = time.perf_counter()
            idle_loops = 0

            if len(buf) > 4096:
                buf = buf[-256:]

            now = time.time()
            if now - last_stats > 30:
                print(f"[Bridge] {pkt_count} packets processed")
                handler.print_stats()
                last_stats = now

        except (serial.SerialException, OSError) as e:
            err_count += 1
            if err_count <= 3:
                print(f"[Serial] {e}")
            if err_count > 10:
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1)
                try:
                    ser.open()
                    err_count = 0
                    buf.clear()
                except Exception:
                    time.sleep(2)
            else:
                time.sleep(0.1)
        except Exception as e:
            print(f"[Error] {e}")
            import traceback
            traceback.print_exc()
            time.sleep(0.1)


def tcp_handle_client(conn, addr, handler, verbose=False):
    buf = bytearray()
    pkt_count = 0
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf.extend(data)
            while len(buf) >= 6:
                idx = buf.find(b'\xff\xff')
                if idx < 0:
                    buf.clear()
                    break
                if idx > 0:
                    buf = buf[idx:]
                if len(buf) < 4:
                    break
                servo_id = buf[2]
                pkt_len = buf[3]
                total_len = 4 + pkt_len
                if len(buf) < total_len:
                    break
                frame = bytes(buf[:total_len])
                buf = buf[total_len:]
                if checksum(frame[2:-1]) != frame[-1]:
                    continue
                instruction = frame[4]
                params = frame[5:-1]
                response = handler.process_packet(servo_id, instruction, params)
                if response:
                    conn.sendall(response)
                pkt_count += 1
            if len(buf) > 4096:
                buf = buf[-256:]
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        conn.close()


def tcp_server_loop(tcp_port, handler, verbose=False):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", tcp_port))
    srv.listen(2)
    try:
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            t = threading.Thread(target=tcp_handle_client,
                                 args=(conn, addr, handler, verbose), daemon=True)
            t.start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


# ============================================
# === Auto Mode Helpers
# ============================================

def _test_ws(url, timeout=3) -> bool:
    """Quick test if WebSocket is responding."""
    async def _check():
        try:
            async with websockets.connect(url) as ws:
                await asyncio.wait_for(ws.recv(), timeout=timeout)
                return True
        except Exception:
            return False
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_check())
        loop.close()
        return result
    except Exception:
        return False


def _find_serial_ports() -> list:
    """Find USB serial ports. Returns [(device, description), ...]"""
    keywords = ['ch340', 'cp210', 'usb', 'uart', 'ftdi', 'serial', 'wch']
    result = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or '').lower()
        if any(k in desc for k in keywords):
            result.append((p.device, p.description))
    return result


def _start_dashboard(port: str) -> subprocess.Popen:
    """Start gateway_dashboard.py in background."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dashboard = os.path.join(script_dir, 'gateway_dashboard.py')
    if not os.path.exists(dashboard):
        print(f"[Error] gateway_dashboard.py not found in {script_dir}")
        return None
    kwargs = {}
    if platform.system() == 'Windows':
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    proc = subprocess.Popen(
        [sys.executable, '-u', dashboard, '-p', port, '--no-browser'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    return proc


def _print_table(bridge):
    """Print device -> port mapping table."""
    with bridge._slots_lock:
        if not bridge.slots:
            print("  (no devices yet)")
            return
        print()
        print("  Device      | MAC           | Servos    | Port")
        print("  ------------|---------------|-----------|--------------------")
        for mac, slot in bridge.slots.items():
            ids = sorted(slot.memory.known_ids)
            id_str = ','.join(str(i) for i in ids) if ids else '...'
            if slot.com_user:
                port_str = slot.com_user
            elif slot.tcp_port:
                port_str = f'socket://localhost:{slot.tcp_port}'
            else:
                port_str = '(pending)'
            print(f"  {slot.role_name:<11} | ...{mac[-8:]} | ID={id_str:<7} | {port_str}")
        print()


def _interactive_loop(bridge, pm, dashboard_proc):
    """Interactive mode: user picks which device to activate one by one."""
    print()
    print("=" * 62)
    print("  Interactive Mode")
    print("  Devices appear as they connect. Enter number to create port.")
    print("  Commands: l=list  a=activate all  q=quit")
    print("=" * 62)
    print()

    try:
        while True:
            # Build device list (all slots, both active and pending)
            with bridge._slots_lock:
                devices = [(mac, slot) for mac, slot in bridge.slots.items()]

            if not devices:
                try:
                    input("  No devices yet. Press Enter to refresh, Ctrl+C to quit...")
                except EOFError:
                    break
                continue

            # Show device list
            print()
            print("  #  | Status  | Device      | MAC           | Servos    | Port")
            print("  ---|---------|-------------|---------------|-----------|--------------------")
            for i, (mac, slot) in enumerate(devices):
                ids = sorted(slot.memory.known_ids)
                id_str = ','.join(str(i) for i in ids) if ids else '...'
                if slot.com_user:
                    status = "ACTIVE"
                    port_str = slot.com_user
                elif slot.tcp_port:
                    status = "ACTIVE"
                    port_str = f'socket://localhost:{slot.tcp_port}'
                else:
                    status = "PENDING"
                    port_str = '-'
                print(f"  {i+1:<2} | {status:<7} | {slot.role_name:<11} | ...{mac[-8:]} | ID={id_str:<7} | {port_str}")
            print()

            try:
                cmd = input("  Enter # to activate, l=list, a=all, q=quit: ").strip().lower()
            except EOFError:
                break

            if cmd == 'q':
                break
            elif cmd == 'l' or cmd == '':
                continue
            elif cmd == 'a':
                for mac, slot in devices:
                    if not slot.com_user and not slot.tcp_port:
                        bridge.activate_device(mac)
                _print_table(bridge)
                print("  All devices activated. Press Enter to refresh, q to quit.")
                continue
            else:
                # Parse number(s): support "1", "1,2", "1 2"
                nums = re.split(r'[,\s]+', cmd)
                for n in nums:
                    try:
                        idx = int(n) - 1
                        if 0 <= idx < len(devices):
                            mac, slot = devices[idx]
                            if slot.com_user or slot.tcp_port:
                                print(f"  [{idx+1}] Already active: {slot.com_user or slot.tcp_port}")
                            else:
                                bridge.activate_device(mac)
                        else:
                            print(f"  Invalid number: {n}")
                    except ValueError:
                        print(f"  Unknown command: {n}")

    except KeyboardInterrupt:
        print("\n[Exit] Cleaning up...")
    finally:
        pm.cleanup()
        if dashboard_proc:
            dashboard_proc.terminate()
            try:
                dashboard_proc.wait(timeout=3)
            except Exception:
                dashboard_proc.kill()


# ============================================
# === Auto Mode Entry
# ============================================

def auto_main(args):
    """Full auto: detect gateway, create ports, bridge."""
    pm = VirtualPortManager()
    can_com, driver_msg = pm.check()

    sys_name = platform.system()
    sys_ver = platform.release()
    print()
    print("=" * 62)
    print("  Box2Driver Virtual Servo Bridge")
    print(f"  {sys_name} {sys_ver} | {driver_msg}")
    print("=" * 62)
    print()

    ws_url = args.ws
    dashboard_proc = None

    # -- Check / start gateway --
    if not _test_ws(ws_url):
        print(f"[Gateway] {ws_url} not responding, searching serial ports...")
        ports = _find_serial_ports()
        if not ports:
            print("[Gateway] No USB serial ports found.")
            print("  Please start gateway_dashboard.py manually:")
            print(f"    python gateway_dashboard.py -p COMxx --no-browser")
            sys.exit(1)

        if len(ports) == 1:
            gw_port = ports[0][0]
            print(f"[Gateway] Found: {gw_port} ({ports[0][1]})")
        else:
            print("[Gateway] Multiple serial ports found:")
            for i, (dev, desc) in enumerate(ports):
                print(f"  [{i + 1}] {dev} - {desc}")
            try:
                choice = input("  Select Gateway port [1]: ").strip()
            except (EOFError, KeyboardInterrupt):
                sys.exit(0)
            idx = int(choice) - 1 if choice else 0
            gw_port = ports[idx][0]

        print(f"[Gateway] Starting dashboard on {gw_port}...")
        dashboard_proc = _start_dashboard(gw_port)
        if not dashboard_proc:
            sys.exit(1)

        for i in range(15):
            if _test_ws(ws_url, timeout=1):
                break
            time.sleep(1)
            if i % 3 == 2:
                print(f"[Gateway] Waiting... ({i + 1}s)")
        else:
            print("[Gateway] Dashboard failed to start. Check serial port.")
            dashboard_proc.terminate()
            sys.exit(1)

    print(f"[Gateway] OK ({ws_url})")

    # -- Start bridge --
    interactive = getattr(args, 'interactive', False)
    bridge = WsBridge(
        ws_url=ws_url,
        port_manager=pm if can_com else None,
        tcp_base=6570,   # TCP fallback always available
        baud=args.baud,
        verbose=args.verbose,
        interactive=interactive,
    )
    bridge.start()

    print("[Bridge] Waiting for devices...")
    time.sleep(4)

    if interactive:
        # Interactive mode: let user pick devices one by one
        _interactive_loop(bridge, pm, dashboard_proc)
        return

    _print_table(bridge)

    if sys_name == 'Windows':
        print("  FD: select the port above, baud=1000000, search servos")
    elif sys_name == 'Linux':
        print("  FD: select /dev/ttyACM* above, baud=1000000, search servos")
    else:
        print("  scservo_sdk: PortHandler('/tmp/vservo0')")
    print()
    print("  Press Ctrl+C to stop")
    print()

    try:
        while True:
            time.sleep(30)
            with bridge._slots_lock:
                for mac, slot in bridge.slots.items():
                    slot.handler.print_stats()
    except KeyboardInterrupt:
        print("\n[Exit] Cleaning up...")
    finally:
        pm.cleanup()
        if dashboard_proc:
            dashboard_proc.terminate()
            try:
                dashboard_proc.wait(timeout=3)
            except Exception:
                dashboard_proc.kill()


# ============================================
# === Main
# ============================================

def main():
    parser = argparse.ArgumentParser(
        description="Box2Driver Virtual Servo Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Auto mode (default, no args):
  Detects ESP32 devices, creates virtual serial ports automatically.
  Windows: com0com | Linux/macOS: socat | Fallback: TCP

  python virtual_servo_bridge.py                         # Full auto
  python virtual_servo_bridge.py --ws ws://host:8765     # Custom gateway
  python virtual_servo_bridge.py -i                      # Interactive: pick devices one by one

Manual mode:
  python virtual_servo_bridge.py --ports COM50,COM52     # Explicit COM
  python virtual_servo_bridge.py --tcp-base 6570         # Explicit TCP
  python virtual_servo_bridge.py -p COM50 --real COM23   # Direct real servo
        """,
    )
    # Multi-device
    parser.add_argument("--ports", type=str, default=None,
                        help="com0com bridge-side ports (e.g. --ports COM50,COM52)")
    parser.add_argument("--tcp-base", type=int, default=0,
                        help="TCP base port, auto-increment per device")
    # Single-device compat
    parser.add_argument("--tcp", action="store_true", help="[legacy] TCP single port")
    parser.add_argument("--tcp-port", type=int, default=6555, help="[legacy] TCP port")
    parser.add_argument("-p", "--port", help="[legacy] Single COM port")
    # Common
    parser.add_argument("-b", "--baud", type=int, default=1000000, help="Baud rate")
    parser.add_argument("--ws", default="ws://localhost:8765", help="Gateway WebSocket URL")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Interactive mode: discover devices first, pick which ones to create /dev/ttyACM* for")
    parser.add_argument("--mock", type=str, default=None, help="Mock servo IDs")
    parser.add_argument("--real", type=str, default=None, help="Real servo serial port")
    parser.add_argument("--real-ids", type=str, default="1", help="Real servo IDs")
    args = parser.parse_args()

    # --- Route: auto mode if no explicit port config ---
    has_explicit = (args.ports or args.tcp_base or args.port or args.tcp
                    or args.mock or args.real)
    if not has_explicit:
        auto_main(args)
        return

    # --- Manual multi-device mode ---
    if args.ports or args.tcp_base:
        com_ports = [p.strip() for p in args.ports.split(",")] if args.ports else []
        print()
        print("=" * 60)
        print("  Virtual Servo Bridge - Manual Multi-device")
        print(f"  WebSocket: {args.ws}")
        if com_ports:
            pairs = [f"{p}<->COM{int(''.join(filter(str.isdigit, p))) + 1}"
                     for p in com_ports]
            print(f"  COM pool: {', '.join(pairs)}")
        if args.tcp_base:
            print(f"  TCP base: {args.tcp_base}+")
        print("=" * 60)
        print()
        bridge = WsBridge(
            ws_url=args.ws, com_ports=com_ports, tcp_base=args.tcp_base,
            baud=args.baud, verbose=args.verbose)
        bridge.start()
        try:
            while True:
                time.sleep(30)
                with bridge._slots_lock:
                    for mac, slot in bridge.slots.items():
                        slot.handler.print_stats()
        except KeyboardInterrupt:
            print("\n[Exit]")
        return

    # --- Legacy single-device mode ---
    if not args.tcp and not args.port:
        print("Error: specify --ports/--tcp-base or -p/--tcp, or use auto mode (no args)")
        parser.print_help()
        sys.exit(1)

    memory = VirtualServoMemory()

    if args.real:
        real_ids = [int(x.strip()) for x in args.real_ids.split(",")]
        real_servo = RealServoBridge(args.real, args.baud, real_ids, memory)
        if not real_servo.start():
            sys.exit(1)
        ws_bridge = WsBridge(args.ws)
        handler = STSProtocolHandler(memory, ws_bridge)
        handler._real_servo = real_servo
    elif args.mock:
        mock_ids = [int(x.strip()) for x in args.mock.split(",")]
        for sid in mock_ids:
            memory.update_from_feedback([{"id": sid, "pos": 2048, "spd": 0, "load": 0}])
        print(f"[Mock] Registered: {memory.known_ids}")
        ws_bridge = WsBridge(args.ws)
        handler = STSProtocolHandler(memory, ws_bridge)
        real_servo = None
    else:
        ws_bridge = WsBridge(args.ws,
                             com_ports=[args.port] if not args.tcp else [],
                             tcp_base=args.tcp_port if args.tcp else 0,
                             baud=args.baud, verbose=args.verbose)
        ws_bridge.start()
        try:
            while True:
                time.sleep(10)
                with ws_bridge._slots_lock:
                    for mac, slot in ws_bridge.slots.items():
                        slot.handler.print_stats()
        except KeyboardInterrupt:
            pass
        return

    if args.tcp:
        tcp_server_loop(args.tcp_port, handler, verbose=args.verbose)
    else:
        ser = serial.Serial()
        ser.port = args.port
        ser.baudrate = args.baud
        ser.timeout = 0
        ser.write_timeout = 0.1
        try:
            ser.open()
        except serial.SerialException as e:
            print(f"[Error] Cannot open {args.port}: {e}")
            sys.exit(1)
        try:
            serial_loop(ser, handler, verbose=args.verbose)
        except KeyboardInterrupt:
            handler.print_stats()
        finally:
            ser.close()
            if args.real:
                real_servo.stop()


if __name__ == "__main__":
    main()
