#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
COM <-> TCP 原始字节桥接器

将 com0com 虚拟串口对的一端桥接到 gateway_dashboard.py 的 STS TCP 端口，
使得另一端对外表现为真实的飞特舵机串口。

架构:
  FD上位机/LeRobot -> COM21 <-(com0com)-> COM20 <-(本脚本)-> TCP:6560 <- gateway STS
  FD上位机/LeRobot -> COM23 <-(com0com)-> COM22 <-(本脚本)-> TCP:6561 <- gateway STS

用法:
  python com_tcp_bridge.py                           # 默认: COM20<->6560, COM22<->6561
  python com_tcp_bridge.py --pairs COM20:6560        # 单个桥接
  python com_tcp_bridge.py --pairs COM20:6560 COM22:6561  # 多个桥接
"""

import argparse
import socket
import sys
import threading
import time

try:
    import serial
except ImportError:
    print("错误: pip install pyserial")
    sys.exit(1)


class COMTCPBridge:
    """单个 COM <-> TCP 桥接"""

    def __init__(self, com_port: str, tcp_port: int, baud: int = 1000000):
        self.com_port = com_port
        self.tcp_port = tcp_port
        self.baud = baud
        self._ser = None
        self._sock = None
        self._running = False
        self._stats = {"com2tcp": 0, "tcp2com": 0}

    def start(self):
        # 打开串口
        self._ser = serial.Serial()
        self._ser.port = self.com_port
        self._ser.baudrate = self.baud
        self._ser.timeout = 0.01
        self._ser.write_timeout = 0.5
        try:
            self._ser.open()
        except serial.SerialException as e:
            print(f"[{self.com_port}] 无法打开串口: {e}")
            return False

        # 连接 TCP
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self._sock.connect(("127.0.0.1", self.tcp_port))
            self._sock.settimeout(0.01)
        except OSError as e:
            print(f"[{self.com_port}] 无法连接 TCP:{self.tcp_port}: {e}")
            self._ser.close()
            return False

        self._running = True
        print(f"[桥接] {self.com_port} <-> TCP:{self.tcp_port} 已建立")

        # COM->TCP 线程
        t1 = threading.Thread(target=self._com_to_tcp, daemon=True)
        # TCP->COM 线程
        t2 = threading.Thread(target=self._tcp_to_com, daemon=True)
        t1.start()
        t2.start()
        return True

    def _com_to_tcp(self):
        """COM 读取 -> TCP 发送"""
        while self._running:
            try:
                data = self._ser.read(1024)
                if data:
                    self._sock.sendall(data)
                    self._stats["com2tcp"] += len(data)
            except serial.SerialException:
                print(f"[{self.com_port}] 串口断开")
                self._running = False
            except OSError:
                print(f"[{self.com_port}] TCP 断开")
                self._running = False

    def _tcp_to_com(self):
        """TCP 读取 -> COM 发送"""
        while self._running:
            try:
                data = self._sock.recv(4096)
                if not data:
                    print(f"[{self.com_port}] TCP 连接关闭")
                    self._running = False
                    break
                self._ser.write(data)
                self._ser.flush()
                self._stats["tcp2com"] += len(data)
            except socket.timeout:
                continue
            except (OSError, serial.SerialException):
                self._running = False

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._ser and self._ser.is_open:
            self._ser.close()

    def print_stats(self):
        print(f"  {self.com_port} <-> TCP:{self.tcp_port}  "
              f"COM->TCP: {self._stats['com2tcp']}B  "
              f"TCP->COM: {self._stats['tcp2com']}B")


def main():
    parser = argparse.ArgumentParser(
        description="COM <-> TCP 原始字节桥接 (com0com + gateway STS TCP)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python com_tcp_bridge.py                                # 默认双桥接
  python com_tcp_bridge.py --pairs COM20:6560             # 单桥接
  python com_tcp_bridge.py --pairs COM20:6560 COM22:6561  # 双桥接

FD上位机连接:
  桥接 COM20:6560 -> FD 软件打开 COM21 (Leader)
  桥接 COM22:6561 -> FD 软件打开 COM23 (Follower)
        """,
    )
    parser.add_argument(
        "--pairs", nargs="+", default=["COM20:6560", "COM22:6561"],
        help="COM:TCP 桥接对 (默认: COM20:6560 COM22:6561)"
    )
    parser.add_argument("-b", "--baud", type=int, default=1000000, help="波特率 (默认 1000000)")
    args = parser.parse_args()

    bridges = []
    for pair in args.pairs:
        parts = pair.split(":")
        if len(parts) != 2:
            print(f"格式错误: {pair}, 应为 COMx:port")
            sys.exit(1)
        com_port = parts[0]
        tcp_port = int(parts[1])
        bridges.append(COMTCPBridge(com_port, tcp_port, args.baud))

    print()
    print("=" * 55)
    print("  COM <-> TCP 桥接器")
    print("=" * 55)
    for b in bridges:
        peer_num = int(b.com_port.replace("COM", "")) + 1
        print(f"  {b.com_port} <-> TCP:{b.tcp_port}  |  FD软件请连 COM{peer_num}")
    print("=" * 55)
    print()

    ok_count = 0
    for b in bridges:
        if b.start():
            ok_count += 1

    if ok_count == 0:
        print("所有桥接都失败了，请检查:")
        print("  1. com0com 串口对是否已创建")
        print("  2. gateway_dashboard.py 是否在运行")
        sys.exit(1)

    print(f"\n{ok_count}/{len(bridges)} 个桥接已启动，按 Ctrl+C 退出\n")

    try:
        while True:
            time.sleep(10)
            print("[统计]")
            for b in bridges:
                b.print_stats()
    except KeyboardInterrupt:
        print("\n\n--- 最终统计 ---")
        for b in bridges:
            b.print_stats()
        for b in bridges:
            b.stop()
        print("已停止")


if __name__ == "__main__":
    main()
