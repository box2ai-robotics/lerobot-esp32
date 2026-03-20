#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
Box2Driver Gateway Web Dashboard + Virtual Servo Bridge

功能：
- 自动检测 CP210x USB 串口（ESP32 Gateway）
- 从 Gateway ESP32 USB 串口读取姿态 JSON 数据
- 启动本地 Web 服务器 + WebSocket 实时推送
- 浏览器打开 dashboard 页面，显示各机械臂舵机数据和轨迹
- 内置 STS 协议虚拟串口 TCP 服务（每设备独立端口）
- 可选 com0com/socat 虚拟串口桥接 (--bridge)
- 可选纯虚拟串口模式，不启动 Web (--no-web)

用法：
    python gateway_dashboard.py                     # 自动检测 CP210x 串口，启动 Web + STS
    python gateway_dashboard.py -p COM5             # 指定串口
    python gateway_dashboard.py --bridge            # 同时启动 com0com/socat 虚拟串口
    python gateway_dashboard.py --no-web            # 不启动 Web，只启动串口+WS+虚拟串口
    python gateway_dashboard.py --list              # 列出可用串口
    python gateway_dashboard.py --no-sts            # 禁用 STS 虚拟串口

LeRobot / scservo_sdk 使用 (每个臂自动分配端口):
    Follower dev=228 -> socket://localhost:6560
    Leader   dev=148 -> socket://localhost:6561
    端口号在控制台实时输出，设备上线即分配

虚拟串口桥接 (--bridge / --no-web):
    FD 软件可通过 COM51 等虚拟串口直接控制远程舵机
    Windows: 需预装 com0com  |  Linux/macOS: 需安装 socat

依赖：
    pip install pyserial websockets

模块拆分 (PyArmor 加密兼容):
    datastore.py  - DataStore 类 + 全局共享状态
    serial_io.py  - 串口工具 + 读取线程 + serial_write_cmd
    ws_server.py  - WebSocket + HTTP 服务
    sts_server.py - STS 协议虚拟串口 TCP 服务
    vcom_bridge.py - com0com/socat 虚拟串口桥接
    gateway.py    - 本文件，入口 main()
"""

import argparse
import asyncio
import threading
import time
import webbrowser

# --- 子模块 ---
from box2driver.datastore import store
import box2driver.datastore as _ds
from box2driver.serial_io import (
    list_ports,
    find_gateway_port,
    serial_reader,
    serial_write_cmd,
)
from box2driver.ws_server import (
    start_ws_server,
    start_http_server,
)
import box2driver.sts_server as _sts_mod
from box2driver.sts_server import STSPortManager
import box2driver.vcom_bridge as _vcom_mod
from box2driver.vcom_bridge import VirtualSerialBridge


def _register_callbacks():
    """将 serial_write_cmd 注册到 datastore 回调槽，供 keepalive/replay 使用。"""
    _ds._serial_write_func = serial_write_cmd


# ============================================
# === Main
# ============================================

def main():
    _register_callbacks()

    parser = argparse.ArgumentParser(
        description="Box2Driver Gateway Web Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python gateway_dashboard.py                      自动检测 CP210x 串口，启动 Web + STS
  python gateway_dashboard.py -p COM5              指定串口
  python gateway_dashboard.py --bridge             同时启动虚拟串口桥接 (com0com/socat)
  python gateway_dashboard.py --no-web             不启动 Web，只启动串口+WS+虚拟串口
  python gateway_dashboard.py --list               列出可用串口
        """,
    )
    parser.add_argument("-p", "--serial-port", help="串口名称 (如 COM5)")
    parser.add_argument("-b", "--baud", type=int, default=921600, help="波特率 (默认 921600)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 端口 (默认 8080)")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket 端口 (默认 8765)")
    parser.add_argument("--list", action="store_true", help="列出可用串口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--sts-base-port", type=int, default=6560, help="STS 虚拟串口起始端口 (默认 6560, 每设备+1)")
    parser.add_argument("--no-sts", action="store_true", help="禁用 STS 虚拟串口服务")
    parser.add_argument("--bridge", action="store_true", help="同时启动虚拟串口桥接 (com0com/socat -> STS TCP)")
    parser.add_argument("--no-web", action="store_true", help="不启动 Web 服务，只启动串口+WS+虚拟串口")
    args = parser.parse_args()

    if args.list:
        list_ports()
        return

    # --no-web 隐含 --no-browser 和 --bridge
    if args.no_web:
        args.no_browser = True
        args.bridge = True

    port = args.serial_port
    if not port:
        port = find_gateway_port(baudrate=args.baud)
        if not port:
            print("未能自动检测到串口，请使用 -p 指定")
            list_ports()
            return

    mode_label = "Virtual Servo Bridge" if args.no_web else "Gateway Web Dashboard"
    print()
    print("=" * 60)
    print(f"  Box2Driver {mode_label}")
    print(f"  Serial: {port} @ {args.baud}")
    if not args.no_web:
        print(f"  HTTP:   http://localhost:{args.port}")
    print(f"  WS:     ws://localhost:{args.ws_port}")
    if not args.no_sts:
        print(f"  STS:    每设备独立端口, base={args.sts_base_port}  (LeRobot/feetech-servo-sdk)")
    if args.bridge:
        print(f"  Bridge: 虚拟串口桥接已启用 (com0com/socat -> STS TCP)")
    print("=" * 60)
    print()

    # 0. GW_CONTROL keep-alive 线程
    store.start_keepalive()

    # 1. 串口读取线程
    t_serial = threading.Thread(target=serial_reader, args=(port, args.baud), daemon=True)
    t_serial.start()

    # 2. HTTP 服务线程 (--no-web 时跳过)
    if not args.no_web:
        t_http = threading.Thread(target=start_http_server, args=(args.port, args.ws_port), daemon=True)
        t_http.start()

    # 3. STS 虚拟串口自动分配 (每设备独立端口)
    if not args.no_sts:
        _sts_mod._sts_manager = STSPortManager(base_port=args.sts_base_port)
        _sts_mod._sts_manager.start()

    # 4. 虚拟串口桥接 (com0com/socat -> STS TCP)
    if args.bridge and _sts_mod._sts_manager:
        _vcom_mod._vserial_bridge = VirtualSerialBridge(_sts_mod._sts_manager)
        _vcom_mod._vserial_bridge.start()

    # 5. 自动打开浏览器
    if not args.no_browser:
        time.sleep(1)
        url = f"http://localhost:{args.port}/?ws_port={args.ws_port}"
        print(f"[浏览器] 打开 {url}")
        webbrowser.open(url)

    # 6. WebSocket 服务（主线程）
    try:
        asyncio.run(start_ws_server(args.ws_port))
    except KeyboardInterrupt:
        print("\n\n--- 统计 ---")
        snap = store.get_snapshot()
        print(f"总包数: {snap['stats']['total']}")
        print(f"设备数: {snap['stats']['device_count']}")
        print(f"运行时间: {snap['stats']['uptime']:.1f}s")
        if _vcom_mod._vserial_bridge:
            _vcom_mod._vserial_bridge.cleanup()


if __name__ == "__main__":
    main()
