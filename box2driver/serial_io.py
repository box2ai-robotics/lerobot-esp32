#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
串口工具 + 串口读取线程 + serial_write_cmd。

依赖: datastore (store, g_serial, g_serial_lock)
通过 datastore._ws_broadcast_func 回调广播 WebSocket 消息。
"""

import asyncio
import json
import time

import serial
import serial.tools.list_ports

from box2driver.datastore import (
    store,
    g_serial_lock,
)
import box2driver.datastore as _ds


# ============================================
# === 串口工具
# ============================================

def list_ports():
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("未发现任何串口设备")
        return []
    print(f"发现 {len(ports)} 个串口:")
    for p in ports:
        print(f"  {p.device:10s}  {p.description}")
    return ports


def probe_esp32_port(port_device, baudrate=921600, timeout=2.0):
    """尝试打开串口并检测是否为 ESP32 Gateway（能收到 JSON 或可识别的启动信息）"""
    try:
        ser = serial.Serial(port_device, baudrate, timeout=0.3)
        ser.dtr = False
        ser.rts = False
        time.sleep(0.2)
        ser.reset_input_buffer()
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue
            # 检测 Gateway JSON 数据流
            if line.startswith("{") and '"dev"' in line:
                ser.close()
                return True
            # 检测 ESP32 启动日志关键字
            if any(kw in line for kw in ["Box2Driver", "ESP-NOW", "JSON_START", "MAIN", "Gateway"]):
                ser.close()
                return True
        ser.close()
    except Exception:
        pass
    return False


def find_gateway_port(baudrate=921600):
    ports = serial.tools.list_ports.comports()
    candidates = []
    cp210x_ports = []
    for p in ports:
        desc = (p.description or "").lower()
        vid = p.vid or 0
        # ESP32 常见 USB 芯片: CP210x (VID 0x10C4), CH340 (VID 0x1A86), FTDI (VID 0x0403)
        esp32_vid = vid in (0x10C4, 0x1A86, 0x0403)
        keyword_match = any(kw in desc for kw in [
            "cp210", "ch340", "ch910", "ftdi", "usb-serial", "usb serial",
            "silicon labs", "wch", "uart bridge",
        ])
        if esp32_vid or keyword_match:
            candidates.append(p)
            # 优先匹配 CP210x (ESP32 最常用的 USB-UART 芯片)
            if "cp210" in desc or "silicon labs" in desc or vid == 0x10C4:
                cp210x_ports.append(p)

    if len(candidates) == 0:
        return None

    # 优先: 只有一个 CP210x -> 直接选
    if len(cp210x_ports) == 1:
        port = cp210x_ports[0].device
        print(f"检测到 CP210x 串口: {port}  ({cp210x_ports[0].description})")
        return port

    # 只有一个候选 -> 直接选
    if len(candidates) == 1:
        port = candidates[0].device
        print(f"检测到 ESP32 串口: {port}  ({candidates[0].description})")
        return port

    # 多个 CP210x -> 只在 CP210x 里选
    if len(cp210x_ports) > 1:
        print(f"发现 {len(cp210x_ports)} 个 CP210x 串口，正在探测 ESP32 Gateway...")
        for p in cp210x_ports:
            print(f"  探测 {p.device:10s}  {p.description} ...", end=" ", flush=True)
            if probe_esp32_port(p.device, baudrate, timeout=2.0):
                print("Gateway!")
                return p.device
            else:
                print("跳过")
        # 探测都没中，让用户选
        print("\n检测到多个 CP210x 串口，请选择:")
        for i, p in enumerate(cp210x_ports):
            print(f"  [{i}] {p.device:10s}  {p.description}")
        choice = input("请选择序号 (默认 0): ").strip()
        idx = int(choice) if choice.isdigit() else 0
        if 0 <= idx < len(cp210x_ports):
            return cp210x_ports[idx].device
        return None

    # 多个候选 (混合芯片)：逐个探测，找到 Gateway 数据流的那个
    print(f"发现 {len(candidates)} 个候选串口，正在探测 ESP32 Gateway...")
    for p in candidates:
        print(f"  探测 {p.device:10s}  {p.description} ...", end=" ", flush=True)
        if probe_esp32_port(p.device, baudrate, timeout=2.0):
            print("Gateway!")
            return p.device
        else:
            print("跳过")

    # 探测都没中，让用户手动选
    print("\n未自动识别到 Gateway，请手动选择:")
    for i, p in enumerate(candidates):
        print(f"  [{i}] {p.device:10s}  {p.description}")
    choice = input("请选择序号 (默认 0): ").strip()
    idx = int(choice) if choice.isdigit() else 0
    if 0 <= idx < len(candidates):
        return candidates[idx].device
    return None


# ============================================
# === 串口写入
# ============================================

def serial_write_cmd(cmd_dict):
    """线程安全地向串口写入控制命令 JSON"""
    if _ds.g_serial is None or not _ds.g_serial.is_open:
        print("[控制] 串口未连接，命令丢弃")
        return False
    try:
        t0 = time.perf_counter()
        line = json.dumps(cmd_dict, ensure_ascii=False, separators=(',', ':')) + "\n"
        with g_serial_lock:
            t_lock = time.perf_counter()
            _ds.g_serial.write(line.encode("utf-8"))
            _ds.g_serial.flush()
        t_done = time.perf_counter()
        lock_ms = (t_lock - t0) * 1000
        write_ms = (t_done - t_lock) * 1000
        total_ms = (t_done - t0) * 1000
        serial_write_cmd._count = getattr(serial_write_cmd, '_count', 0) + 1
        if serial_write_cmd._count % 30 == 1 or total_ms > 10:
            print(f"[T-SER_W] #{serial_write_cmd._count} lock={lock_ms:.1f}ms "
                  f"write={write_ms:.1f}ms total={total_ms:.1f}ms "
                  f"len={len(line)}B cmd={line.strip()[:60]}")
        return True
    except Exception as e:
        print(f"[串口写入错误] {e}")
        return False


# ============================================
# === 串口读取线程
# ============================================

def _try_open_serial(port, baudrate):
    """尝试打开指定串口，成功返回 Serial 对象，失败返回 None"""
    try:
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = baudrate
        ser.timeout = 0
        ser.dtr = False
        ser.rts = False
        ser.open()
        time.sleep(0.3)
        ser.reset_input_buffer()
        return ser
    except Exception:
        return None


def _find_gateway_port_silent(baudrate):
    """静默版 find_gateway_port：不提示用户选择，找不到就返回 None"""
    try:
        ports = serial.tools.list_ports.comports()
    except Exception:
        return None
    candidates = []
    cp210x_ports = []
    for p in ports:
        desc = (p.description or "").lower()
        vid = p.vid or 0
        esp32_vid = vid in (0x10C4, 0x1A86, 0x0403)
        keyword_match = any(kw in desc for kw in [
            "cp210", "ch340", "ch910", "ftdi", "usb-serial", "usb serial",
            "silicon labs", "wch", "uart bridge",
        ])
        if esp32_vid or keyword_match:
            candidates.append(p)
            if "cp210" in desc or "silicon labs" in desc or vid == 0x10C4:
                cp210x_ports.append(p)
    if len(cp210x_ports) == 1:
        return cp210x_ports[0].device
    if len(candidates) == 1:
        return candidates[0].device
    # 多个候选：逐个探测
    probe_list = cp210x_ports if cp210x_ports else candidates
    for p in probe_list:
        if probe_esp32_port(p.device, baudrate, timeout=2.0):
            return p.device
    return None


def _notify_serial_status(status, port=None, detail=""):
    """通过 WebSocket 广播串口连接状态变化"""
    from box2driver.ws_server import broadcast_ws, ws_loop
    msg = {"type": "serial_status", "status": status}
    if port:
        msg["port"] = port
    if detail:
        msg["detail"] = detail
    if ws_loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(broadcast_ws(msg), ws_loop)
        except Exception:
            pass


def serial_reader(port, baudrate):
    from box2driver.ws_server import broadcast_ws, ws_loop as _ws_loop_ref
    import box2driver.ws_server as _ws_mod

    original_port = port  # 用户指定或首次检测到的端口
    reconnect_interval = 2  # 重连间隔 (秒)

    while True:  # ====== 外层重连循环 ======
        # --- 尝试打开串口 ---
        print(f"\n[串口] 连接 {port} @ {baudrate}...")
        ser = _try_open_serial(port, baudrate)
        if ser is None:
            # 指定端口打开失败 -> 尝试自动扫描
            print(f"[串口] 无法打开 {port}，尝试自动扫描...")
            _notify_serial_status("disconnected", port, "端口打开失败，扫描中...")
            scanned = _find_gateway_port_silent(baudrate)
            if scanned and scanned != port:
                print(f"[串口] 扫描到新端口: {scanned}")
                port = scanned
                ser = _try_open_serial(port, baudrate)
            if ser is None:
                print(f"[串口] 连接失败，{reconnect_interval}s 后重试...")
                _notify_serial_status("disconnected", port, f"{reconnect_interval}s 后重试")
                time.sleep(reconnect_interval)
                continue

        _ds.g_serial = ser
        print(f"[串口] 已连接: {port}（DTR/RTS 已禁用）")
        _notify_serial_status("connected", port)

        # --- 等待 JSON_START 或自动检测 JSON 数据 ---
        print("[串口] 等待 Gateway 就绪...")
        json_started = False
        t0 = time.time()
        ser.timeout = 0.1
        try:
            while not json_started and time.time() - t0 < 10:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if "---JSON_START---" in line:
                    json_started = True
                    print("  收到 JSON_START 标记")
                elif line.startswith("{") and '"dev"' in line:
                    json_started = True
                    print(f"  自动检测到 JSON 数据流，跳过等待")
                    try:
                        data = json.loads(line)
                        if "dev" in data and "servos" in data:
                            store.update(data)
                    except json.JSONDecodeError:
                        pass
                else:
                    print(f"  [BOOT] {line}")
        except (serial.SerialException, OSError):
            print(f"[串口] 等待期间断开连接")
            _ds.g_serial = None
            _notify_serial_status("disconnected", port, "等待期间断开")
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(reconnect_interval)
            continue
        if not json_started:
            print("  未收到 JSON_START，继续读取...")

        # --- 主数据读取循环 ---
        ser.timeout = 0
        print("[串口] 数据读取中...")
        buf = b""
        _ser_bytes = 0
        _ser_lines = 0
        _ser_t0 = time.perf_counter()
        consecutive_errors = 0

        while True:
            try:
                waiting = ser.in_waiting
                if waiting == 0:
                    time.sleep(0.001)
                    _now = time.perf_counter()
                    if _now - _ser_t0 > 5.0:
                        rate = _ser_bytes / (_now - _ser_t0)
                        print(f"[T-SER_IN] {rate:.0f} B/s  {_ser_lines} lines/5s  "
                              f"ws_clients={len(store.ws_clients)} "
                              f"gw_ctrl={list(store.gw_control_macs)}")
                        _ser_bytes = 0
                        _ser_lines = 0
                        _ser_t0 = _now
                    continue
                chunk = ser.read(waiting)
                _ser_bytes += len(chunk)
                buf += chunk
                consecutive_errors = 0  # 成功读取，重置错误计数

                # 按行分割处理
                while b"\n" in buf:
                    line_bytes, buf = buf.split(b"\n", 1)
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "gw_info" in data:
                        store.gw_info = data["gw_info"]
                        print(f"[Gateway] 固件 v{store.gw_info.get('fw', '?')}  MAC={store.gw_info.get('mac', '?')}")
                        if _ws_mod.ws_loop is not None:
                            asyncio.run_coroutine_threadsafe(broadcast_ws(data), _ws_mod.ws_loop)
                        continue
                    if "neighbors" in data:
                        if _ws_mod.ws_loop is not None:
                            asyncio.run_coroutine_threadsafe(broadcast_ws(data), _ws_mod.ws_loop)
                        continue
                    if "joycon" in data:
                        jc = data["joycon"]
                        jc_dev_id = f"JC-{jc.get('dev', '?')}"
                        jc_store = {
                            "dev": jc_dev_id, "role": 4, "mac": "JoyCon",
                            "seq": jc.get("seq", 0), "t": jc.get("t", 0),
                            "joycon_pose": {
                                "x": jc.get("x", 0), "y": jc.get("y", 0), "z": jc.get("z", 0),
                                "roll": jc.get("r", 0), "pitch": jc.get("p", 0), "yaw": jc.get("yaw", 0),
                                "buttons": jc.get("btn", 0),
                                "stick_x": jc.get("sx", 0), "stick_y": jc.get("sy", 0),
                            },
                            "servos": [],
                        }
                        with store.lock:
                            store.total_packets += 1
                            store.devices[jc_dev_id] = jc_store
                        if _ws_mod.ws_loop is not None:
                            asyncio.run_coroutine_threadsafe(broadcast_ws(data), _ws_mod.ws_loop)
                        continue
                    if "dev" not in data or "servos" not in data:
                        if "dbg" in data or "ack" in data:
                            print(f"[ESP32] {line.strip()[:120]}")
                            if _ws_mod.ws_loop is not None:
                                asyncio.run_coroutine_threadsafe(broadcast_ws(data), _ws_mod.ws_loop)
                        continue
                    store.update(data)
                    _ser_lines += 1
                    if _ws_mod.ws_loop is not None:
                        asyncio.run_coroutine_threadsafe(broadcast_ws(data), _ws_mod.ws_loop)

                # 防止缓冲区无限增长
                if len(buf) > 2048:
                    buf = buf[-1024:]

            except (serial.SerialException, OSError) as e:
                # USB 拔出 / 设备 reset -> 跳出内层循环，触发重连
                print(f"\n[串口] 连接断开: {e}")
                _ds.g_serial = None
                _notify_serial_status("disconnected", port, str(e))
                try:
                    ser.close()
                except Exception:
                    pass
                break  # -> 外层 while True 重连

            except Exception as e:
                consecutive_errors += 1
                print(f"[串口错误] {e}")
                buf = b""
                if consecutive_errors >= 10:
                    # 连续错误过多，可能端口已不可用
                    print(f"[串口] 连续 {consecutive_errors} 次错误，尝试重连...")
                    _ds.g_serial = None
                    _notify_serial_status("disconnected", port, "连续错误过多")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    break  # -> 外层 while True 重连
                time.sleep(0.5)

        # 内层 break 出来，等一会儿再重连
        print(f"[串口] {reconnect_interval}s 后尝试重连...")
        time.sleep(reconnect_interval)
