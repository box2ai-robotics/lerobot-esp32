#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
Box2Driver 数据采集示例

演示如何使用 box2driver_client 进行数据集采集和预处理。

用法：
    # 1. 先启动 gateway_dashboard.py
    python gateway_dashboard.py -p COM36

    # 2. 再运行本脚本
    python example_collect.py                    # 交互式录制
    python example_collect.py --duration 30      # 录制 30 秒
    python example_collect.py --csv              # 实时输出 CSV
    python example_collect.py --numpy            # 录制并转为 numpy 数组
"""

import argparse
import json
import sys
import time
from pathlib import Path

# 将 scripts 目录加入路径
sys.path.insert(0, str(Path(__file__).parent))
from box2driver_client import Box2DriverClient


def collect_interactive():
    """交互式录制：按 Enter 开始/停止"""
    client = Box2DriverClient()
    print("连接 Gateway...")
    devices = client.wait_for_devices(count=1, timeout=15)
    print(f"发现 {len(devices)} 个设备: {devices}")

    recordings = []
    idx = 0

    while True:
        input(f"\n按 Enter 开始第 {idx + 1} 段录制 (输入 q 退出): ")
        print("录制中... 按 Enter 停止")

        frames = []
        recording = True

        def on_data(dev_id, frame):
            if recording:
                frames.append({"dev": dev_id, **frame})

        client.on_frame = on_data
        if not client.connected:
            client.start()

        input()
        recording = False

        total = len(frames)
        duration = frames[-1]["pc_time"] - frames[0]["pc_time"] if total > 1 else 0
        print(f"录制完成: {total} 帧, {duration:.1f}s")

        recordings.append(frames)
        idx += 1

        save = input("保存? (y/n, 默认 y): ").strip().lower()
        if save == "q":
            break
        if save != "n":
            filename = f"dataset_{int(time.time())}_{idx}.json"
            with open(filename, "w") as f:
                json.dump(frames, f, ensure_ascii=False)
            print(f"已保存: {filename}")

    client.stop()
    return recordings


def collect_timed(duration: float, output: str = None):
    """定时录制"""
    client = Box2DriverClient()
    print(f"录制 {duration} 秒...")

    data = client.record(duration)
    client.stop()

    total = sum(len(v) for v in data.values())
    print(f"完成: {len(data)} 设备, {total} 帧")

    out = output or f"dataset_{int(time.time())}.json"
    with open(out, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"已保存: {out}")

    return data


def collect_to_numpy(duration: float):
    """录制并转为 numpy 数组 (用于模型训练)"""
    try:
        import numpy as np
    except ImportError:
        print("错误: pip install numpy")
        return

    client = Box2DriverClient()
    print(f"录制 {duration} 秒并转为 numpy 数组...")

    data = client.record(duration)
    client.stop()

    for dev_id, frames in data.items():
        if not frames:
            continue

        # 提取 servo IDs
        servo_ids = sorted({s["id"] for f in frames for s in f.get("servos", [])})
        n_frames = len(frames)
        n_servos = len(servo_ids)
        id_to_idx = {sid: i for i, sid in enumerate(servo_ids)}

        # 构建数组: [n_frames, n_servos, 3] (pos, spd, load)
        arr = np.zeros((n_frames, n_servos, 3), dtype=np.float32)
        timestamps = np.zeros(n_frames, dtype=np.float64)

        for i, frame in enumerate(frames):
            timestamps[i] = frame.get("pc_time", 0)
            for s in frame.get("servos", []):
                j = id_to_idx.get(s["id"])
                if j is not None:
                    arr[i, j, 0] = s.get("pos", 0)
                    arr[i, j, 1] = s.get("spd", 0)
                    arr[i, j, 2] = s.get("load", 0)

        out = f"dataset_{dev_id}_{int(time.time())}.npz"
        np.savez(
            out,
            data=arr,
            timestamps=timestamps,
            servo_ids=np.array(servo_ids),
            dev_id=dev_id,
        )
        hz = n_frames / (timestamps[-1] - timestamps[0]) if n_frames > 1 else 0
        print(
            f"设备 {dev_id}: shape={arr.shape}, "
            f"servo_ids={servo_ids}, {hz:.1f}Hz → {out}"
        )


def stream_csv():
    """实时 CSV 输出（可用管道重定向到文件）"""
    print("time,dev,role,seq,servo_id,pos,spd,load", flush=True)
    client = Box2DriverClient()
    for dev_id, frame in client.stream():
        t = frame["pc_time"]
        for s in frame["servos"]:
            print(
                f"{t:.3f},{dev_id},{frame['role']},{frame['seq']},"
                f"{s['id']},{s['pos']},{s['spd']},{s['load']}",
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Box2Driver 数据采集")
    parser.add_argument("--duration", type=float, help="录制秒数")
    parser.add_argument("--output", "-o", help="输出文件名")
    parser.add_argument("--csv", action="store_true", help="实时 CSV 输出")
    parser.add_argument("--numpy", action="store_true", help="录制并转 numpy (.npz)")
    parser.add_argument("--interactive", action="store_true", help="交互式录制")
    args = parser.parse_args()

    if args.csv:
        stream_csv()
    elif args.numpy:
        collect_to_numpy(args.duration or 10)
    elif args.interactive:
        collect_interactive()
    elif args.duration:
        collect_timed(args.duration, args.output)
    else:
        # 默认交互式
        collect_interactive()
