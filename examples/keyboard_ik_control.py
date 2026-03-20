#!/usr/bin/env python3
"""
Box2Driver D1 — 键盘控制示例

通过键盘操控机械臂关节位置，
通过 Gateway WebSocket 发送舵机位置给 Follower。

支持两种模式：
  - IK 笛卡尔空间模式 (需要 lerobot-kinematics)
  - 关节空间模式 (无额外依赖)

依赖安装：
    pip install websockets pynput numpy
    cd lerobot-kinematics && pip install -e .   # IK 模式需要 C 编译器

使用方式：
    1. 启动 Gateway Dashboard:
       python scripts/gateway_dashboard.py

    2. 运行本脚本:
       python examples/keyboard_ik_control.py
       python examples/keyboard_ik_control.py --mac AA:BB:CC:DD:EE:FF   # 指定目标
       python examples/keyboard_ik_control.py --mode joint              # 关节空间模式

启动流程：
    1. 连接 Gateway WebSocket
    2. 等待 Follower 上线，读取当前舵机位置作为初始值
    3. 按 Enter 键开始控制（在当前位置的基础上增量控制）

键盘映射 (IK 笛卡尔空间模式):
    W/S  — X 前进/后退
    A/D  — Y 左移/右移
    R/F  — Z 上升/下降
    Q/E  — Roll +/-
    G/T  — Pitch +/-
    Z/C  — 夹爪 开/合
    0    — 回到启动时的位置
    ESC  — 退出

键盘映射 (关节空间模式):
    1/Q  — 关节1 (底座旋转) +/-
    2/W  — 关节2 (肩部俯仰) +/-
    3/E  — 关节3 (肘部弯曲) +/-
    4/R  — 关节4 (腕部俯仰) +/-
    5/T  — 关节5 (腕部翻转) +/-
    6/Y  — 关节6 (夹爪) +/-
    0    — 回到启动时的位置
    ESC  — 退出

作者: Box2Driver D1 项目
"""

import sys
import os
import time
import threading
import argparse
import numpy as np
from pynput import keyboard

# 添加项目根目录到路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from box2driver_client import Box2DriverClient

# ============================================
# 舵机参数
# ============================================

# STS3215: 4096 步 = 360°, 中位 = 2048
SERVO_RESOLUTION = 4096
SERVO_CENTER = SERVO_RESOLUTION // 2  # 2048
NUM_JOINTS = 6

# 各关节的方向映射 (1=正向, -1=反向) — 与 feetech_arm.action() 一致
# joints 0,1,4 需要反向
JOINT_DIRECTION = np.array([-1, -1, 1, 1, -1, 1], dtype=float)

# 关节增量 (raw servo steps per key press)
RAW_INCREMENT = 20       # 关节空间模式: 每次约 1.76°
IK_POS_INCREMENT = 0.0008  # IK 笛卡尔模式 (m/step)
IK_JOINT_INCREMENT = 0.005  # IK 模式下底座/夹爪增量 (rad)

# 关节空间 raw 限位: 以启动位置为中心的最大偏移量 (steps)
# 每个关节可独立配置，默认 ±800 steps ≈ ±70°
# [底座旋转, 肩部, 肘部, 腕俯仰, 腕翻转, 夹爪]
RAW_MAX_OFFSET = np.array([800, 800, 800, 800, 800, 400])
# 绝对安全边界 (防止到达舵机物理极限)
RAW_ABS_MIN = 80
RAW_ABS_MAX = SERVO_RESOLUTION - 80  # 4016


def raw_to_rad(raw_array):
    """舵机原始位置 (0-4095) -> 弧度 (考虑方向映射)"""
    raw = np.array(raw_array, dtype=float)
    degrees = (raw - SERVO_CENTER) / (SERVO_RESOLUTION // 2) * 180.0
    rad = np.deg2rad(degrees) / JOINT_DIRECTION
    return rad


def rad_to_raw(rad_array):
    """弧度 -> 舵机原始位置 (0-4095) (考虑方向映射)"""
    degrees = np.rad2deg(rad_array) * JOINT_DIRECTION
    raw = (degrees / 180.0 * (SERVO_RESOLUTION // 2) + SERVO_CENTER).astype(int)
    return np.clip(raw, 0, SERVO_RESOLUTION - 1)


# ============================================
# IK 模块加载
# ============================================

try:
    from lerobot_kinematics import lerobot_IK, lerobot_FK, get_robot
    IK_AVAILABLE = True
    robot = get_robot('so100')
    print("[OK] lerobot-kinematics (C扩展) 已加载")
except ImportError:
    try:
        from so100_kinematics import lerobot_IK, lerobot_FK, get_robot
        IK_AVAILABLE = True
        robot = get_robot('so100')
        print("[OK] so100_kinematics (纯Python) 已加载，IK 笛卡尔控制可用")
    except ImportError:
        IK_AVAILABLE = False
        robot = None
        print("[WARN] 运动学库未找到，仅关节空间模式可用")
        print("       scripts/so100_kinematics.py 应与本脚本同仓库")


# ============================================
# 控制器
# ============================================

class KeyboardController:
    """键盘控制器 — 支持 IK 笛卡尔模式和关节空间模式"""

    def __init__(self, client: Box2DriverClient, mac: str = None, mode: str = "ik",
                 control_hz: float = 30):
        self.client = client
        self.mac = mac
        self.mode = mode
        self.hz = control_hz
        self.running = False

        # 当前舵机 raw 位置 (启动时从 Follower 读取)
        self.current_raw = np.full(NUM_JOINTS, SERVO_CENTER, dtype=int)

        # IK 模式的关节弧度和末端位姿 (启动后从 raw 计算)
        self.target_qpos = None
        self.target_gpos = None
        self.target_gpos_last = None
        self.init_raw = None

        # IK 笛卡尔控制限位
        self.control_glimit = [
            [0.125, -0.4, 0.046, -3.1, -0.75, -1.5],
            [0.340,  0.4, 0.23,   2.0,  1.57,  1.5]
        ]

        # 关节限位 (弧度)
        self.qlimit = [
            [-2.1, -3.14, 0.0, -1.375, -1.57, -0.15],
            [ 2.1,  0.0,  3.14, 1.475,  3.14,  1.5]
        ]

        # 键盘状态
        self.lock = threading.Lock()
        self.keys_pressed = {}

    def start(self):
        """启动控制循环"""
        self.running = True

        # 启动客户端后台线程
        self.client.start()
        time.sleep(0.5)

        # 读取 Follower 当前位置
        if not self._read_initial_positions():
            print("[ERROR] 未能读取到设备位置，请检查 Gateway 和设备连接")
            self.client.stop()
            return

        # 初始化控制状态
        self.init_raw = self.current_raw.copy()
        self.target_qpos = raw_to_rad(self.current_raw)
        if IK_AVAILABLE and self.mode == "ik":
            self.target_gpos = lerobot_FK(self.target_qpos[1:5], robot=robot)
            self.target_gpos_last = self.target_gpos.copy()

        # 显示限位范围
        low = np.maximum(RAW_ABS_MIN, self.init_raw.astype(int) - RAW_MAX_OFFSET)
        high = np.minimum(RAW_ABS_MAX, self.init_raw.astype(int) + RAW_MAX_OFFSET)
        print("[INFO] 限位范围 (基于当前位置 ± 最大偏移):")
        for j in range(NUM_JOINTS):
            print(f"       J{j+1}: [{low[j]:4d} ~ {high[j]:4d}]  当前={self.init_raw[j]:4d}  范围=±{RAW_MAX_OFFSET[j]}")

        self._print_help()

        # 等待用户确认
        print("\n按 Enter 开始控制（当前位置作为起点）...")
        try:
            input()
        except EOFError:
            pass

        # 启动键盘监听
        self.listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release
        )
        self.listener.start()

        print("[INFO] 控制已启动！按键操控机械臂，ESC 退出")

        # 控制循环
        try:
            while self.running:
                t0 = time.time()
                self._update()
                dt = time.time() - t0
                sleep_time = 1.0 / self.hz - dt
                if sleep_time > 0:
                    time.sleep(sleep_time)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _read_initial_positions(self):
        """从 Gateway 读取 Follower 当前舵机位置"""
        print("[INFO] 等待设备上线...")

        # 等待设备数据 (最多 10 秒)
        for i in range(100):
            all_pos = self.client.get_all_positions()
            if all_pos:
                # 找到目标设备或第一个设备
                target_dev = None
                if self.mac:
                    # 按 MAC 查找
                    for dev_id, frame in self.client.latest.items():
                        if frame.get("mac", "").upper() == self.mac.upper():
                            target_dev = dev_id
                            break
                if not target_dev:
                    # 用第一个有 servo 数据的设备
                    for dev_id, positions in all_pos.items():
                        if len(positions) >= NUM_JOINTS:
                            target_dev = dev_id
                            break

                if target_dev and target_dev in all_pos:
                    positions = all_pos[target_dev]
                    if len(positions) >= NUM_JOINTS:
                        for j in range(NUM_JOINTS):
                            servo_id = j + 1
                            if servo_id in positions:
                                self.current_raw[j] = positions[servo_id]

                        print(f"[OK] 读取到设备 {target_dev} 当前位置:")
                        pos_str = " ".join(
                            [f"J{j+1}:{self.current_raw[j]:4d}" for j in range(NUM_JOINTS)]
                        )
                        print(f"     {pos_str}")
                        return True
            time.sleep(0.1)

        return False

    def stop(self):
        """停止控制"""
        self.running = False
        if hasattr(self, 'listener'):
            self.listener.stop()
        # 释放 Gateway 控制
        self.client.release_control(self.mac)
        time.sleep(0.1)
        self.client.stop()
        print("\n[INFO] 已释放控制，退出")

    def _print_help(self):
        if self.mode == "ik" and IK_AVAILABLE:
            print("\n===== 键盘 IK 笛卡尔控制 =====")
            print("  W/S  X前进/后退    A/D  Y左/右    R/F  Z上/下")
            print("  Q/E  Roll +/-      G/T  Pitch +/-")
            print("  Z/C  夹爪 开/合    0    复位      ESC  退出")
        else:
            print("\n===== 键盘关节空间控制 =====")
            print("  1/Q  关节1(底座)   2/W  关节2(肩部)   3/E  关节3(肘部)")
            print("  4/R  关节4(腕俯仰) 5/T  关节5(腕翻转) 6/Y  关节6(夹爪)")
            print("  0    复位          ESC  退出")
        target_mac = self.mac or "广播"
        print(f"  目标: {target_mac}  频率: {self.hz}Hz")
        print("=" * 35)

    # ---- 键盘事件 ----

    def _on_press(self, key):
        try:
            k = key.char.lower()
            with self.lock:
                if self.mode == "ik" and IK_AVAILABLE:
                    self._ik_key_press(k)
                else:
                    self._joint_key_press(k)
        except AttributeError:
            if key == keyboard.Key.esc:
                self.running = False

    def _on_release(self, key):
        try:
            k = key.char.lower()
            with self.lock:
                if k in self.keys_pressed:
                    del self.keys_pressed[k]
        except AttributeError:
            pass

    def _ik_key_press(self, k):
        """IK 模式按键处理"""
        ik_increase = {'w': 0, 'a': 1, 'r': 2, 'q': 3, 'g': 4, 'z': 5}
        ik_decrease = {'s': 0, 'd': 1, 'f': 2, 'e': 3, 't': 4, 'c': 5}

        if k in ik_increase:
            self.keys_pressed[k] = ('inc', ik_increase[k])
        elif k in ik_decrease:
            self.keys_pressed[k] = ('dec', ik_decrease[k])
        elif k == '0':
            self.current_raw = self.init_raw.copy()
            self.target_qpos = raw_to_rad(self.init_raw)
            self.target_gpos = lerobot_FK(self.target_qpos[1:5], robot=robot)
            self.target_gpos_last = self.target_gpos.copy()
            self._send_current()
            print("\n[RESET] 回到启动时位置")

    def _joint_key_press(self, k):
        """关节空间模式按键处理"""
        joint_inc = {'1': 0, '2': 1, '3': 2, '4': 3, '5': 4, '6': 5}
        joint_dec = {'q': 0, 'w': 1, 'e': 2, 'r': 3, 't': 4, 'y': 5}

        if k in joint_inc:
            self.keys_pressed[k] = ('inc', joint_inc[k])
        elif k in joint_dec:
            self.keys_pressed[k] = ('dec', joint_dec[k])
        elif k == '0':
            self.current_raw = self.init_raw.copy()
            self._send_current()
            print("\n[RESET] 回到启动时位置")

    # ---- 控制循环 ----

    def _update(self):
        """每帧更新"""
        with self.lock:
            if not self.keys_pressed:
                return

            if self.mode == "ik" and IK_AVAILABLE:
                self._update_ik()
            else:
                self._update_joint()

        self._send_current()

    def _update_ik(self):
        """IK 模式: 键盘增量 -> 末端位姿 -> IK 解算 -> 关节角 -> raw"""
        for k, (direction, idx) in self.keys_pressed.items():
            sign = 1 if direction == 'inc' else -1

            # 底座旋转 (idx=1 → joint 0) 和 夹爪 (idx=5) 直接关节空间操作
            if idx == 1:  # Y → 底座旋转
                j = 0
                new_val = self.target_qpos[j] + IK_JOINT_INCREMENT * sign
                if self.qlimit[0][j] < new_val < self.qlimit[1][j]:
                    self.target_qpos[j] = new_val
            elif idx == 5:  # 夹爪
                j = 5
                new_val = self.target_qpos[j] + IK_JOINT_INCREMENT * sign
                if self.qlimit[0][j] < new_val < self.qlimit[1][j]:
                    self.target_qpos[j] = new_val
            elif idx in (3, 4):  # Roll / Pitch
                new_val = self.target_gpos[idx] + IK_POS_INCREMENT * sign * 4
                if self.control_glimit[0][idx] <= new_val <= self.control_glimit[1][idx]:
                    self.target_gpos[idx] = new_val
            else:  # XYZ
                new_val = self.target_gpos[idx] + IK_POS_INCREMENT * sign
                if self.control_glimit[0][idx] <= new_val <= self.control_glimit[1][idx]:
                    self.target_gpos[idx] = new_val

        # IK 解算 (4 DOF 中间关节)
        fd_qpos = self.target_qpos[1:5]
        qpos_inv, success = lerobot_IK(fd_qpos, self.target_gpos, robot=robot)

        if np.all(qpos_inv != -1.0) and success:
            self.target_qpos = np.concatenate((
                self.target_qpos[0:1], qpos_inv[:4], self.target_qpos[5:]
            ))
            candidate_raw = rad_to_raw(self.target_qpos)
            # 限位检查: 不超过初始位置 ± 最大偏移 且 不超过绝对安全边界
            low = np.maximum(RAW_ABS_MIN, self.init_raw.astype(int) - RAW_MAX_OFFSET)
            high = np.minimum(RAW_ABS_MAX, self.init_raw.astype(int) + RAW_MAX_OFFSET)
            if np.all(candidate_raw >= low) and np.all(candidate_raw <= high):
                self.current_raw = candidate_raw
                self.target_gpos_last = self.target_gpos.copy()
            else:
                # IK 结果超出安全范围，回退
                self.target_qpos = raw_to_rad(self.current_raw)
                self.target_gpos = self.target_gpos_last.copy()
        else:
            self.target_gpos = self.target_gpos_last.copy()

    def _update_joint(self):
        """关节空间模式: 键盘增量 -> 直接修改 raw 位置 (带限位保护)"""
        for k, (direction, idx) in self.keys_pressed.items():
            sign = 1 if direction == 'inc' else -1
            new_val = int(self.current_raw[idx]) + RAW_INCREMENT * sign
            # 限位: 初始位置 ± 最大偏移 且 不超过绝对安全边界
            low = max(RAW_ABS_MIN, int(self.init_raw[idx]) - RAW_MAX_OFFSET[idx])
            high = min(RAW_ABS_MAX, int(self.init_raw[idx]) + RAW_MAX_OFFSET[idx])
            if low <= new_val <= high:
                self.current_raw[idx] = new_val

    def _send_current(self):
        """将当前 raw 位置通过 WebSocket 发送"""
        servos = [{"id": i + 1, "pos": int(self.current_raw[i])} for i in range(NUM_JOINTS)]
        ok = self.client.send_positions(servos, mac=self.mac)

        pos_str = " ".join([f"J{i+1}:{int(self.current_raw[i]):4d}" for i in range(NUM_JOINTS)])
        status = "OK" if ok else "FAIL"
        print(f"\r[{status}] {pos_str}  ", end="", flush=True)


# ============================================
# 主程序
# ============================================

def main():
    parser = argparse.ArgumentParser(
        description="Box2Driver D1 键盘控制 — 通过 Gateway WebSocket 控制机械臂"
    )
    parser.add_argument("--ws", default="ws://localhost:8765",
                        help="Gateway WebSocket 地址 (默认 ws://localhost:8765)")
    parser.add_argument("--mac", default=None,
                        help="目标 Follower MAC 地址 (如 AA:BB:CC:DD:EE:FF)，不指定则广播")
    parser.add_argument("--mode", choices=["ik", "joint"], default="ik",
                        help="控制模式: ik=笛卡尔IK空间, joint=关节空间 (默认 ik)")
    parser.add_argument("--hz", type=float, default=30,
                        help="控制频率 Hz (默认 30)")

    args = parser.parse_args()

    if args.mode == "ik" and not IK_AVAILABLE:
        print("[WARN] IK 不可用，自动切换到关节空间模式")
        args.mode = "joint"

    print(f"[INFO] 连接 Gateway: {args.ws}")
    client = Box2DriverClient(ws_url=args.ws)

    controller = KeyboardController(
        client=client,
        mac=args.mac,
        mode=args.mode,
        control_hz=args.hz,
    )

    controller.start()


if __name__ == "__main__":
    main()
