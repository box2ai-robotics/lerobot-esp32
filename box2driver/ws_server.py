#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
WebSocket 服务 + HTTP 服务（提供 dashboard.html）。

依赖: datastore (store), serial_io (serial_write_cmd)
"""

import asyncio
import json
import os
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import websockets
from websockets.asyncio.server import serve as ws_serve

from box2driver.datastore import store
import box2driver.datastore as _ds
from box2driver.serial_io import serial_write_cmd


# ============================================
# === WebSocket 服务
# ============================================

ws_loop = None


_bc_count = 0
_bc_slow_count = 0

async def broadcast_ws(data):
    global _bc_count, _bc_slow_count
    if store.ws_clients:
        t0 = time.perf_counter()
        msg = json.dumps(data, ensure_ascii=False)
        dead = set()
        for ws in store.ws_clients.copy():
            try:
                # 100ms 超时，避免慢客户端阻塞整个广播
                await asyncio.wait_for(ws.send(msg), timeout=0.1)
            except (Exception, asyncio.TimeoutError):
                dead.add(ws)
        store.ws_clients -= dead
        dt = (time.perf_counter() - t0) * 1000
        _bc_count += 1
        if dt > 10:
            _bc_slow_count += 1
            print(f"[T-BROADCAST] SLOW {dt:.1f}ms  clients={len(store.ws_clients)} "
                  f"dead={len(dead)} msg_len={len(msg)} slow_total={_bc_slow_count}/{_bc_count}")


async def ws_handler(websocket):
    # 延迟导入避免循环（sts_server 在 gateway.py 中初始化后才有值）
    import box2driver.sts_server as _sts_mod

    store.ws_clients.add(websocket)
    remote = websocket.remote_address
    print(f"[WS] 客户端连接: {remote}")
    ws_ctrl_macs = set()  # 追踪本 WS 客户端激活的 GW_CONTROL MAC
    try:
        # 发送初始快照（含 STS 端口信息）
        snapshot = store.get_snapshot()
        if _sts_mod._sts_manager:
            snapshot["sts_ports"] = _sts_mod._sts_manager.get_port_table()
        await websocket.send(json.dumps({"type": "snapshot", "data": snapshot}))
        # 保持连接
        async for msg in websocket:
            try:
                req = json.loads(msg)
                if req.get("type") == "get_trajectories":
                    traj = store.get_trajectories()
                    await websocket.send(json.dumps({"type": "trajectories", "data": traj}))
                elif req.get("type") == "control":
                    # 控制命令：转发到 ESP32 Serial（可选 mac 字段指定目标 Follower）
                    t_ctrl = time.perf_counter()
                    servos = req.get("servos", [])
                    if servos:
                        cmd = {"cmd": "sync", "servos": servos}
                        mac = req.get("mac")
                        if mac:
                            cmd["mac"] = mac  # 单播到指定 Follower
                            # 自动激活 GW_CONTROL keep-alive（任何控制来源都会保活）
                            store.gw_control_macs.add(mac)
                            store.gw_ctrl_last_active[mac] = time.time()
                            ws_ctrl_macs.add(mac)
                        ok = serial_write_cmd(cmd)
                        dt_ctrl = (time.perf_counter() - t_ctrl) * 1000
                        store._ctrl_count = getattr(store, '_ctrl_count', 0) + 1
                        if store._ctrl_count % 30 == 1 or dt_ctrl > 10:
                            print(f"[T-WS_CTRL] #{store._ctrl_count} {dt_ctrl:.1f}ms "
                                  f"servos={len(servos)} mac={mac[-5:] if mac else 'N/A'}")
                        if not ok:
                            await websocket.send(json.dumps({"type": "error", "msg": "serial not connected"}))
                elif req.get("type") == "torque":
                    # 单舵机力矩控制
                    cmd = {"cmd": "torque", "id": req.get("id", 0), "enable": req.get("enable", 0)}
                    serial_write_cmd(cmd)
                elif req.get("type") == "torque_all":
                    # 全部力矩控制
                    cmd = {"cmd": "torque_all", "enable": req.get("enable", 0)}
                    serial_write_cmd(cmd)

                elif req.get("type") == "ctrl_release":
                    # 释放 Gateway 对 Follower 的控制（可选 mac 字段）
                    cmd = {"cmd": "ctrl_release"}
                    mac = req.get("mac")
                    if mac:
                        cmd["mac"] = mac
                        store.gw_control_macs.discard(mac)  # 停止 keep-alive
                    else:
                        store.gw_control_macs.clear()  # 无 mac = 释放全部
                    serial_write_cmd(cmd)

                elif req.get("type") == "request_control":
                    # 激活 GW_CONTROL keep-alive（不发 sync，避免舵机跳位）
                    mac = req.get("mac")
                    if mac:
                        store.gw_control_macs.add(mac)
                        store.gw_ctrl_last_active[mac] = time.time()
                        ws_ctrl_macs.add(mac)
                        print(f"[GW_CTRL] keep-alive 已激活: {mac[-5:]}")

                # ---- 远程设备控制命令 ----
                elif req.get("type") == "set_mode":
                    # 远程切换设备模式：{"type":"set_mode","mac":"AA:BB:...","mode":1}
                    cmd = {"cmd": "set_mode", "mode": req.get("mode", 0)}
                    mac = req.get("mac")
                    if mac:
                        cmd["mac"] = mac
                    serial_write_cmd(cmd)

                elif req.get("type") == "bind_accept":
                    # 远程接受 Follower 绑定：{"type":"bind_accept","mac":"AA:BB:..."}
                    cmd = {"cmd": "bind_accept"}
                    mac = req.get("mac")
                    if mac:
                        cmd["mac"] = mac
                    serial_write_cmd(cmd)

                elif req.get("type") == "bind_reject":
                    # 远程拒绝 Follower 绑定：{"type":"bind_reject","mac":"AA:BB:..."}
                    cmd = {"cmd": "bind_reject"}
                    mac = req.get("mac")
                    if mac:
                        cmd["mac"] = mac
                    serial_write_cmd(cmd)

                # ---- 固件更新命令 ----
                elif req.get("type") == "check_fw_update":
                    # 检查固件更新：比较当前版本和 VERSION 文件
                    current = store.gw_info.get("fw", "") if store.gw_info else ""
                    latest = ""
                    try:
                        ver_file = Path(__file__).parent.parent / "VERSION"
                        if ver_file.exists():
                            latest = ver_file.read_text().strip()
                    except Exception:
                        pass
                    await websocket.send(json.dumps({
                        "type": "fw_update_info",
                        "current": current,
                        "latest": latest or current,
                    }))

                elif req.get("type") == "flash_firmware":
                    # USB 烧录固件：接收 base64 bin 数据 -> 写入临时文件 -> 调用 esptool
                    import base64
                    import tempfile
                    filename = req.get("filename", "firmware.bin")
                    data_b64 = req.get("data", "")
                    size = req.get("size", 0)
                    try:
                        fw_data = base64.b64decode(data_b64)
                        if len(fw_data) != size:
                            raise ValueError(f"大小不匹配: 期望 {size}, 实际 {len(fw_data)}")
                        # 写入临时文件
                        tmp = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
                        tmp.write(fw_data)
                        tmp_path = tmp.name
                        tmp.close()
                        await websocket.send(json.dumps({
                            "type": "flash_progress", "status": "flashing",
                            "progress": 10, "message": f"已接收 {len(fw_data)//1024} KB，准备烧录...",
                        }))
                        # 获取串口号
                        port = _ds.g_serial.port if _ds.g_serial and _ds.g_serial.is_open else None
                        if not port:
                            raise RuntimeError("串口未连接，无法烧录")
                        # 关闭串口（esptool 需要独占）
                        if _ds.g_serial and _ds.g_serial.is_open:
                            _ds.g_serial.close()
                        await websocket.send(json.dumps({
                            "type": "flash_progress", "status": "flashing",
                            "progress": 20, "message": f"烧录到 {port}...",
                        }))
                        # 调用 esptool
                        import subprocess as sp
                        esptool_cmd = [
                            sys.executable, "-m", "esptool",
                            "--port", port, "--baud", "921600",
                            "write_flash", "0x10000", tmp_path,
                        ]
                        result = sp.run(esptool_cmd, capture_output=True, text=True, timeout=120)
                        os.unlink(tmp_path)
                        if result.returncode == 0:
                            await websocket.send(json.dumps({
                                "type": "flash_progress", "status": "done",
                                "progress": 100, "message": "烧录完成！设备将自动重启。",
                            }))
                            # 等待 ESP32 重启后重新打开串口
                            time.sleep(3)
                            try:
                                _ds.g_serial.open()
                            except Exception:
                                pass
                        else:
                            err_msg = result.stderr[-200:] if result.stderr else "未知错误"
                            await websocket.send(json.dumps({
                                "type": "flash_progress", "status": "error",
                                "message": f"烧录失败: {err_msg}",
                            }))
                            # 重新打开串口
                            try:
                                _ds.g_serial.open()
                            except Exception:
                                pass
                    except Exception as e:
                        await websocket.send(json.dumps({
                            "type": "flash_progress", "status": "error",
                            "message": f"错误: {e}",
                        }))
                        # 确保串口恢复
                        if _ds.g_serial and not _ds.g_serial.is_open:
                            try:
                                _ds.g_serial.open()
                            except Exception:
                                pass

                # ---- 录制命令 ----
                elif req.get("type") == "record_start":
                    dev_ids = req.get("dev_ids", None)
                    store.start_recording(dev_ids)
                    await websocket.send(json.dumps({"type": "record_status", "recording": True}))

                elif req.get("type") == "record_stop":
                    name = req.get("name", f"rec_{int(time.time())}")
                    result = store.stop_recording(name)
                    await websocket.send(json.dumps({
                        "type": "record_result",
                        "name": name,
                        "duration": result["duration"],
                        "frames": result["frames"],
                        "data": result["data"],
                    }))

                elif req.get("type") == "record_list":
                    recordings = store.get_recording_list()
                    await websocket.send(json.dumps({
                        "type": "record_list",
                        "recordings": recordings,
                        "recording": store.recording,
                        "replaying": store.replaying,
                    }))

                # ---- 回放命令 ----
                elif req.get("type") == "replay_start":
                    name = req.get("name")
                    loops = req.get("loops", 1)        # 0=无限
                    speed = req.get("speed", 1.0)
                    target_dev = req.get("target_dev", None)
                    mac = req.get("mac", None)  # 目标 Follower MAC
                    ok = store.start_replay(name, target_dev, loops, speed, mac=mac)
                    await websocket.send(json.dumps({
                        "type": "replay_status", "replaying": ok, "name": name,
                    }))

                elif req.get("type") == "replay_stop":
                    store.stop_replay()
                    await websocket.send(json.dumps({"type": "replay_status", "replaying": False}))

                # ---- STS 虚拟串口端口查询 ----
                elif req.get("type") == "sts_ports":
                    if _sts_mod._sts_manager:
                        table = _sts_mod._sts_manager.get_port_table()
                        config = _sts_mod._sts_manager.get_full_config()
                    else:
                        table, config = {}, {}
                    await websocket.send(json.dumps({
                        "type": "sts_ports", "online": table, "config": config,
                    }))

                # ---- 删除录制 ----
                elif req.get("type") == "record_delete":
                    name = req.get("name", "")
                    if name:
                        with store.lock:
                            store.saved_recordings.pop(name, None)
                        # 同时删除磁盘文件
                        rec_dir = Path(__file__).parent.parent / "recordings"
                        rec_file = rec_dir / f"{name}.json"
                        if rec_file.exists():
                            rec_file.unlink()
                            print(f"[REC] 已删除文件: {rec_file}")
                        print(f"[REC] 已删除录制: {name}")

                # ---- 录制数据导入 ----
                elif req.get("type") == "record_import":
                    name = req.get("name", f"import_{int(time.time())}")
                    data = req.get("data", {})
                    if data:
                        with store.lock:
                            store.saved_recordings[name] = data
                        total = sum(len(v) for v in data.values())
                        await websocket.send(json.dumps({
                            "type": "record_import_ok", "name": name, "frames": total,
                        }))

            except Exception as e:
                print(f"[WS处理错误] {e}")
                import traceback; traceback.print_exc()
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        store.ws_clients.discard(websocket)
        # WS 客户端断开 -> 释放它激活的所有 GW_CONTROL
        if ws_ctrl_macs:
            for mac in ws_ctrl_macs:
                store.gw_control_macs.discard(mac)
                serial_write_cmd({"cmd": "ctrl_release", "mac": mac})
            print(f"[WS] 客户端断开: {remote}, 已释放 GW_CONTROL: "
                  f"{[m[-5:] for m in ws_ctrl_macs]}")
        else:
            print(f"[WS] 客户端断开: {remote}")


async def start_ws_server(ws_port):
    global ws_loop
    ws_loop = asyncio.get_event_loop()
    async with ws_serve(ws_handler, "0.0.0.0", ws_port):
        print(f"[WS] WebSocket 服务已启动: ws://localhost:{ws_port}")
        await asyncio.Future()  # run forever


# ============================================
# === HTTP 服务（提供 dashboard.html）
# ============================================

def start_http_server(http_port, ws_port):
    html_dir = Path(__file__).parent / "static"
    if not html_dir.exists():
        # Fallback: 同目录下找 (开发模式)
        html_dir = Path(__file__).parent
    os.chdir(html_dir)

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            # 提取路径部分（忽略查询字符串）
            path_only = self.path.split("?")[0]
            if path_only == "/" or path_only == "/index.html":
                # 保留查询字符串
                qs = self.path[len(path_only):]
                self.path = "/dashboard.html" + qs
            elif path_only == "/gateway_dashboard.html":
                qs = self.path[len(path_only):]
                self.path = "/dashboard.html" + qs
            return super().do_GET()

        def log_message(self, format, *args):
            pass  # 静默 HTTP 日志

    server = HTTPServer(("0.0.0.0", http_port), Handler)
    print(f"[HTTP] Dashboard: http://localhost:{http_port}")
    server.serve_forever()
