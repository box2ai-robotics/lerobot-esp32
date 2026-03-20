#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
One-click: Gateway + COM Bridge
1) Start gateway_dashboard.py on COM36
2) Wait for devices and STS TCP ports
3) Start COM<->TCP bridges for each device
4) Run PING verification test

FD software connects to: COM21 (Leader) / COM23 (Follower)
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GATEWAY_PORT = "COM36"
WS_PORT = 8765
STS_BASE_PORT = 6560
BAUD = 1000000

# com0com port mapping: bridge opens COMx, FD opens COMx+1
COM_TCP_MAP = {
    # tcp_port -> (bridge_com, fd_com)
}


def wait_tcp(port, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            time.sleep(1)
    return False


def discover_devices():
    """Query WebSocket for online devices"""
    try:
        import asyncio
        import websockets

        async def _query():
            async with websockets.connect(f"ws://localhost:{WS_PORT}") as ws:
                for _ in range(10):
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=3)
                        data = json.loads(msg)
                        if data.get("type") == "snapshot":
                            return data.get("data", {}).get("devices", {})
                        if "dev" in data and "servos" in data:
                            continue
                    except asyncio.TimeoutError:
                        continue
            return {}

        return asyncio.run(_query())
    except Exception as e:
        print(f"  [!] WebSocket error: {e}")
        return {}


def find_sts_ports():
    """Find listening STS TCP ports (6560-6569)"""
    ports = []
    for p in range(STS_BASE_PORT, STS_BASE_PORT + 10):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", p))
            s.close()
            ports.append(p)
        except OSError:
            continue
    return ports


def test_ping(com_port, servo_ids):
    """Send PING to each servo ID via COM port"""
    import serial
    try:
        s = serial.Serial(com_port, BAUD, timeout=0.5)
    except Exception as e:
        print(f"  [!] Cannot open {com_port}: {e}")
        return []

    found = []
    for sid in servo_ids:
        pkt = bytes([0xFF, 0xFF, sid, 0x02, 0x01, (~(sid + 0x02 + 0x01)) & 0xFF])
        s.write(pkt)
        s.flush()
        time.sleep(0.05)
        resp = s.read(s.in_waiting or 10)
        if resp and len(resp) >= 6 and resp[0] == 0xFF and resp[1] == 0xFF:
            found.append(sid)
    s.close()
    return found


def main():
    procs = []

    print("\n" + "=" * 55)
    print("  Box2Driver Gateway + COM Bridge Starter")
    print("=" * 55)

    # Step 1: Start Gateway
    print(f"\n[1/4] Starting gateway on {GATEWAY_PORT}...")
    gw_proc = subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, "gateway_dashboard.py"),
         "-p", GATEWAY_PORT, "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    procs.append(gw_proc)

    print("  Waiting for WebSocket...", end="", flush=True)
    if not wait_tcp(WS_PORT, timeout=15):
        print(" FAILED")
        gw_proc.terminate()
        sys.exit(1)
    print(" OK")

    # Step 2: Wait for devices
    print("\n[2/4] Waiting for devices (15s)...")
    devices = {}
    for i in range(15):
        time.sleep(1)
        devices = discover_devices()
        if devices:
            break
        print(f"  ... {i + 1}s", flush=True)

    if not devices:
        print("  [!] No devices found. Is your ESP32 powered on?")
        print("  Gateway will keep running, press Ctrl+C to quit")
    else:
        ROLE_NAMES = {0: "Follower", 1: "Leader", 2: "M-Leader", 3: "Gateway", 4: "JoyCon"}
        for did, d in devices.items():
            role = ROLE_NAMES.get(d.get("role", -1), "Unknown")
            n_servos = len(d.get("servos", []))
            mac = d.get("mac", "?")
            print(f"  dev={did} {role} mac={mac} servos={n_servos}")

    # Step 3: Find STS ports and start bridges
    print("\n[3/4] Starting COM bridges...")
    time.sleep(2)  # Let STS ports initialize
    sts_ports = find_sts_ports()
    print(f"  STS TCP ports found: {sts_ports}")

    # Map STS ports to COM pairs: 6560->COM20/COM21, 6561->COM22/COM23
    com_base = 20
    bridge_pairs = []
    for tcp_port in sts_ports:
        bridge_com = f"COM{com_base}"
        fd_com = f"COM{com_base + 1}"
        bridge_pairs.append((bridge_com, fd_com, tcp_port))
        com_base += 2

    for bridge_com, fd_com, tcp_port in bridge_pairs:
        print(f"  Bridge: {bridge_com} <-> TCP:{tcp_port}  (FD connect: {fd_com})")
        bp = subprocess.Popen(
            [sys.executable, os.path.join(SCRIPT_DIR, "com_tcp_bridge.py"),
             "--pairs", f"{bridge_com}:{tcp_port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        procs.append(bp)

    time.sleep(2)

    # Step 4: Verify
    print("\n[4/4] Verification PING test...")
    for bridge_com, fd_com, tcp_port in bridge_pairs:
        found = test_ping(fd_com, list(range(1, 9)))
        status = f"IDs={found}" if found else "NO RESPONSE"
        print(f"  {fd_com} (TCP:{tcp_port}): {status}")

    # Summary
    print("\n" + "=" * 55)
    print("  Ready! FD software connection info:")
    print("=" * 55)
    for bridge_com, fd_com, tcp_port in bridge_pairs:
        print(f"  Port: {fd_com}  Baud: {BAUD}  (TCP:{tcp_port})")
    print(f"\n  Press Ctrl+C to stop all services")
    print("=" * 55 + "\n")

    try:
        while True:
            time.sleep(5)
            # Check if gateway is still running
            if gw_proc.poll() is not None:
                print("[!] Gateway process exited unexpectedly")
                break
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print("All stopped.")


if __name__ == "__main__":
    main()
