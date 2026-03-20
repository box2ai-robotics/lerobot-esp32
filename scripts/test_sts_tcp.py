#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
STS TCP 虚拟串口快速测试

测试 gateway_dashboard.py 内置的 STS TCP 服务是否可用。
无需虚拟串口驱动，纯 TCP 连接。

用法:
    python test_sts_tcp.py                          # 默认 localhost:6560
    python test_sts_tcp.py --port 6561              # 指定端口
    python test_sts_tcp.py --scan                   # 扫描所有端口
"""

import argparse
import socket
import struct
import sys
import time


def sts_checksum(data: bytes) -> int:
    return (~sum(data)) & 0xFF


def build_packet(servo_id, instruction, params=b''):
    """构造 STS 指令包"""
    length = len(params) + 2
    pkt = bytes([servo_id, length, instruction]) + params
    return b'\xff\xff' + pkt + bytes([sts_checksum(pkt)])


def read_response(sock, timeout=0.5):
    """从 TCP socket 读取一个完整的 STS 状态包"""
    sock.settimeout(timeout)
    buf = bytearray()
    try:
        # 先读头部
        while len(buf) < 4:
            chunk = sock.recv(64)
            if not chunk:
                return None
            buf.extend(chunk)
        # 跳过非 FF FF 头
        while len(buf) >= 2 and (buf[0] != 0xFF or buf[1] != 0xFF):
            buf.pop(0)
        if len(buf) < 4:
            return None
        pkt_len = buf[3]
        total = 4 + pkt_len
        # 读剩余
        while len(buf) < total:
            chunk = sock.recv(64)
            if not chunk:
                break
            buf.extend(chunk)
        if len(buf) < total:
            return None
        return bytes(buf[:total])
    except socket.timeout:
        return bytes(buf) if buf else None


def read_multi_response(sock, count, timeout=0.5):
    """读取多个连续的 STS 状态包 (用于 SYNC_READ)"""
    sock.settimeout(timeout)
    buf = bytearray()
    results = []
    deadline = time.time() + timeout + 0.3

    while len(results) < count and time.time() < deadline:
        try:
            chunk = sock.recv(256)
            if not chunk:
                break
            buf.extend(chunk)
        except socket.timeout:
            pass

        # 尝试解析 buf 中的完整包
        while len(buf) >= 6:
            idx = buf.find(b'\xff\xff')
            if idx < 0:
                buf.clear()
                break
            if idx > 0:
                buf = buf[idx:]
            if len(buf) < 4:
                break
            pkt_len = buf[3]
            total = 4 + pkt_len
            if len(buf) < total:
                break
            pkt = bytes(buf[:total])
            buf = buf[total:]
            results.append(pkt)

    return results


def parse_status(pkt):
    """解析 STS 状态包 → (servo_id, error, payload)"""
    if not pkt or len(pkt) < 6:
        return None
    if pkt[0] != 0xFF or pkt[1] != 0xFF:
        return None
    sid = pkt[2]
    length = pkt[3]
    if len(pkt) < 4 + length:
        return None
    error = pkt[4]
    payload = pkt[5:4 + length - 1]
    return sid, error, payload


def test_port(tcp_port, host="localhost"):
    """测试一个 STS TCP 端口"""
    url = f"{host}:{tcp_port}"
    print(f"\n{'='*55}")
    print(f"  STS TCP 测试: socket://{url}")
    print(f"{'='*55}")

    # 建立 TCP 连接
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.connect((host, tcp_port))
    except Exception as e:
        print(f"  [FAIL] 连接失败: {e}")
        return False

    found_servos = []

    # --- Test 1: PING ---
    print(f"\n--- 测试 1: PING (ID=1~8) ---")
    for sid in range(1, 9):
        pkt = build_packet(sid, 0x01)  # PING
        sock.sendall(pkt)
        resp = read_response(sock, timeout=0.3)
        parsed = parse_status(resp) if resp else None
        if parsed:
            print(f"  ID={sid}: PING OK  (error={parsed[1]})")
            found_servos.append(sid)
        else:
            pass  # 没有此 ID 的舵机，不打印

    if found_servos:
        print(f"  >> 发现舵机: {found_servos}")
    else:
        print(f"  [WARN] 未发现任何舵机")

    # --- Test 2: READ Present_Position ---
    print(f"\n--- 测试 2: READ Present_Position ---")
    for sid in found_servos:
        pkt = build_packet(sid, 0x02, bytes([56, 2]))  # addr=56, len=2
        sock.sendall(pkt)
        resp = read_response(sock, timeout=0.3)
        parsed = parse_status(resp) if resp else None
        if parsed and len(parsed[2]) >= 2:
            pos = parsed[2][0] | (parsed[2][1] << 8)
            print(f"  ID={sid}: position = {pos}")

    # --- Test 3: READ Model ---
    print(f"\n--- 测试 3: READ Model ---")
    for sid in found_servos[:1]:
        pkt = build_packet(sid, 0x02, bytes([3, 2]))  # addr=3, len=2
        sock.sendall(pkt)
        resp = read_response(sock, timeout=0.3)
        parsed = parse_status(resp) if resp else None
        if parsed and len(parsed[2]) >= 2:
            model = parsed[2][0] | (parsed[2][1] << 8)
            name = "ST3215" if model == 0x0F0C else f"未知(0x{model:04X})"
            print(f"  ID={sid}: model = {name}")

    # --- Test 4: SYNC_READ ---
    print(f"\n--- 测试 4: SYNC_READ (所有舵机位置) ---")
    ids = found_servos or list(range(1, 7))
    params = bytes([56, 2]) + bytes(ids)
    pkt = build_packet(0xFE, 0x82, params)
    sock.sendall(pkt)
    responses = read_multi_response(sock, len(ids), timeout=0.5)
    for resp in responses:
        parsed = parse_status(resp)
        if parsed and len(parsed[2]) >= 2:
            pos = parsed[2][0] | (parsed[2][1] << 8)
            print(f"  ID={parsed[0]}: position = {pos}")

    # --- Test 5: 完整状态读取 ---
    print(f"\n--- 测试 5: 完整状态 (位置+速度+负载+电压+温度) ---")
    for sid in found_servos:
        pkt = build_packet(sid, 0x02, bytes([56, 8]))  # addr=56~63
        sock.sendall(pkt)
        resp = read_response(sock, timeout=0.3)
        parsed = parse_status(resp) if resp else None
        if parsed and len(parsed[2]) >= 8:
            d = parsed[2]
            pos = d[0] | (d[1] << 8)
            spd = d[2] | (d[3] << 8)
            load = d[4] | (d[5] << 8)
            volt = d[6]
            temp = d[7]
            print(f"  ID={sid}: pos={pos:5d}  spd={spd:4d}  load={load:4d}  "
                  f"volt={volt/10:.1f}V  temp={temp}°C")

    sock.close()

    print(f"\n{'='*55}")
    if found_servos:
        print(f"  测试通过! 发现 {len(found_servos)} 个舵机: {found_servos}")
    else:
        print(f"  未发现舵机 (设备可能尚未上线)")
    print(f"{'='*55}\n")
    return len(found_servos) > 0


def scan_ports(host="localhost", base=6560, count=10):
    """扫描多个端口"""
    print(f"\n扫描 {host}:{base} ~ {host}:{base+count-1} ...\n")
    found = []
    for port in range(base, base + count):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect((host, port))
            # PING ID=1
            pkt = build_packet(1, 0x01)
            sock.sendall(pkt)
            resp = read_response(sock, timeout=0.3)
            parsed = parse_status(resp) if resp else None
            if parsed:
                print(f"  端口 {port}: 在线, PING ID=1 OK")
                found.append(port)
            else:
                print(f"  端口 {port}: 已连接, 无 PING 响应")
                found.append(port)
            sock.close()
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass
    if found:
        print(f"\n发现 {len(found)} 个 STS 端口: {found}")
    else:
        print(f"\n未发现 STS 端口 (gateway_dashboard 是否在运行?)")


def main():
    parser = argparse.ArgumentParser(description="STS TCP 虚拟串口测试")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6560)
    parser.add_argument("--scan", action="store_true", help="扫描 6560~6569 端口")
    args = parser.parse_args()

    if args.scan:
        scan_ports(args.host)
    else:
        test_port(args.port, args.host)


if __name__ == "__main__":
    main()
