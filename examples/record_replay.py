#!/usr/bin/env python3
"""
录制 & 回放示例 — 通过 Gateway WebSocket 录制机械臂轨迹并回放

功能:
  1. record  — 录制 Leader 手动操作的关节轨迹，保存为 JSON
  2. replay  — 加载 JSON 轨迹，回放到 Follower
  3. live    — 实时显示所有设备舵机位置

前置条件:
  - Gateway ESP32 已连接 PC (USB)
  - gateway_dashboard.py 已启动: python scripts/gateway_dashboard.py
  - Leader 和 Follower 已配对上线

用法:
  # 录制 10 秒 (自动保存到 recordings/ 目录)
  python examples/record_replay.py record --duration 10

  # 录制指定设备
  python examples/record_replay.py record --duration 10 --dev 228

  # 回放 (播放 1 次)
  python examples/record_replay.py replay recordings/rec_20260317_143000.json

  # 回放 3 次, 1.5 倍速
  python examples/record_replay.py replay recordings/rec_20260317_143000.json --loops 3 --speed 1.5

  # 实时监控
  python examples/record_replay.py live

依赖:
  pip install websockets
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 添加 scripts 目录到搜索路径
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from box2driver_client import Box2DriverClient


RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "recordings"


def cmd_record(args):
    """录制轨迹"""
    client = Box2DriverClient(ws_url=args.url)

    print(f"连接 Gateway: {args.url}")
    try:
        devs = client.wait_for_devices(count=1, timeout=5)
    except TimeoutError:
        print("错误: 未发现设备，请确认 Gateway 已连接且有设备在线")
        return

    print(f"发现 {len(devs)} 个设备: {devs}")

    # 列出设备信息
    for dev_id in devs:
        frame = client.get_latest(dev_id)
        role_names = {0: "Follower", 1: "Leader", 2: "M-Leader", 3: "Gateway", 4: "JoyCon"}
        role = role_names.get(frame.get("role", -1), "?")
        n_servos = len(frame.get("servos", []))
        print(f"  dev={dev_id} role={role} servos={n_servos}")

    # 确定录制目标
    dev_ids = [str(args.dev)] if args.dev else None
    if dev_ids:
        print(f"\n录制目标: dev={dev_ids}")
    else:
        print(f"\n录制目标: 所有设备")

    print(f"录制时长: {args.duration}s, 采样率: {args.fps}Hz")
    input("按 Enter 开始录制...")

    print("录制中...")
    trajectory = client.record_trajectory(
        duration=args.duration,
        dev_ids=dev_ids,
        fps=args.fps,
    )

    if not trajectory:
        print("错误: 未录制到任何数据")
        client.stop()
        return

    # 保存
    RECORDINGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"rec_{timestamp}.json"
    filepath = RECORDINGS_DIR / filename

    client.save_trajectory(trajectory, str(filepath))

    # 统计
    for dev_id, frames in trajectory.items():
        if frames:
            duration = frames[-1]["t"] - frames[0]["t"]
            print(f"  dev={dev_id}: {len(frames)} 帧, {duration:.1f}s, "
                  f"{len(frames)/max(duration,0.001):.1f}Hz")

    client.stop()
    print(f"\n文件: {filepath}")


def cmd_replay(args):
    """回放轨迹"""
    filepath = Path(args.file)
    if not filepath.exists():
        # 尝试在 recordings 目录查找
        alt = RECORDINGS_DIR / filepath.name
        if alt.exists():
            filepath = alt
        else:
            print(f"错误: 文件不存在: {filepath}")
            return

    client = Box2DriverClient(ws_url=args.url)

    print(f"加载轨迹: {filepath}")
    trajectory = client.load_trajectory(str(filepath))

    if not trajectory:
        print("错误: 轨迹数据为空")
        return

    # 显示轨迹信息
    for dev_id, frames in trajectory.items():
        if frames:
            duration = frames[-1]["t"] - frames[0]["t"]
            n_servos = len(frames[0].get("servos", []))
            print(f"  dev={dev_id}: {len(frames)} 帧, {duration:.1f}s, {n_servos} 舵机")

    # 选择回放数据源
    dev_ids = list(trajectory.keys())
    if args.dev:
        source_dev = str(args.dev)
    elif len(dev_ids) == 1:
        source_dev = dev_ids[0]
    else:
        # 多设备：优先选 Leader(1) 的轨迹回放
        source_dev = dev_ids[0]
        for did in dev_ids:
            frames = trajectory[did]
            if frames and frames[0].get("role") == 1:
                source_dev = did
                break
        print(f"多设备录制，使用 dev={source_dev} 的轨迹回放")

    traj_data = trajectory[source_dev]
    if not traj_data:
        print("错误: 选定设备无轨迹数据")
        return

    print(f"\n连接 Gateway: {args.url}")
    try:
        client.wait_for_devices(count=1, timeout=5)
    except TimeoutError:
        print("错误: 未发现设备")
        return

    duration = traj_data[-1]["t"] - traj_data[0]["t"]
    total_frames = len(traj_data)
    print(f"\n回放: {total_frames} 帧, {duration:.1f}s, "
          f"loops={args.loops}, speed={args.speed}x")
    if args.loops == 0:
        print("(无限循环, Ctrl+C 停止)")
    input("按 Enter 开始回放...")

    print("回放中...")
    t0 = time.time()
    sent = client.replay_trajectory(
        trajectory=traj_data,
        loops=args.loops,
        speed=args.speed,
    )
    elapsed = time.time() - t0
    print(f"\n回放完成: 发送 {sent} 帧, 耗时 {elapsed:.1f}s")

    # 回放结束释放控制权
    client.release_control()
    print("已释放 Gateway 控制权")

    client.stop()


def cmd_live(args):
    """实时监控"""
    client = Box2DriverClient(ws_url=args.url)

    print(f"连接 Gateway: {args.url}")
    print("实时舵机位置 (Ctrl+C 退出)\n")

    role_names = {0: "Fol", 1: "Led", 2: "MLd", 3: "GW", 4: "JC"}
    frame_count = 0

    def on_frame(dev_id, frame):
        nonlocal frame_count
        frame_count += 1
        if frame_count % 30 != 1:  # 约 1Hz 输出
            return
        role = role_names.get(frame.get("role", -1), "?")
        servos = frame.get("servos", [])
        pos_str = " ".join(f"#{s['id']}:{s['pos']:4d}" for s in servos)
        print(f"[{role}] dev={dev_id:>4s} seq={frame.get('seq',0):4d}  {pos_str}")

    client.on_frame = on_frame
    try:
        client.run()
    except KeyboardInterrupt:
        print(f"\n\n总帧数: {frame_count}")


def main():
    parser = argparse.ArgumentParser(
        description="Box2Driver 录制 & 回放工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python record_replay.py record --duration 10           录制 10 秒
  python record_replay.py replay recordings/rec.json     回放
  python record_replay.py replay rec.json --loops 0      无限循环回放
  python record_replay.py live                           实时监控
        """,
    )
    parser.add_argument("--url", default="ws://localhost:8765",
                        help="Gateway WebSocket URL (默认: ws://localhost:8765)")

    sub = parser.add_subparsers(dest="command", help="子命令")

    # record
    p_rec = sub.add_parser("record", help="录制轨迹")
    p_rec.add_argument("--duration", "-d", type=float, default=10,
                        help="录制秒数 (默认: 10)")
    p_rec.add_argument("--fps", type=int, default=30,
                        help="采样帧率 (默认: 30)")
    p_rec.add_argument("--dev", type=str, default=None,
                        help="只录制指定设备 ID")

    # replay
    p_play = sub.add_parser("replay", help="回放轨迹")
    p_play.add_argument("file", help="轨迹 JSON 文件路径")
    p_play.add_argument("--loops", "-l", type=int, default=1,
                         help="循环次数 (0=无限, 默认: 1)")
    p_play.add_argument("--speed", "-s", type=float, default=1.0,
                         help="播放速度 (默认: 1.0)")
    p_play.add_argument("--dev", type=str, default=None,
                         help="使用指定设备的轨迹数据")

    # live
    sub.add_parser("live", help="实时监控舵机位置")

    args = parser.parse_args()

    if args.command == "record":
        cmd_record(args)
    elif args.command == "replay":
        cmd_replay(args)
    elif args.command == "live":
        cmd_live(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
