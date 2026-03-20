#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
Box2Driver 固件版本查询工具

自动检测 ESP32 串口，查询当前固件版本。
支持两种方式：
  1. Gateway 模式：发送 {"cmd":"info"} 查询（不重启）
  2. 任意模式：DTR 重启后读取启动日志

用法：
    python check_firmware.py              # 自动检测串口
    python check_firmware.py -p COM36     # 指定串口
    python check_firmware.py --reset      # 强制重启方式读取

依赖：
    pip install pyserial
"""

import argparse
import json
import re
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("错误: pip install pyserial")
    sys.exit(1)


def find_cp210x_port():
    """自动检测 CP210x 串口，单个直接返回，多个让用户选。"""
    ports = serial.tools.list_ports.comports()
    cp210x = [p for p in ports
              if p.vid == 0x10C4 or 'cp210' in (p.description or '').lower()
              or 'silicon labs' in (p.description or '').lower()]

    if len(cp210x) == 1:
        print(f"检测到: {cp210x[0].device}  ({cp210x[0].description})")
        return cp210x[0].device
    if len(cp210x) > 1:
        print(f"发现 {len(cp210x)} 个 CP210x 串口:")
        for i, p in enumerate(cp210x):
            print(f"  [{i}] {p.device:10s}  {p.description}")
        choice = input("请选择 (默认 0): ").strip()
        idx = int(choice) if choice.isdigit() and int(choice) < len(cp210x) else 0
        return cp210x[idx].device

    # 无 CP210x，尝试其他 USB-Serial
    usb = [p for p in ports if p.vid]
    if len(usb) == 1:
        print(f"检测到: {usb[0].device}  ({usb[0].description})")
        return usb[0].device
    if usb:
        print("未发现 CP210x，可用串口:")
        for i, p in enumerate(usb):
            print(f"  [{i}] {p.device:10s}  {p.description}")
        choice = input("请选择 (默认 0): ").strip()
        idx = int(choice) if choice.isdigit() and int(choice) < len(usb) else 0
        return usb[idx].device

    print("未发现任何 USB 串口设备")
    return None


def query_info(ser, timeout=3.0):
    """发送 {"cmd":"info"} 查询固件信息（Gateway 模式）。"""
    ser.reset_input_buffer()
    cmd = '{"cmd":"info"}\n'
    ser.write(cmd.encode())
    ser.flush()

    t0 = time.time()
    while time.time() - t0 < timeout:
        line = ser.readline().decode('utf-8', errors='replace').strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if 'gw_info' in data:
                return data['gw_info']
        except json.JSONDecodeError:
            pass
    return None


def reset_and_read(ser, timeout=8.0):
    """DTR 重启 ESP32，从启动日志中读取版本信息。"""
    print("重启 ESP32...")
    ser.dtr = True
    time.sleep(0.1)
    ser.dtr = False
    time.sleep(0.1)
    ser.reset_input_buffer()

    info = {}
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = ser.readline().decode('utf-8', errors='replace').strip()
        if not line:
            continue

        # 启动日志中的版本行: "  Version: v0.4.3"
        m = re.search(r'Version:\s*(v[\d.]+)', line)
        if m:
            info['fw'] = m.group(1).lstrip('v')

        # gw_info JSON (Gateway 模式)
        if line.startswith('{'):
            try:
                data = json.loads(line)
                if 'gw_info' in data:
                    return data['gw_info']
            except json.JSONDecodeError:
                pass

        # MAC 地址: "[MAIN] MAC: XX:XX:XX:XX:XX:XX"
        m2 = re.search(r'MAC:\s*([0-9A-Fa-f:]{17})', line)
        if m2:
            info['mac'] = m2.group(1).upper()

        # 模式和舵机数: "[MAIN] System ready! Found N servos, Mode=M"
        m3 = re.search(r'Found (\d+) servos.*Mode=(\d+)', line)
        if m3:
            info['servos'] = int(m3.group(1))
            info['role'] = int(m3.group(2))

        # 有版本就可以提前返回（等 MAC 和 ready）
        if 'fw' in info and ('mac' in info or time.time() - t0 > 4):
            return info

    return info if info else None


def main():
    parser = argparse.ArgumentParser(description='Box2Driver 固件版本查询')
    parser.add_argument('-p', '--port', help='串口号 (如 COM36)')
    parser.add_argument('-b', '--baud', type=int, default=921600, help='波特率 (默认 921600)')
    parser.add_argument('--reset', action='store_true', help='强制 DTR 重启读取 (默认先尝试 info 查询)')
    args = parser.parse_args()

    port = args.port or find_cp210x_port()
    if not port:
        sys.exit(1)

    try:
        ser = serial.Serial(port, args.baud, timeout=0.5)
    except serial.SerialException as e:
        print(f"无法打开 {port}: {e}")
        sys.exit(1)

    ser.dtr = False
    ser.rts = False
    time.sleep(0.2)

    info = None
    role_names = {0: 'Follower', 1: 'Leader', 2: 'M-Leader', 3: 'Gateway', 4: 'JoyCon'}

    if not args.reset:
        print(f"查询 {port} 固件信息...")
        info = query_info(ser, timeout=2.0)
        if info:
            print("(通过 info 命令获取)")

    if not info:
        if not args.reset:
            print("info 命令无响应 (可能非 Gateway 模式)，将重启读取...")
        info = reset_and_read(ser, timeout=8.0)

    ser.close()

    if not info:
        print("\n未能获取固件信息。请确认：")
        print("  1. ESP32 已上电且 USB 连接正常")
        print("  2. 串口未被其他程序占用")
        sys.exit(1)

    print("\n" + "=" * 40)
    print("  Box2Driver 固件信息")
    print("=" * 40)
    fw = info.get('fw', '未知')
    print(f"  固件版本:  v{fw}")
    if 'mac' in info:
        print(f"  MAC 地址:  {info['mac']}")
    if 'role' in info:
        role = info['role']
        print(f"  当前模式:  {role} ({role_names.get(role, '未知')})")
    if 'servos' in info:
        print(f"  舵机数量:  {info['servos']}")
    print("=" * 40)


if __name__ == '__main__':
    main()
