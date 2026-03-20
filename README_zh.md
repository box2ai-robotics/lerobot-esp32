[English](README.md) | 中文

# LeRobot-ESP32: 全无线 LeRobot 机械臂控制方案

**告别线材束缚，让 LeRobot 真正自由。**

LeRobot-ESP32 基于 ESP-NOW 无线协议，实现 LeRobot 机械臂的全无线遥操作、数据采集与 AI 部署。Leader-Follower 之间无需任何线缆连接，30Hz 同步、<5ms 延迟，配合 PC 端工具链即可完成从数据采集到模型部署的完整工作流。

## 为什么要无线化？

- 线材限制了机械臂的摆放自由度，桌面变得杂乱
- 有线连接容易因拉扯导致接触不良或舵机总线故障
- LeRobot 原生方案需要每条臂各一根 USB 线连 PC，扩展性差
- **无线化后**：Leader 和 Follower 各自独立供电，放在任意位置，开机即连

## 系统架构

```
┌─────────┐   ESP-NOW 30Hz   ┌──────────┐
│ Leader  │ ──────────────→  │ Follower │
│ (读位置) │  ←────────────── │ (写舵机)  │
└─────────┘   feedback        └──────────┘
                                   ↑
┌─────────┐   ESP-NOW            │
│ Gateway │ ←────────────────────┘
│ (USB→PC)│ ──→ Serial JSON ──→ PC
└─────────┘
     ↓
┌─────────────────────────────────────┐
│ PC 工具链                            │
│ - Web Dashboard (实时监控/控制)       │
│ - 虚拟串口桥接 (FD 软件直连)          │
│ - LeRobot 数据采集 & 模型部署         │
│ - Python API / 键盘控制 / JoyCon     │
└─────────────────────────────────────┘
```

## 主要功能

- **ESP-NOW 无线同步** — Leader→Follower 30Hz 实时位置同步，告别 USB 线材
- **虚拟舵机串口桥接** — 一键将 ESP32 设备映射为虚拟 COM 口，FD 软件直连
- **Gateway Dashboard** — 浏览器实时监控、控制、波形图
- **LeRobot 集成** — 无线数据集采集 + 模型推理部署
- **Python Client API** — 数据读取、控制、录制回放
- **键盘 IK 控制** — 笛卡尔/关节空间键盘遥操作
- **JoyCon IK 桥接** — Joy-Con 手柄姿态 → IK → 机械臂
- **预编译固件** — 开箱即烧录，无需搭建编译环境

## 目录结构

```
lerobot-esp32/
├── scripts/                          # 核心脚本
│   ├── gateway_dashboard.py          # Gateway Web Dashboard 服务端
│   ├── gateway_dashboard.html        # Dashboard 前端页面
│   ├── virtual_servo_bridge.py       # 虚拟舵机串口桥接
│   ├── start_servo_bridge.bat        # Windows 双击启动
│   ├── start_servo_bridge.sh         # Linux/macOS 启动
│   ├── box2driver_client.py          # Python Client API
│   ├── lerobot_collect.py            # LeRobot 数据集采集
│   ├── lerobot_deploy.py             # LeRobot 模型推理部署
│   ├── joycon_ik_bridge.py           # JoyCon → IK → Follower
│   ├── compare_servo_protocol.py     # STS 协议对比调试工具
│   ├── gateway_recv.py               # 简易串口接收 (调试用)
│   ├── example_collect.py            # 数据采集示例
│   ├── generate_manual.py            # 用户说明书生成器
│   └── check_env.py                  # 环境检查工具
├── examples/                         # 示例脚本
│   └── keyboard_ik_control.py        # 键盘 IK/关节空间 控制示例
├── bin/                              # 预编译固件 (v0.4.4)
│   ├── box2driver_v0.4.4_firmware.bin
│   ├── box2driver_v0.4.4_bootloader.bin
│   └── box2driver_v0.4.4_partitions.bin
├── dist_pkg/                         # 预编译 Python 包
│   └── box2driver-0.4.4-py3-none-any.whl
├── flash_download_tool/              # 乐鑫烧录工具
├── ESP32-CAM/                        # ESP32-CAM 摄像头资料
├── requirements.txt                  # 基础依赖
├── requirements-lerobot.txt          # LeRobot 完整依赖
└── VERSION
```

## 快速开始

### 1. 安装

```bash
conda create -n box2driver python=3.11 -y
conda activate box2driver
pip install dist_pkg/box2driver-0.4.4-py3-none-any.whl
```

### 2. 烧录固件

预编译固件在 `bin/` 目录下，无需搭建编译环境。

**更新固件 (出厂已烧录过)**

出厂已完整烧录过一次，后续版本更新**只需烧录 firmware.bin 一个文件**：

```bash
pip install esptool
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x10000 bin/box2driver_v0.4.4_firmware.bin
```

或使用乐鑫烧录工具：firmware.bin → 地址 0x10000

**首次完整烧录 (新板子)**

需要烧录全部 3 个文件：

| 文件 | 地址 | 说明 |
|------|------|------|
| box2driver_v0.4.4_bootloader.bin | 0x1000 | 引导程序 |
| box2driver_v0.4.4_partitions.bin | 0x8000 | 分区表 |
| box2driver_v0.4.4_firmware.bin | 0x10000 | 应用固件 |

```bash
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x1000 bin/box2driver_v0.4.4_bootloader.bin \
    0x8000 bin/box2driver_v0.4.4_partitions.bin \
    0x10000 bin/box2driver_v0.4.4_firmware.bin
```

或使用 `flash_download_tool/flash_download_tool_3.9.9_R2.exe` (Windows GUI)。

### 3. 启动

将 Gateway 模式的 ESP32 通过 USB 连接到电脑：

```bash
box2driver                     # 自动检测串口，启动 Web + STS 虚拟串口
box2driver -p COM5             # 指定串口
box2driver --bridge            # 同时启动 com0com/socat 虚拟 COM 口
box2driver --no-web            # 不启动 Web，只启动虚拟串口
box2driver --list              # 列出可用串口
```

或直接运行脚本：

```bash
python scripts/gateway_dashboard.py            # 自动检测 CP210x 串口
python scripts/gateway_dashboard.py -p COM5    # 指定串口
python scripts/gateway_dashboard.py --bridge   # 同时启动虚拟串口
```

启动后自动：
1. 检测平台和虚拟串口驱动 (Windows: com0com / Linux: socat / macOS: socat)
2. 连接 Gateway WebSocket
3. 发现所有 ESP32 设备 (Follower、Leader 等)
4. 为每个设备创建独立虚拟串口
5. 打印端口映射表

```
  Device      | MAC           | Servos    | Port
  ------------|---------------|-----------|--------------------
  Follower    | ...2D:B6:94 | ID=2      | COM51
  Leader      | ...2C:3E:28 | ID=1-6    | socket://localhost:6570
```

**Windows**: 打开 FD 软件 → 选择 COM51 → 波特率 1000000 → 搜索舵机
**Linux/macOS**: `scservo_sdk` 使用 `PortHandler('/tmp/vservo0')`

跨平台虚拟串口前置安装：

| 平台 | 安装命令 |
|------|---------|
| Windows | 安装 [com0com](https://sourceforge.net/projects/com0com/)，管理员运行: `setupc install PortName=COM50 PortName=COM51` |
| Ubuntu | `sudo apt install -y socat` |
| macOS | `brew install socat` |

### 4. Python API 使用

```python
from box2driver_client import Box2DriverClient

# 快照模式
client = Box2DriverClient()
client.start()
positions = client.get_all_positions()
client.send_positions([{"id": 1, "pos": 2048}])
client.stop()

# 迭代器模式
for dev_id, frame in client.stream():
    print(dev_id, frame['servos'])
```

## 功能详解

### Gateway Dashboard

Web 可视化面板，支持：
- 多臂实时波形图
- JoyCon 6DOF 姿态显示
- 设备列表与状态监控
- 控制面板 (力矩开关、位置发送)
- 轨迹录制与回放

```bash
box2driver -p COM5 --port 8080
```

### 键盘控制 (IK / 关节空间)

```bash
python examples/keyboard_ik_control.py               # IK 模式
python examples/keyboard_ik_control.py --mode joint   # 关节模式
python examples/keyboard_ik_control.py --mac AA:BB:CC:DD:EE:FF  # 指定目标
```

**IK 模式键位：**

| 按键 | 功能 | 按键 | 功能 |
|------|------|------|------|
| W/S | X 前进/后退 | Q/E | Roll +/- |
| A/D | Y 左/右 | G/T | Pitch +/- |
| R/F | Z 上/下 | Z/C | 夹爪 开/合 |
| 0 | 回到初始位 | ESC | 退出 |

**关节模式键位：**

| 按键 | 功能 | 按键 | 功能 |
|------|------|------|------|
| 1/Q | 关节1 底座 +/- | 4/R | 关节4 腕俯仰 +/- |
| 2/W | 关节2 肩部 +/- | 5/T | 关节5 腕翻转 +/- |
| 3/E | 关节3 肘部 +/- | 6/Y | 关节6 夹爪 +/- |

IK 模式额外依赖：
```bash
pip install pynput
git clone https://github.com/box-robotics/lerobot-kinematics.git
cd lerobot-kinematics && pip install -e .
```

### LeRobot 数据集采集

```bash
python scripts/gateway_dashboard.py -p COM5
python scripts/lerobot_collect.py --repo-id box2driver/pick_cup
python scripts/lerobot_collect.py --repo-id box2driver/pick_cup --duration 10 --num-episodes 5
```

完整依赖：
```bash
pip install -r requirements-lerobot.txt
git clone https://github.com/huggingface/lerobot.git
cd lerobot && pip install -e .
```

### LeRobot 模型部署

```bash
python scripts/lerobot_deploy.py \
    --policy-path ./outputs/train/act_box2driver/checkpoints/last/pretrained_model
```

### JoyCon IK 桥接

```bash
python scripts/joycon_ik_bridge.py
python scripts/joycon_ik_bridge.py --no-ik
```

## 5 种设备模式

通过 BOOT 键长按切换，NVS 记忆上次选择：

| 模式 | 舵机 | 核心职责 |
|------|------|----------|
| Follower | 读写 | 收 sync → 写舵机 → feedback |
| Leader | 只读 | 读位置 → sync → feedback |
| M-Leader | 只读 | 同 Leader 但广播多 Follower |
| Gateway | 不连 | ESP-NOW ↔ Serial JSON 中转 |
| JoyCon | 不连 | 蓝牙手柄 → IK → sync |

## LED 指示灯

板载 2 颗 WS2812 RGB LED（GPIO23），左灯 (LED0) 指示**当前模式**，右灯 (LED1) 指示**当前状态**。

**LED0 — 模式指示（常亮）：**

| 模式 | 颜色 |
|------|------|
| Follower | 绿色 |
| Leader | 蓝色 |
| M-Leader | 暗蓝 |
| Gateway | 紫色 |
| JoyCon | 灰色 |

**LED1 — 状态指示：**

| 状态 | 颜色 | 表现 | 触发条件 |
|------|------|------|----------|
| 搜索/掉线 | 橙色 | 闪烁 | 搜索舵机中、Leader 掉线 |
| 等待 | 蓝色 | 常亮 | 空闲等待连接 |
| 待确认 | 深蓝 | 闪烁 | 收到 Leader 握手，等待按键确认 |
| 已连接 | 绿色 | 常亮 | 绑定正常，同步运行中 |
| 被接管 | 紫色 | 常亮 | Gateway/PC 控制中 |
| 超负载 | 红色 | 两灯闪烁 | 力矩保护触发 |

> 设计原则：偏红色 = 异常/故障，偏绿色 = 正常运行，蓝色系 = 等待中，紫色 = 外部接管。

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.4.4 | 2026-03-19 | RGB 双灯系统 (模式+状态)、修复 RMT 通道冲突、修复模式切换力矩竞态、集成 STS TCP 虚拟串口 |
| v0.4.3 | 2026-03-18 | 虚拟 COM 桥接完整 STS 协议、Gateway 控制稳定性、WS 断连保护 |
| v0.4.2 | 2026-03-18 | 虚拟舵机串口桥接 (跨平台一键启动)、多设备自动检测、com0com/socat 支持 |
| v0.4.1 | 2026-03-17 | 30Hz 参数修正、快速使用章节、表格优化、键盘 IK 控制示例 |

## 许可证

Apache 2.0 License

## 相关链接

- [Box2Driver D1 固件源码](https://github.com/nicekwell/Box2Driver_D1_joycon)
- [LeRobot](https://github.com/huggingface/lerobot)
- [lerobot-kinematics](https://github.com/box-robotics/lerobot-kinematics)
