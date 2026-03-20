#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
Box2Driver Gateway 串口接收脚本

功能：
- 从 Gateway ESP32 的 USB 串口读取姿态 JSON 数据
- 按设备分类显示实时姿态
- 可选保存到 JSONL 文件（每行一条 JSON）

用法：
    python gateway_recv.py                     # 自动检测串口
    python gateway_recv.py -p COM5             # 指定串口
    python gateway_recv.py -p COM5 -s          # 保存到文件
    python gateway_recv.py -p COM5 -s -o data/ # 指定保存目录
    python gateway_recv.py --list              # 列出可用串口

依赖：
    pip install pyserial
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("错误: 请先安装 pyserial")
    print("  pip install pyserial")
    sys.exit(1)


def list_ports():
    """列出所有可用串口"""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("未发现任何串口设备")
        return []
    print(f"发现 {len(ports)} 个串口:")
    for p in ports:
        print(f"  {p.device:10s}  {p.description}")
    return ports


def find_gateway_port():
    """尝试自动检测 Gateway 串口（ESP32 USB）"""
    ports = serial.tools.list_ports.comports()
    candidates = []
    for p in ports:
        desc = (p.description or "").lower()
        # ESP32 常见 USB 芯片描述
        if any(kw in desc for kw in ["cp210", "ch340", "ch910", "ftdi", "usb", "serial", "uart"]):
            candidates.append(p)
    if len(candidates) == 1:
        return candidates[0].device
    if len(candidates) > 1:
        print("发现多个可能的串口:")
        for i, p in enumerate(candidates):
            print(f"  [{i}] {p.device:10s}  {p.description}")
        choice = input("请选择序号 (默认 0): ").strip()
        idx = int(choice) if choice.isdigit() else 0
        if 0 <= idx < len(candidates):
            return candidates[idx].device
    return None


class GatewayReceiver:
    def __init__(self, port, baudrate=115200, save=False, output_dir="."):
        self.port = port
        self.baudrate = baudrate
        self.save = save
        self.output_dir = output_dir
        self.ser = None
        self.file = None
        self.filename = None

        # 统计
        self.total_packets = 0
        self.devices = {}  # dev_id -> {role, last_seq, count, last_data}
        self.start_time = time.time()

    def connect(self):
        """连接串口（禁用 DTR/RTS 防止 ESP32 重启）"""
        print(f"连接串口 {self.port} @ {self.baudrate}...")
        self.ser = serial.Serial()
        self.ser.port = self.port
        self.ser.baudrate = self.baudrate
        self.ser.timeout = 1
        # 关键：禁用 DTR/RTS，防止打开串口时 ESP32 自动重启
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        time.sleep(0.3)
        # 清空缓冲
        self.ser.reset_input_buffer()
        print(f"已连接: {self.port}（DTR/RTS 已禁用，ESP32 不会重启）")

    def start_save(self):
        """开始保存文件"""
        if not self.save:
            return
        os.makedirs(self.output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filename = os.path.join(self.output_dir, f"feedback_{ts}.jsonl")
        self.file = open(self.filename, "w", encoding="utf-8")
        print(f"保存到: {self.filename}")

    def process_line(self, line):
        """处理一行 JSON 数据"""
        line = line.strip()
        if not line or not line.startswith("{"):
            return

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return

        # 验证是姿态包
        if "dev" not in data or "servos" not in data:
            return

        self.total_packets += 1
        dev_id = data["dev"]
        role = data.get("role", -1)
        seq = data.get("seq", 0)
        role_names = {0: "Follower", 1: "Leader", 2: "M-Leader"}
        role_name = role_names.get(role, f"Unknown({role})")

        # 更新设备统计
        if dev_id not in self.devices:
            self.devices[dev_id] = {
                "role": role_name,
                "count": 0,
                "first_seen": time.time(),
            }
        dev = self.devices[dev_id]
        dev["count"] += 1
        dev["role"] = role_name
        dev["last_seq"] = seq
        dev["last_data"] = data

        # 保存到文件（添加 PC 时间戳）
        if self.file:
            data["_pc_time"] = time.time()
            self.file.write(json.dumps(data, ensure_ascii=False) + "\n")
            self.file.flush()

        # 终端实时显示
        self.display(data, dev_id, role_name)

    def display(self, data, dev_id, role_name):
        """终端实时显示"""
        servos = data.get("servos", [])
        t = data.get("t", 0)
        seq = data.get("seq", 0)
        mac = data.get("mac", "?")

        # 构建紧凑的舵机信息
        servo_strs = []
        for s in servos:
            servo_strs.append(f"#{s['id']}:p{s['pos']:>5d} l{s['load']:>4d}")
        servos_line = " | ".join(servo_strs)

        elapsed = time.time() - self.start_time
        print(f"[{elapsed:7.1f}s] {role_name:>9s} {dev_id} seq={seq:3d} t={t:>8d}ms  {servos_line}")

    def run(self):
        """主循环"""
        self.connect()
        self.start_save()

        # 等待 ---JSON_START--- 标记
        print("等待 Gateway 就绪...")
        json_started = False
        timeout_start = time.time()
        while not json_started and time.time() - timeout_start < 10:
            if self.ser.in_waiting:
                line = self.ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    print(f"  [BOOT] {line}")
                if "---JSON_START---" in line:
                    json_started = True
                    break

        if not json_started:
            print("未收到 JSON_START 标记，继续尝试读取...")

        print()
        print("=" * 80)
        print(f"  Gateway 数据接收中  |  Ctrl+C 停止")
        if self.save:
            print(f"  保存到: {self.filename}")
        print("=" * 80)
        print()

        try:
            while True:
                if self.ser.in_waiting:
                    line = self.ser.readline().decode("utf-8", errors="replace")
                    self.process_line(line)
                else:
                    time.sleep(0.001)
        except KeyboardInterrupt:
            print("\n\n--- 统计 ---")
            elapsed = time.time() - self.start_time
            print(f"运行时间: {elapsed:.1f}s")
            print(f"总包数:   {self.total_packets}")
            for dev_id, info in self.devices.items():
                rate = info["count"] / elapsed if elapsed > 0 else 0
                print(f"  设备 {dev_id} ({info['role']}): {info['count']} 包, {rate:.1f} 包/秒")
            if self.file:
                print(f"数据已保存: {self.filename}")
        finally:
            if self.file:
                self.file.close()
            if self.ser:
                self.ser.close()


def main():
    parser = argparse.ArgumentParser(
        description="Box2Driver Gateway 姿态数据接收脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python gateway_recv.py                     自动检测串口
  python gateway_recv.py -p COM5             指定串口
  python gateway_recv.py -p COM5 -s          接收并保存到 JSONL 文件
  python gateway_recv.py -p COM5 -s -o data/ 指定保存目录
  python gateway_recv.py --list              列出可用串口
        """,
    )
    parser.add_argument("-p", "--port", help="串口名称 (如 COM5 或 /dev/ttyUSB0)")
    parser.add_argument("-b", "--baud", type=int, default=115200, help="波特率 (默认 115200)")
    parser.add_argument("-s", "--save", action="store_true", help="保存数据到 JSONL 文件")
    parser.add_argument("-o", "--output", default=".", help="保存目录 (默认当前目录)")
    parser.add_argument("--list", action="store_true", help="列出可用串口")
    args = parser.parse_args()

    if args.list:
        list_ports()
        return

    port = args.port
    if not port:
        port = find_gateway_port()
        if not port:
            print("未能自动检测到串口，请使用 -p 指定")
            print("可用串口:")
            list_ports()
            return

    receiver = GatewayReceiver(
        port=port,
        baudrate=args.baud,
        save=args.save,
        output_dir=args.output,
    )
    receiver.run()


if __name__ == "__main__":
    main()
