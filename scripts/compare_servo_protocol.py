#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
对比测试: 真实舵机 vs 虚拟桥接
发送相同的 STS 协议命令到两个串口，逐字节对比响应差异。

用法:
    python compare_servo_protocol.py --real COM23 --virtual COM50 --id 1
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("pip install pyserial")
    sys.exit(1)


def checksum(data: bytes) -> int:
    return (~sum(data)) & 0xFF


def build_packet(servo_id, instruction, params=b''):
    length = len(params) + 2
    pkt = bytes([0xFF, 0xFF, servo_id, length, instruction]) + params
    return pkt + bytes([checksum(pkt[2:])])


def send_recv(ser, pkt, timeout=0.02):
    ser.reset_input_buffer()
    ser.write(pkt)
    ser.flush()
    time.sleep(timeout)
    n = ser.in_waiting
    if n > 0:
        return ser.read(n)
    return b''


def parse_response(resp):
    """解析 STS 应答包"""
    if len(resp) < 6:
        return None
    if resp[0] != 0xFF or resp[1] != 0xFF:
        return None
    sid = resp[2]
    plen = resp[3]
    if len(resp) < 4 + plen:
        return None
    err = resp[4]
    data = resp[5:4 + plen - 1]  # 去掉 checksum
    return {"id": sid, "len": plen, "err": err, "data": data, "raw": resp[:4 + plen]}


def hex_diff(label_a, resp_a, label_b, resp_b):
    """打印两个响应的差异"""
    hex_a = resp_a.hex() if resp_a else "(无响应)"
    hex_b = resp_b.hex() if resp_b else "(无响应)"
    match = hex_a == hex_b
    status = "OK" if match else "DIFF"
    print(f"  [{status}] {label_a}: {hex_a}")
    if not match:
        print(f"  [{status}] {label_b}: {hex_b}")
        # 逐字节对比
        if resp_a and resp_b:
            max_len = max(len(resp_a), len(resp_b))
            diff_positions = []
            for i in range(max_len):
                a = resp_a[i] if i < len(resp_a) else None
                b = resp_b[i] if i < len(resp_b) else None
                if a != b:
                    diff_positions.append(i)
            if diff_positions:
                print(f"         差异位置: {diff_positions}")
                for pos in diff_positions[:10]:
                    a_val = f"0x{resp_a[pos]:02x}" if pos < len(resp_a) else "N/A"
                    b_val = f"0x{resp_b[pos]:02x}" if pos < len(resp_b) else "N/A"
                    print(f"         byte[{pos}]: {label_a}={a_val}  {label_b}={b_val}")
    return match


def test_ping(ser_real, ser_virtual, sid):
    print(f"\n=== PING ID={sid} ===")
    pkt = build_packet(sid, 0x01)
    print(f"  TX: {pkt.hex()}")
    resp_real = send_recv(ser_real, pkt)
    resp_virt = send_recv(ser_virtual, pkt)
    return hex_diff("Real", resp_real, "Virt", resp_virt)


def test_read(ser_real, ser_virtual, sid, addr, length, desc=""):
    print(f"\n=== READ ID={sid} addr={addr} len={length} {desc} ===")
    pkt = build_packet(sid, 0x02, bytes([addr, length]))
    print(f"  TX: {pkt.hex()}")
    resp_real = send_recv(ser_real, pkt)
    resp_virt = send_recv(ser_virtual, pkt)
    match = hex_diff("Real", resp_real, "Virt", resp_virt)

    # 解析数据内容
    p_real = parse_response(resp_real)
    p_virt = parse_response(resp_virt)
    if p_real and p_virt:
        print(f"  Real data ({len(p_real['data'])}B): {p_real['data'].hex()}")
        print(f"  Virt data ({len(p_virt['data'])}B): {p_virt['data'].hex()}")
        # 逐地址解读
        if not match and p_real['data'] and p_virt['data']:
            for i in range(min(len(p_real['data']), len(p_virt['data']))):
                if p_real['data'][i] != p_virt['data'][i]:
                    print(f"    addr[{addr+i}]: real=0x{p_real['data'][i]:02x}({p_real['data'][i]})  virt=0x{p_virt['data'][i]:02x}({p_virt['data'][i]})")
    return match


def test_read_single(ser, label, sid, addr, length, desc=""):
    """只对单个串口执行 READ"""
    pkt = build_packet(sid, 0x02, bytes([addr, length]))
    resp = send_recv(ser, pkt)
    p = parse_response(resp)
    if p:
        print(f"  [{label}] READ addr={addr} len={length} {desc}: err={p['err']} data={p['data'].hex()}")
        return p['data']
    else:
        print(f"  [{label}] READ addr={addr} len={length} {desc}: 无响应 raw={resp.hex() if resp else 'empty'}")
        return None


def test_ping_nonexist(ser_real, ser_virtual, sid):
    """测试不存在的 ID 应该无响应"""
    print(f"\n=== PING 不存在的 ID={sid} (应该无响应) ===")
    pkt = build_packet(sid, 0x01)
    resp_real = send_recv(ser_real, pkt, timeout=0.005)
    resp_virt = send_recv(ser_virtual, pkt, timeout=0.005)
    real_ok = len(resp_real) == 0
    virt_ok = len(resp_virt) == 0
    print(f"  Real: {'无响应 OK' if real_ok else f'异常响应: {resp_real.hex()}'}")
    print(f"  Virt: {'无响应 OK' if virt_ok else f'异常响应: {resp_virt.hex()}'}")
    return real_ok and virt_ok


def main():
    parser = argparse.ArgumentParser(description="对比真实舵机 vs 虚拟桥接响应")
    parser.add_argument("--real", required=True, help="真实舵机串口 (如 COM23)")
    parser.add_argument("--virtual", required=True, help="虚拟桥接串口 (如 COM50)")
    parser.add_argument("--id", type=int, default=1, help="舵机 ID (默认 1)")
    parser.add_argument("--baud", type=int, default=1000000, help="波特率 (默认 1000000)")
    args = parser.parse_args()

    sid = args.id

    print(f"打开真实舵机: {args.real}")
    ser_real = serial.Serial(args.real, args.baud, timeout=0.05)
    print(f"打开虚拟桥接: {args.virtual}")
    ser_virtual = serial.Serial(args.virtual, args.baud, timeout=0.05)
    time.sleep(0.5)

    results = []

    # 1. PING
    results.append(("PING", test_ping(ser_real, ser_virtual, sid)))

    # 2. PING 不存在的 ID
    results.append(("PING non-exist", test_ping_nonexist(ser_real, ser_virtual, 253)))

    # 3. FD 扫描时的 READ: addr=0, len=9 (fw_major, fw_minor, servo_ver, model_L, model_H, ID, baud, return_delay, response_level)
    results.append(("READ SCAN(0,9)", test_read(ser_real, ser_virtual, sid, 0, 9, "(FD扫描)")))

    # 4. READ EPROM 完整: addr=0, len=55
    results.append(("READ EPROM(0,55)", test_read(ser_real, ser_virtual, sid, 0, 55, "(完整EPROM)")))

    # 5. READ SRAM 实时数据: addr=56, len=10 (pos+spd+load+volt+temp)
    results.append(("READ SRAM(56,10)", test_read(ser_real, ser_virtual, sid, 56, 10, "(实时数据)")))

    # 6. READ 完整 SRAM: addr=40, len=31
    results.append(("READ SRAM(40,31)", test_read(ser_real, ser_virtual, sid, 40, 31, "(SRAM完整)")))

    # 7. FD 常用: addr=56, len=17
    results.append(("READ FD_MONITOR(56,17)", test_read(ser_real, ser_virtual, sid, 56, 17, "(FD监控)")))

    # 8. READ addr=56, len=2 (仅位置)
    results.append(("READ POS(56,2)", test_read(ser_real, ser_virtual, sid, 56, 2, "(仅位置)")))

    # 9. 单独读真实舵机全部地址 (作为参考)
    print(f"\n=== 真实舵机完整内存 dump (参考) ===")
    for start in [0, 40, 55]:
        length = min(40, 86 - start)
        test_read_single(ser_real, "Real", sid, start, length, f"(addr {start}-{start+length-1})")
    print(f"\n=== 虚拟桥接完整内存 dump (参考) ===")
    for start in [0, 40, 55]:
        length = min(40, 86 - start)
        test_read_single(ser_virtual, "Virt", sid, start, length, f"(addr {start}-{start+length-1})")

    # 总结
    print("\n" + "=" * 60)
    print("测试总结:")
    print("=" * 60)
    pass_count = sum(1 for _, ok in results if ok)
    fail_count = sum(1 for _, ok in results if not ok)
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}: {name}")
    print(f"\n  总计: {pass_count} 通过, {fail_count} 失败")

    ser_real.close()
    ser_virtual.close()


if __name__ == "__main__":
    main()
