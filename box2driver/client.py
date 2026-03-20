#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
Box2Driver Gateway Python Client

从 Gateway Dashboard 的 WebSocket 服务实时读取机械臂数据，
供外部 Python 程序进行数据采集、模型训练等。

三种使用方式：
1. 回调模式 — 每帧数据到达时触发回调
2. 迭代器模式 — for 循环逐帧读取
3. 快照模式 — 随时获取各设备最新状态

依赖：
    pip install websockets

示例：
    # 回调模式
    from box2driver_client import Box2DriverClient

    def on_frame(dev_id, frame):
        print(f"设备 {dev_id}: servo1_pos={frame['servos'][0]['pos']}")

    client = Box2DriverClient()
    client.on_frame = on_frame
    client.run()   # 阻塞运行

    # 迭代器模式
    client = Box2DriverClient()
    for dev_id, frame in client.stream():
        print(dev_id, frame)

    # 快照模式（在另一个线程中运行）
    client = Box2DriverClient()
    client.start()          # 后台线程
    latest = client.latest  # dict: {dev_id: frame}
    client.stop()
"""

import asyncio
import json
import threading
import time
from collections import defaultdict
from typing import Callable, Dict, Iterator, List, Optional, Tuple


class Box2DriverClient:
    """Box2Driver Gateway WebSocket 客户端

    连接到 gateway_dashboard.py 的 WebSocket 服务，
    实时接收所有机械臂的舵机姿态数据。

    Attributes:
        latest (dict): 各设备最新帧 {dev_id_str: frame_dict}
        history (dict): 各设备历史帧 {dev_id_str: [frame_dict, ...]}
        on_frame (callable): 回调函数 on_frame(dev_id: str, frame: dict)
        connected (bool): 是否已连接
        stats (dict): 统计信息
    """

    def __init__(
        self,
        ws_url: str = "ws://localhost:8765",
        max_history: int = 3600,
        auto_reconnect: bool = True,
    ):
        """
        Args:
            ws_url: Gateway WebSocket 地址
            max_history: 每设备最大历史帧数 (默认 3600 = 60Hz × 60s)
            auto_reconnect: 断线自动重连
        """
        self.ws_url = ws_url
        self.max_history = max_history
        self.auto_reconnect = auto_reconnect

        # 数据存储（线程安全）
        self._lock = threading.Lock()
        self.latest: Dict[str, dict] = {}
        self.history: Dict[str, list] = defaultdict(list)

        # 回调
        self.on_frame: Optional[Callable[[str, dict], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        # 状态
        self.connected = False
        self.stats = {"total_packets": 0, "start_time": 0, "devices": set()}

        # 内部
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = threading.Event()
        self._stream_queue: Optional[asyncio.Queue] = None
        self._ws: Optional[object] = None  # 当前 WebSocket 连接
        self._replay_stop = threading.Event()

    # ============================================
    # === 公开 API
    # ============================================

    def start(self):
        """后台线程启动，非阻塞。用于快照模式。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止后台线程。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def run(self):
        """阻塞运行，配合 on_frame 回调使用。Ctrl+C 退出。"""
        self.stats["start_time"] = time.time()
        try:
            asyncio.run(self._ws_loop())
        except KeyboardInterrupt:
            self._print_stats()

    def stream(self) -> Iterator[Tuple[str, dict]]:
        """迭代器模式，逐帧 yield (dev_id, frame)。

        Usage:
            client = Box2DriverClient()
            for dev_id, frame in client.stream():
                print(dev_id, frame['servos'])
        """
        import queue

        q = queue.Queue(maxsize=1000)

        original_cb = self.on_frame

        def _enqueue(dev_id, frame):
            try:
                q.put_nowait((dev_id, frame))
            except queue.Full:
                q.get_nowait()  # 丢弃最旧
                q.put_nowait((dev_id, frame))
            if original_cb:
                original_cb(dev_id, frame)

        self.on_frame = _enqueue
        self.start()

        try:
            while not self._stop_event.is_set():
                try:
                    yield q.get(timeout=1.0)
                except queue.Empty:
                    continue
        finally:
            self.on_frame = original_cb
            self.stop()

    def get_latest(self, dev_id: Optional[str] = None) -> dict:
        """获取最新帧。

        Args:
            dev_id: 设备 ID。None 返回所有设备。

        Returns:
            单设备: frame dict; 所有设备: {dev_id: frame}
        """
        with self._lock:
            if dev_id is not None:
                return self.latest.get(str(dev_id), {}).copy()
            return {k: v.copy() for k, v in self.latest.items()}

    def get_history(self, dev_id: str, last_n: Optional[int] = None) -> list:
        """获取历史帧。

        Args:
            dev_id: 设备 ID
            last_n: 返回最近 N 帧。None 返回全部。

        Returns:
            [frame_dict, ...]
        """
        with self._lock:
            h = self.history.get(str(dev_id), [])
            if last_n:
                return [f.copy() for f in h[-last_n:]]
            return [f.copy() for f in h]

    def get_servo_positions(self, dev_id: str) -> Dict[int, int]:
        """获取某设备所有舵机当前位置。

        Returns:
            {servo_id: position}
        """
        frame = self.get_latest(str(dev_id))
        if not frame or "servos" not in frame:
            return {}
        return {s["id"]: s["pos"] for s in frame["servos"]}

    def get_all_positions(self) -> Dict[str, Dict[int, int]]:
        """获取所有设备所有舵机位置。

        Returns:
            {dev_id: {servo_id: position}}
        """
        result = {}
        with self._lock:
            for dev_id, frame in self.latest.items():
                if frame and "servos" in frame:
                    result[dev_id] = {s["id"]: s["pos"] for s in frame["servos"]}
        return result

    def wait_for_devices(self, count: int = 1, timeout: float = 10.0) -> List[str]:
        """等待指定数量的设备上线。

        Args:
            count: 需要的设备数
            timeout: 超时秒数

        Returns:
            设备 ID 列表

        Raises:
            TimeoutError: 超时未发现足够设备
        """
        self.start()
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                if len(self.latest) >= count:
                    return list(self.latest.keys())
            time.sleep(0.1)
        with self._lock:
            found = list(self.latest.keys())
        if len(found) < count:
            raise TimeoutError(
                f"等待 {timeout}s 后只发现 {len(found)}/{count} 个设备"
            )
        return found

    def record(
        self,
        duration: float,
        dev_ids: Optional[List[str]] = None,
    ) -> Dict[str, list]:
        """录制指定时长的数据。

        Args:
            duration: 录制秒数
            dev_ids: 要录制的设备 ID 列表。None = 所有设备。

        Returns:
            {dev_id: [frame_dict, ...]}
        """
        recorded: Dict[str, list] = defaultdict(list)
        t0 = time.time()

        def _record_cb(dev_id, frame):
            if dev_ids is None or dev_id in dev_ids:
                recorded[dev_id].append(
                    {"t": time.time() - t0, "pc_time": time.time(), **frame}
                )

        old_cb = self.on_frame
        self.on_frame = _record_cb
        self.start()

        try:
            time.sleep(duration)
        finally:
            self.on_frame = old_cb

        return dict(recorded)

    # ============================================
    # === 内部实现
    # ============================================

    def _run_loop(self):
        self.stats["start_time"] = time.time()
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._ws_loop())
        except Exception:
            pass
        finally:
            loop.close()
            self._loop = None

    async def _ws_loop(self):
        try:
            import websockets
        except ImportError:
            print("错误: pip install websockets")
            return

        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    self.connected = True
                    if self.on_connect:
                        self.on_connect()

                    async for msg in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            data = json.loads(msg)
                        except json.JSONDecodeError:
                            continue

                        # snapshot 消息（初始连接时）
                        if data.get("type") == "snapshot":
                            continue
                        if data.get("type") == "trajectories":
                            continue

                        # 普通数据帧
                        if "dev" not in data or "servos" not in data:
                            continue

                        dev_id = str(data["dev"])
                        frame = {
                            "dev": data["dev"],
                            "mac": data.get("mac", ""),
                            "role": data.get("role", -1),
                            "seq": data.get("seq", 0),
                            "t": data.get("t", 0),
                            "servos": data.get("servos", []),
                            "pc_time": time.time(),
                        }

                        with self._lock:
                            self.latest[dev_id] = frame
                            h = self.history[dev_id]
                            h.append(frame)
                            if len(h) > self.max_history:
                                h.pop(0)
                            self.stats["total_packets"] += 1
                            self.stats["devices"].add(dev_id)

                        if self.on_frame:
                            try:
                                self.on_frame(dev_id, frame)
                            except Exception:
                                pass

            except Exception:
                self._ws = None
                self.connected = False
                if self.on_disconnect:
                    self.on_disconnect()
                if not self.auto_reconnect or self._stop_event.is_set():
                    break
                await asyncio.sleep(2)

        self._ws = None
        self.connected = False

    def _print_stats(self):
        elapsed = time.time() - self.stats["start_time"]
        print(f"\n--- Box2DriverClient 统计 ---")
        print(f"总帧数: {self.stats['total_packets']}")
        print(f"设备数: {len(self.stats['devices'])}")
        print(f"运行时间: {elapsed:.1f}s")
        if elapsed > 0:
            print(f"平均频率: {self.stats['total_packets'] / elapsed:.1f} Hz")

    # ============================================
    # === 控制 API
    # ============================================

    def _send_ws(self, msg: dict) -> bool:
        """通过 WebSocket 发送 JSON 消息（线程安全）。"""
        if not self._ws or not self._loop:
            return False
        try:
            data = json.dumps(msg, ensure_ascii=False)
            future = asyncio.run_coroutine_threadsafe(self._ws.send(data), self._loop)
            future.result(timeout=0.5)
            return True
        except Exception:
            return False

    def send_positions(self, servos: list, mac: str = None) -> bool:
        """发送舵机位置控制命令。

        Args:
            servos: [{"id": 1, "pos": 2048}, {"id": 2, "pos": 1024}, ...]
            mac: 目标 Follower MAC (如 "AA:BB:CC:DD:EE:FF")，None 则广播

        Returns:
            是否发送成功
        """
        msg = {"type": "control", "servos": servos}
        if mac:
            msg["mac"] = mac
        return self._send_ws(msg)

    def send_torque(self, servo_id: int, enable: bool) -> bool:
        """控制单个舵机力矩。"""
        return self._send_ws({"type": "torque", "id": servo_id, "enable": 1 if enable else 0})

    def send_torque_all(self, enable: bool) -> bool:
        """控制所有舵机力矩。"""
        return self._send_ws({"type": "torque_all", "enable": 1 if enable else 0})

    def release_control(self, mac: str = None) -> bool:
        """释放 Gateway 对 Follower 的控制。

        Args:
            mac: 目标 Follower MAC，None 则广播释放所有
        """
        msg = {"type": "ctrl_release"}
        if mac:
            msg["mac"] = mac
        return self._send_ws(msg)

    # ============================================
    # === 录制 & 回放 API
    # ============================================

    def record_trajectory(
        self,
        duration: float,
        dev_ids: Optional[List[str]] = None,
        fps: int = 30,
    ) -> Dict[str, list]:
        """录制轨迹数据，适合回放使用。

        Args:
            duration: 录制秒数
            dev_ids: 要录制的设备 ID。None = 所有设备。
            fps: 采样帧率

        Returns:
            {"dev_id": [{"t": 相对时间, "servos": [{"id":1,"pos":2048}, ...]}, ...]}
        """
        self.start()
        recorded: Dict[str, list] = {}
        interval = 1.0 / fps
        t0 = time.time()

        while time.time() - t0 < duration:
            loop_start = time.time()
            with self._lock:
                for dev_id, frame in self.latest.items():
                    if dev_ids and dev_id not in dev_ids:
                        continue
                    if dev_id not in recorded:
                        recorded[dev_id] = []
                    recorded[dev_id].append({
                        "t": time.time() - t0,
                        "servos": [{"id": s["id"], "pos": s["pos"]} for s in frame.get("servos", [])],
                    })
            elapsed = time.time() - loop_start
            if elapsed < interval:
                time.sleep(interval - elapsed)

        for dev_id in recorded:
            print(f"  录制 {dev_id}: {len(recorded[dev_id])} 帧, {duration:.1f}s")
        return recorded

    def replay_trajectory(
        self,
        trajectory: list,
        loops: int = 1,
        speed: float = 1.0,
    ) -> int:
        """回放轨迹到 Follower。

        Args:
            trajectory: record_trajectory 返回的单设备帧列表
                        [{"t": 0.0, "servos": [...]}, ...]
            loops: 循环次数 (0 = 无限循环)
            speed: 播放速度倍率 (1.0 = 原速, 2.0 = 两倍速)

        Returns:
            实际播放的帧数
        """
        if not trajectory:
            return 0

        self.start()
        self._replay_stop.clear()
        total_sent = 0
        loop_count = 0

        try:
            while not self._replay_stop.is_set():
                loop_count += 1
                t0 = time.time()

                for i, frame in enumerate(trajectory):
                    if self._replay_stop.is_set():
                        break

                    # 计算目标时间点
                    target_t = frame["t"] / speed
                    elapsed = time.time() - t0
                    wait = target_t - elapsed
                    if wait > 0:
                        # 用小步等待以支持中断
                        while wait > 0 and not self._replay_stop.is_set():
                            time.sleep(min(wait, 0.01))
                            wait = target_t - (time.time() - t0)

                    if self._replay_stop.is_set():
                        break

                    if self.send_positions(frame["servos"]):
                        total_sent += 1

                if loops > 0 and loop_count >= loops:
                    break

        except KeyboardInterrupt:
            pass

        return total_sent

    def stop_replay(self):
        """停止正在进行的回放。"""
        self._replay_stop.set()

    def save_trajectory(self, trajectory: Dict[str, list], filepath: str):
        """保存轨迹到 JSON 文件。"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(trajectory, f, ensure_ascii=False)
        total = sum(len(v) for v in trajectory.values())
        print(f"轨迹已保存: {filepath} ({len(trajectory)} 设备, {total} 帧)")

    def load_trajectory(self, filepath: str) -> Dict[str, list]:
        """从 JSON 文件加载轨迹。"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        total = sum(len(v) for v in data.values())
        print(f"轨迹已加载: {filepath} ({len(data)} 设备, {total} 帧)")
        return data


# ============================================
# === 便捷函数
# ============================================

def connect(ws_url: str = "ws://localhost:8765", **kwargs) -> Box2DriverClient:
    """快速创建并启动客户端（后台模式）。

    Usage:
        import box2driver_client as bd
        client = bd.connect()
        client.wait_for_devices(2)
        print(client.get_all_positions())
    """
    client = Box2DriverClient(ws_url=ws_url, **kwargs)
    client.start()
    return client


def record(duration: float, ws_url: str = "ws://localhost:8765") -> Dict[str, list]:
    """快速录制指定时长的数据。

    Usage:
        import box2driver_client as bd
        data = bd.record(10)  # 录制 10 秒
        # data = {"148": [frame, ...], "228": [frame, ...]}
    """
    client = Box2DriverClient(ws_url=ws_url)
    return client.record(duration)


def stream(ws_url: str = "ws://localhost:8765") -> Iterator[Tuple[str, dict]]:
    """快速迭代器。

    Usage:
        import box2driver_client as bd
        for dev_id, frame in bd.stream():
            print(dev_id, frame['servos'])
    """
    client = Box2DriverClient(ws_url=ws_url)
    return client.stream()


# ============================================
# === CLI 测试
# ============================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Box2Driver Client 测试工具")
    parser.add_argument(
        "--url", default="ws://localhost:8765", help="WebSocket URL"
    )
    parser.add_argument(
        "--record", type=float, default=0, metavar="SEC", help="录制指定秒数并输出 JSON"
    )
    parser.add_argument(
        "--csv", action="store_true", help="以 CSV 格式输出实时数据"
    )
    args = parser.parse_args()

    if args.record > 0:
        print(f"录制 {args.record} 秒...")
        data = record(args.record, ws_url=args.url)
        total = sum(len(v) for v in data.values())
        print(f"录制完成: {len(data)} 个设备, {total} 帧")
        out_file = f"recording_{int(time.time())}.json"
        with open(out_file, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"已保存到 {out_file}")
    elif args.csv:
        print("time,dev,role,seq,servo_id,pos,spd,load")
        for dev_id, frame in stream(ws_url=args.url):
            t = frame["pc_time"]
            for s in frame["servos"]:
                print(
                    f"{t:.3f},{dev_id},{frame['role']},{frame['seq']},"
                    f"{s['id']},{s['pos']},{s['spd']},{s['load']}"
                )
    else:
        print(f"连接 {args.url} ...")
        print("实时数据 (Ctrl+C 退出):\n")

        def show(dev_id, frame):
            servos = " ".join(
                f"#{s['id']}:{s['pos']}" for s in frame["servos"]
            )
            print(
                f"[{frame['pc_time']:.2f}] dev={dev_id} "
                f"role={frame['role']} seq={frame['seq']:3d}  {servos}"
            )

        client = Box2DriverClient(ws_url=args.url)
        client.on_frame = show
        client.run()
