#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
DataStore: 全局数据存储 + 共享状态。

所有其他模块 import 此文件获取 store / g_serial / g_serial_lock。
本模块不 import 其他 box2driver 子模块，避免循环依赖。
"""

import threading
import time

# ============================================
# === 全局数据存储
# ============================================

class DataStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.devices = {}       # dev_id -> latest data
        self.trajectories = {}  # dev_id -> list of {t, servos:[{id,pos},...]}
        self.max_trajectory = 3600  # 保留最近 3600 个点 (~60s at 60Hz)
        self.ws_clients = set()
        self.total_packets = 0
        self.start_time = time.time()

        # Gateway 固件信息
        self.gw_info = {}  # {"fw": "0.4.2", "mac": "XX:XX:..."}

        # 录制状态
        self.recording = False
        self.record_start_time = 0
        self.record_dev_ids = []    # 录制目标设备
        self.record_data = {}       # dev_id -> [{"t": float, "servos": [...]}]
        self.saved_recordings = {}  # name -> {dev_id: [...]}

        # 回放状态
        self.replaying = False
        self.replay_thread = None

        # Gateway 控制保活
        self.gw_control_macs = set()  # 需要 keep-alive 的 Follower MAC
        self.gw_ctrl_last_active = {}  # mac -> 最后一次收到真实控制指令的时间戳
        self._keepalive_thread = None

    GW_CTRL_AUTO_RELEASE_S = 3.0  # 无控制指令超过此时间自动释放

    DEVICE_TTL = 5.0  # 设备超过 5 秒无数据视为离线

    def update(self, data):
        dev_id = data.get("dev", "?")
        with self.lock:
            self.total_packets += 1
            data["_pc_time"] = time.time()
            self.devices[dev_id] = data

            # 轨迹记录
            if dev_id not in self.trajectories:
                self.trajectories[dev_id] = []
            traj = self.trajectories[dev_id]
            point = {
                "t": data.get("t", 0),
                "pc_time": time.time(),
                "servos": data.get("servos", []),
                "role": data.get("role", -1),
                "seq": data.get("seq", 0),
            }
            traj.append(point)
            if len(traj) > self.max_trajectory:
                traj.pop(0)

            # 录制中：追加到录制缓冲
            if self.recording:
                dev_str = str(dev_id)
                if not self.record_dev_ids or dev_str in self.record_dev_ids:
                    if dev_str not in self.record_data:
                        self.record_data[dev_str] = []
                    self.record_data[dev_str].append({
                        "t": time.time() - self.record_start_time,
                        "servos": [{"id": s["id"], "pos": s["pos"]} for s in data.get("servos", [])],
                    })

    def get_snapshot(self):
        with self.lock:
            now = time.time()
            # 只返回近期活跃的设备，过滤掉已离线的缓存
            live = {k: v for k, v in self.devices.items()
                    if now - v.get("_pc_time", 0) < self.DEVICE_TTL}
            snap = {
                "devices": live,
                "stats": {
                    "total": self.total_packets,
                    "uptime": now - self.start_time,
                    "device_count": len(live),
                },
            }
            if self.gw_info:
                snap["gw_info"] = self.gw_info
            return snap

    def get_trajectories(self):
        with self.lock:
            result = {}
            for dev_id, traj in self.trajectories.items():
                result[dev_id] = traj[-self.max_trajectory:]
            return result

    # ---- 录制 ----

    def start_recording(self, dev_ids=None):
        with self.lock:
            self.recording = True
            self.record_start_time = time.time()
            self.record_dev_ids = [str(d) for d in dev_ids] if dev_ids else []
            self.record_data = {}
        devs = ", ".join(self.record_dev_ids) if self.record_dev_ids else "all"
        print(f"[录制] 开始 (设备: {devs})")

    def stop_recording(self, name=None):
        with self.lock:
            self.recording = False
            data = dict(self.record_data)
            total = sum(len(v) for v in data.values())
            duration = time.time() - self.record_start_time if self.record_start_time else 0
            if name:
                self.saved_recordings[name] = data
            self.record_data = {}
        print(f"[录制] 停止: {len(data)} 设备, {total} 帧, {duration:.1f}s")
        return {"name": name, "data": data, "duration": duration, "frames": total}

    def get_recording_list(self):
        with self.lock:
            return {
                name: {
                    "devices": list(data.keys()),
                    "frames": sum(len(v) for v in data.values()),
                }
                for name, data in self.saved_recordings.items()
            }

    def get_recording(self, name):
        with self.lock:
            return self.saved_recordings.get(name)

    # ---- 回放 ----

    def start_replay(self, name, target_dev=None, loops=1, speed=1.0, mac=None):
        """启动回放线程。
        target_dev: 回放数据来源设备 ID（取该设备的轨迹发送控制）
        mac: 目标 Follower MAC（指定单播目标，None=广播）
        """
        recording = self.get_recording(name)
        if not recording:
            return False

        # 选择轨迹数据源
        if target_dev and str(target_dev) in recording:
            traj = recording[str(target_dev)]
        elif len(recording) == 1:
            traj = list(recording.values())[0]
        else:
            traj = list(recording.values())[0]

        self._replay_target_mac = mac  # 保存目标 MAC 给回放线程用

        if not traj:
            return False

        self.stop_replay()  # 停止之前的回放
        self.replaying = True

        def _replay_worker():
            loop_count = 0
            try:
                while self.replaying:
                    loop_count += 1
                    t0 = time.time()
                    for frame in traj:
                        if not self.replaying:
                            break
                        target_t = frame["t"] / speed
                        while (time.time() - t0) < target_t and self.replaying:
                            time.sleep(0.005)
                        if not self.replaying:
                            break
                        cmd = {"cmd": "sync", "servos": frame["servos"]}
                        if self._replay_target_mac:
                            cmd["mac"] = self._replay_target_mac
                        _serial_write_func(cmd)
                    if loops > 0 and loop_count >= loops:
                        break
            finally:
                self.replaying = False
                print(f"[回放] 结束: {loop_count} 次循环")

        self.replay_thread = threading.Thread(target=_replay_worker, daemon=True)
        self.replay_thread.start()
        print(f"[回放] 开始: {name}, loops={loops}, speed={speed}x")
        return True

    def stop_replay(self):
        self.replaying = False
        if self.replay_thread and self.replay_thread.is_alive():
            self.replay_thread.join(timeout=2)
        self.replay_thread = None

    def start_keepalive(self):
        """启动 GW_CONTROL keep-alive 线程，每 1s 回传 Follower 当前位置"""
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            return
        self._keepalive_thread = threading.Thread(target=self._keepalive_worker, daemon=True)
        self._keepalive_thread.start()

    def _keepalive_worker(self):
        while True:
            time.sleep(0.8)
            macs_to_keepalive = set(self.gw_control_macs)
            if not macs_to_keepalive:
                continue
            now = time.time()
            # 自动释放：超过 3 秒没有收到真实控制指令的 MAC
            expired = [mac for mac in macs_to_keepalive
                       if now - self.gw_ctrl_last_active.get(mac, 0) > self.GW_CTRL_AUTO_RELEASE_S]
            for mac in expired:
                self.gw_control_macs.discard(mac)
                self.gw_ctrl_last_active.pop(mac, None)
                _serial_write_func({"cmd": "ctrl_release", "mac": mac})
                print(f"[GW_CTRL] 自动释放(超时{self.GW_CTRL_AUTO_RELEASE_S}s无指令): {mac[-5:]}")
            # 只对仍然活跃的 MAC 发心跳
            macs_to_keepalive -= set(expired)
            if not macs_to_keepalive:
                continue
            with self.lock:
                for dev_id, data in self.devices.items():
                    mac = data.get("mac")
                    if mac not in macs_to_keepalive:
                        continue
                    if data.get("role") != 0:
                        continue
                    cmd = {"cmd": "sync", "servos": [], "mac": mac}
                    _serial_write_func(cmd)
                    macs_to_keepalive.discard(mac)
            for mac in macs_to_keepalive:
                cmd = {"cmd": "sync", "servos": [], "mac": mac}
                _serial_write_func(cmd)


store = DataStore()

# 全局串口对象（读写共享）
g_serial = None
g_serial_lock = threading.Lock()

# ---- 回调槽 ----
# serial_io.py 设置 _serial_write_func，ws_server.py 设置 _ws_broadcast_func
# 避免模块间循环 import

def _noop_serial_write(cmd_dict):
    """占位: serial_write_cmd 尚未注册"""
    return False

def _noop_ws_broadcast(data):
    """占位: broadcast_ws 尚未注册"""
    pass

_serial_write_func = _noop_serial_write
_ws_broadcast_func = _noop_ws_broadcast
