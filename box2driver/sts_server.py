#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
STS 协议虚拟串口 TCP 服务 (每个设备独立端口)。

每个在线机械臂自动分配一个 TCP 端口，模拟独立的飞特舵机串口总线。
LeRobot / scservo_sdk 通过 serial.Serial("socket://localhost:<port>") 连接。

端口分配:
  base_port + 0  -> 第一个 Follower (role=0)
  base_port + 1  -> 第一个 Leader  (role=1)
  base_port + N  -> 按发现顺序分配

数据流 (以 Follower 端口为例):
  scservo_sdk READ  -> 返回该 Follower 的 feedback 数据
  scservo_sdk WRITE -> serial_write_cmd(mac=该Follower的MAC) -> ESP-NOW -> 该 Follower

依赖: datastore (store), serial_io (serial_write_cmd)
"""

import json
import socket
import threading
import time
from collections import defaultdict
from pathlib import Path

from box2driver.datastore import store
from box2driver.serial_io import serial_write_cmd


# STS 协议常量
_INST_PING       = 0x01
_INST_READ       = 0x02
_INST_WRITE      = 0x03
_INST_REG_WRITE  = 0x04
_INST_ACTION     = 0x05
_INST_SYNC_READ  = 0x82
_INST_SYNC_WRITE = 0x83

# SMS_STS 内存地址
_ADDR_MODEL_L            = 3
_ADDR_TORQUE_ENABLE      = 40
_ADDR_GOAL_POSITION_L    = 42
_ADDR_PRESENT_POSITION_L = 56
_ADDR_PRESENT_POSITION_H = 57
_ADDR_PRESENT_SPEED_L    = 58
_ADDR_PRESENT_LOAD_L     = 60
_ADDR_PRESENT_VOLTAGE    = 62
_ADDR_PRESENT_TEMPERATURE = 63

_ROLE_NAMES = {0: "Follower", 1: "Leader", 2: "M-Leader", 3: "Gateway", 4: "JoyCon"}


def _sts_checksum(data: bytes) -> int:
    return (~sum(data)) & 0xFF


def _sts_status_packet(servo_id: int, error: int, data: bytes = b'') -> bytes:
    length = len(data) + 2
    pkt = bytes([servo_id, length, error]) + data
    return b'\xff\xff' + pkt + bytes([_sts_checksum(pkt)])


class _DevicePort:
    """一个设备对应的虚拟 STS 串口，绑定特定 dev_id / MAC"""

    # 真实 ST3215 EPROM 默认值 (从实际舵机抓取, Model=777=0x0309)
    # fmt: off
    _ST3215_EPROM = [
        0x03, 0x0A, 0x00,  # [0-2] fw_major, fw_minor, servo_ver
        0x09, 0x03, 0x00, 0x00, 0x00, 0x01,  # [3-8] model=ST3215, ID, baud, delay, resp
        0x00, 0x00, 0xFF, 0x0F,  # [9-12] min_angle=0, max_angle=4095
        0x46, 0x8C, 0x28,  # [13-15] max_temp=70, max_volt=140, min_volt=40
        0xE8, 0x03, 0x0C, 0x2C, 0x2F,  # [16-20] max_torque=1000, phase, unload, led
        0x20, 0x20, 0x00,  # [21-23] P=32, D=32, I=0
        0x10, 0x00, 0x01, 0x01,  # [24-27] min_startup=16, CW_dead, CCW_dead
        0x36, 0x01, 0x01,  # [28-30] prot_current=310, angular_res
        0x00, 0x00, 0x00, 0x14, 0xC8, 0x50, 0x0A, 0xC8, 0xC8,  # [31-39] offset..vel_I
        0x01, 0x00,  # [40-41] torque_enable=1, acceleration=0
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  # [42-47] goal_pos, goal_time, goal_speed
        0xE8, 0x03,  # [48-49] torque_limit=1000
        0x00, 0x00, 0x00, 0x00, 0x00, 0x01,  # [50-55] reserved + lock=1
    ]
    # fmt: on

    def __init__(self, dev_id, mac, role, tcp_port):
        self.dev_id = dev_id      # Gateway feedback 中的 dev 字段
        self.mac = mac            # 目标 MAC（用于定向发送控制命令）
        self.role = role
        self.tcp_port = tcp_port
        self.lock = threading.Lock()
        self._mem = defaultdict(lambda: defaultdict(int))
        self._known_ids = set()   # 实际存在的舵机 ID
        self._inited_ids = set()  # 已初始化 EPROM 的 ID
        self._reg_goals = {}
        self._server = None
        self._thread = None

    def _init_eprom(self, sid):
        """用真实 ST3215 EPROM 默认值初始化舵机内存（仅首次）"""
        if sid in self._inited_ids:
            return
        self._inited_ids.add(sid)
        mem = self._mem[sid]
        for i, v in enumerate(self._ST3215_EPROM):
            mem[i] = v
        mem[5] = sid  # 设置实际 ID

    def refresh(self):
        """从 store 刷新本设备的舵机数据"""
        with store.lock:
            data = store.devices.get(self.dev_id) or store.devices.get(str(self.dev_id))
        if not data:
            return
        with self.lock:
            for s in data.get("servos", []):
                sid = s.get("id", 0)
                pos = s.get("pos", 0)
                spd = s.get("spd", 0)
                load = s.get("load", 0)
                self._known_ids.add(sid)
                self._init_eprom(sid)
                mem = self._mem[sid]
                mem[_ADDR_PRESENT_POSITION_L] = pos & 0xFF
                mem[_ADDR_PRESENT_POSITION_H] = (pos >> 8) & 0xFF
                mem[_ADDR_PRESENT_SPEED_L] = spd & 0xFF
                mem[_ADDR_PRESENT_SPEED_L + 1] = (spd >> 8) & 0xFF
                mem[_ADDR_PRESENT_LOAD_L] = load & 0xFF
                mem[_ADDR_PRESENT_LOAD_L + 1] = (load >> 8) & 0xFF
                # goal_pos echo (addr 67-68, 真实舵机行为)
                goal = mem[_ADDR_GOAL_POSITION_L] | (mem[_ADDR_GOAL_POSITION_L + 1] << 8)
                if goal == 0:
                    mem[_ADDR_GOAL_POSITION_L] = pos & 0xFF
                    mem[_ADDR_GOAL_POSITION_L + 1] = (pos >> 8) & 0xFF
                mem[62] = s.get("volt", 125)   # voltage 12.5V
                mem[63] = s.get("temp", 36)    # temperature

    def read_bytes(self, servo_id, start_addr, length):
        self.refresh()
        with self.lock:
            mem = self._mem[servo_id]
            return bytes([mem.get(start_addr + i, 0) for i in range(length)])

    def write_bytes(self, servo_id, start_addr, data):
        with self.lock:
            mem = self._mem[servo_id]
            for i, b in enumerate(data):
                mem[start_addr + i] = b

    def send_control(self, servos):
        """发送控制命令，定向到本设备的 MAC"""
        cmd = {"cmd": "sync", "servos": servos}
        if self.mac:
            cmd["mac"] = self.mac
            # 激活 GW_CONTROL keep-alive，防止推理间隔超时导致 Follower 回退到 Leader
            store.gw_control_macs.add(self.mac)
            store.gw_ctrl_last_active[self.mac] = time.time()
        self._ctrl_count = getattr(self, '_ctrl_count', 0) + 1
        if self._ctrl_count <= 5 or self._ctrl_count % 30 == 0:
            role_name = _ROLE_NAMES.get(self.role, f"role{self.role}")
            servo_str = ', '.join(f"{s['id']}:{s['pos']}" for s in servos[:3])
            print(f"[STS_CTRL] #{self._ctrl_count} {role_name} dev={self.dev_id} "
                  f"mac={self.mac[-5:] if self.mac else 'N/A'} "
                  f"servos=[{servo_str}]")
        serial_write_cmd(cmd)

    def send_torque(self, servo_id, enable):
        cmd = {"cmd": "torque", "id": servo_id, "enable": 1 if enable else 0}
        if self.mac:
            cmd["mac"] = self.mac
        serial_write_cmd(cmd)

    def process_packet(self, servo_id, instruction, params):
        """处理一个 STS 协议包"""
        if instruction == _INST_PING:
            if servo_id == 0xFE:
                return b''
            self.refresh()
            if servo_id not in self._known_ids:
                return b''  # 未知 ID 不回复，FD 扫描时会跳过
            return _sts_status_packet(servo_id, 0)

        elif instruction == _INST_READ:
            if len(params) < 2:
                return _sts_status_packet(servo_id, 0x03)
            if servo_id != 0xFE and servo_id not in self._known_ids:
                self.refresh()
                if servo_id not in self._known_ids:
                    return b''  # unknown ID, no reply
            return _sts_status_packet(servo_id, 0, self.read_bytes(servo_id, params[0], params[1]))

        elif instruction == _INST_WRITE:
            if len(params) < 2:
                return _sts_status_packet(servo_id, 0x03)
            start_addr, data = params[0], params[1:]
            self.write_bytes(servo_id, start_addr, data)
            # 目标位置 -> 控制
            if start_addr <= _ADDR_GOAL_POSITION_L and start_addr + len(data) > _ADDR_GOAL_POSITION_L + 1:
                off = _ADDR_GOAL_POSITION_L - start_addr
                pos = data[off] | (data[off + 1] << 8)
                if servo_id != 0xFE:
                    self.send_control([{"id": servo_id, "pos": pos}])
            # 力矩
            if start_addr <= _ADDR_TORQUE_ENABLE and start_addr + len(data) > _ADDR_TORQUE_ENABLE:
                off = _ADDR_TORQUE_ENABLE - start_addr
                self.send_torque(servo_id, bool(data[off]))
            return _sts_status_packet(servo_id, 0) if servo_id != 0xFE else b''

        elif instruction == _INST_REG_WRITE:
            if len(params) < 2:
                return _sts_status_packet(servo_id, 0x03)
            start_addr, data = params[0], params[1:]
            self.write_bytes(servo_id, start_addr, data)
            if start_addr <= _ADDR_GOAL_POSITION_L and start_addr + len(data) > _ADDR_GOAL_POSITION_L + 1:
                off = _ADDR_GOAL_POSITION_L - start_addr
                self._reg_goals[servo_id] = data[off] | (data[off + 1] << 8)
            return _sts_status_packet(servo_id, 0) if servo_id != 0xFE else b''

        elif instruction == _INST_ACTION:
            goals = dict(self._reg_goals)
            self._reg_goals.clear()
            if goals:
                self.send_control([{"id": sid, "pos": p} for sid, p in goals.items()])
            return b''

        elif instruction == _INST_SYNC_WRITE:
            if len(params) < 2:
                return b''
            start_addr, data_len = params[0], params[1]
            payload = params[2:]
            chunk = 1 + data_len
            servos = []
            i = 0
            while i + chunk <= len(payload):
                sid = payload[i]
                sdata = payload[i + 1:i + chunk]
                i += chunk
                self.write_bytes(sid, start_addr, sdata)
                if start_addr <= _ADDR_GOAL_POSITION_L:
                    off = _ADDR_GOAL_POSITION_L - start_addr
                    if off + 1 < len(sdata):
                        servos.append({"id": sid, "pos": sdata[off] | (sdata[off + 1] << 8)})
            if servos:
                self.send_control(servos)
            return b''

        elif instruction == _INST_SYNC_READ:
            if len(params) < 2:
                return b''
            start_addr, read_len = params[0], params[1]
            resp = b''
            for sid in params[2:]:
                resp += _sts_status_packet(sid, 0, self.read_bytes(sid, start_addr, read_len))
            return resp

        return b''

    def start(self):
        """启动本设备的 TCP 服务"""
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.tcp_port))
        srv.listen(2)
        self._server = srv
        role_name = _ROLE_NAMES.get(self.role, f"role{self.role}")
        print(f"[STS] dev={self.dev_id} ({role_name}) mac={self.mac or '?'}"
              f"  ->  socket://localhost:{self.tcp_port}")
        while True:
            try:
                conn, addr = srv.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                t.start()
            except OSError:
                break

    def _handle_client(self, conn, addr):
        role_name = _ROLE_NAMES.get(self.role, f"role{self.role}")
        print(f"[STS:{self.tcp_port}] {role_name} dev={self.dev_id} 客户端连接: {addr}")
        buf = bytearray()
        pkt_count = 0
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf.extend(data)
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
                    total_len = 4 + pkt_len
                    if len(buf) < total_len:
                        break
                    frame = bytes(buf[:total_len])
                    buf = buf[total_len:]
                    if _sts_checksum(frame[2:-1]) != frame[-1]:
                        continue
                    response = self.process_packet(frame[2], frame[4], frame[5:-1])
                    if response:
                        conn.sendall(response)
                    pkt_count += 1
                if len(buf) > 4096:
                    buf = buf[-256:]
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            conn.close()
            has_ctrl = getattr(self, '_ctrl_count', 0) > 0
            print(f"[STS:{self.tcp_port}] dev={self.dev_id} 客户端断开 ({pkt_count} 包)")
            # 客户端断开 -> 释放 GW_CONTROL，让 Follower 恢复 Leader 直连
            if has_ctrl and self.mac and self.role == 0:
                store.gw_control_macs.discard(self.mac)
                serial_write_cmd({"cmd": "ctrl_release", "mac": self.mac})
                print(f"[STS:{self.tcp_port}] 已释放 GW_CONTROL: {self.mac[-5:]}")
                self._ctrl_count = 0


class STSPortManager:
    """自动为每个在线设备分配独立的 STS TCP 端口，MAC->端口映射持久化到配置文件。

    配置文件 sts_ports.json 示例:
    {
      "AA:BB:CC:DD:EE:FF": {"port": 6560, "role": 0, "role_name": "Follower", "dev_id": 228, "last_seen": "2026-03-17"},
      "11:22:33:44:55:66": {"port": 6561, "role": 1, "role_name": "Leader",   "dev_id": 148, "last_seen": "2026-03-17"}
    }

    同一 MAC 的设备每次启动都会分配到相同的端口号，不会因上线顺序变化而改变。
    """

    CONFIG_FILE = "sts_ports.json"

    def __init__(self, base_port=6560):
        self.base_port = base_port
        self.lock = threading.Lock()
        self._ports = {}          # dev_id -> _DevicePort
        self._mac_map = {}        # mac -> {"port": int, "role": int, ...}  持久化映射
        self._used_ports = set()  # 已占用端口
        self._running = False
        self._config_path = Path(__file__).parent / self.CONFIG_FILE
        self._load_config()

    def _load_config(self):
        """从 JSON 文件加载历史 MAC->端口映射"""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._mac_map = json.load(f)
                for mac, info in self._mac_map.items():
                    self._used_ports.add(info["port"])
                print(f"[STS] 已加载端口配置: {self._config_path.name} ({len(self._mac_map)} 设备)")
                for mac, info in self._mac_map.items():
                    role_name = info.get("role_name", "?")
                    print(f"  {mac}  {role_name:10s} dev={info.get('dev_id','?'):>4}  "
                          f"->  socket://localhost:{info['port']}  (last: {info.get('last_seen','?')})")
            except Exception as e:
                print(f"[STS] 配置加载失败: {e}，将使用全新分配")
                self._mac_map = {}

    def _save_config(self):
        """保存 MAC->端口映射到 JSON 文件"""
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._mac_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[STS] 配置保存失败: {e}")

    def _alloc_port(self, mac, role, dev_id):
        """为 MAC 分配端口，优先复用历史记录"""
        # 已有记录 -> 复用
        if mac and mac in self._mac_map:
            info = self._mac_map[mac]
            info["role"] = role
            info["role_name"] = _ROLE_NAMES.get(role, "?")
            info["dev_id"] = dev_id
            info["last_seen"] = time.strftime("%Y-%m-%d")
            self._save_config()
            return info["port"]

        # 新设备 -> 找下一个空闲端口
        port = self.base_port
        while port in self._used_ports:
            port += 1
        self._used_ports.add(port)

        # 写入映射
        if mac:
            self._mac_map[mac] = {
                "port": port,
                "role": role,
                "role_name": _ROLE_NAMES.get(role, "?"),
                "dev_id": dev_id,
                "last_seen": time.strftime("%Y-%m-%d"),
            }
            self._save_config()
        return port

    def start(self):
        """启动设备发现循环"""
        self._running = True
        t = threading.Thread(target=self._discovery_loop, daemon=True)
        t.start()
        print(f"[STS] 虚拟串口管理器已启动 (base_port={self.base_port})")
        print(f"[STS] 等待设备上线... 每个设备将自动分配 socket://localhost:<port>")

    def _discovery_loop(self):
        """定期扫描 store.devices，为新设备分配端口"""
        while self._running:
            now = time.time()
            with store.lock:
                current_devs = {
                    dev_id: data for dev_id, data in store.devices.items()
                    if data.get("servos")  # 只关注有舵机数据的设备
                    and data.get("role", -1) in (0, 1, 2)  # Follower/Leader/M-Leader
                    and now - data.get("_pc_time", 0) < store.DEVICE_TTL  # 仅活跃设备
                }
            with self.lock:
                for dev_id, data in current_devs.items():
                    if dev_id not in self._ports:
                        role = data.get("role", -1)
                        mac = data.get("mac")
                        tcp_port = self._alloc_port(mac, role, dev_id)
                        dp = _DevicePort(dev_id, mac, role, tcp_port)
                        self._ports[dev_id] = dp
                        dp.start()
                    else:
                        # 更新 MAC（首次可能为空）
                        dp = self._ports[dev_id]
                        mac = data.get("mac")
                        if mac and dp.mac != mac:
                            dp.mac = mac
                            # 更新配置中的 MAC
                            if mac not in self._mac_map:
                                self._mac_map[mac] = {
                                    "port": dp.tcp_port,
                                    "role": dp.role,
                                    "role_name": _ROLE_NAMES.get(dp.role, "?"),
                                    "dev_id": dev_id,
                                    "last_seen": time.strftime("%Y-%m-%d"),
                                }
                                self._save_config()
            time.sleep(1)

    def get_port_table(self):
        """返回当前在线设备的端口分配表（含虚拟串口桥接信息）"""
        from box2driver.vcom_bridge import _vserial_bridge
        bridge_table = _vserial_bridge.get_table() if _vserial_bridge else {}
        with self.lock:
            return {
                dev_id: {
                    "port": dp.tcp_port,
                    "role": dp.role,
                    "role_name": _ROLE_NAMES.get(dp.role, "?"),
                    "mac": dp.mac,
                    "url": f"socket://localhost:{dp.tcp_port}",
                    "com": bridge_table.get(dev_id, ""),
                }
                for dev_id, dp in self._ports.items()
            }

    def get_full_config(self):
        """返回完整历史配置（含离线设备）"""
        with self.lock:
            online_macs = {dp.mac for dp in self._ports.values() if dp.mac}
            result = {}
            for mac, info in self._mac_map.items():
                result[mac] = {**info, "online": mac in online_macs}
            return result


_sts_manager = None
