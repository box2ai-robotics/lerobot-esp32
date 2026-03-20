# Lerobot-ESP32: Box2Driver D1 Python Tools & Firmware

Box2Driver D1 机械臂的 PC 端 Python 工具集 + 预编译固件 + 用户说明书。

配合 [Box2Driver D1 固件仓库](https://github.com/nicekwell/Box2Driver_D1_joycon) 使用，提供：
- **虚拟舵机串口桥接** — 一键将 ESP32 设备映射为虚拟 COM 口，FD 软件直连
- **Gateway Dashboard** — 串口 → WebSocket → 浏览器实时监控
- **Python Client API** — 数据读取、控制、录制回放
- **LeRobot 集成** — 数据集采集 + 模型部署
- **键盘 IK 控制** — 笛卡尔/关节空间键盘遥操作
- **JoyCon IK 桥接** — Joy-Con 姿态 → IK → 机械臂
- **预编译固件** — 开箱即烧录

## 目录结构

```
Lerobot-ESP32/
├── scripts/                          # 核心脚本
│   ├── virtual_servo_bridge.py       # 虚拟舵机串口桥接 (一键启动入口)
│   ├── start_servo_bridge.bat        # Windows 双击启动
│   ├── start_servo_bridge.sh         # Linux/macOS 启动
│   ├── gateway_dashboard.py          # Gateway Web Dashboard 服务端
│   ├── gateway_dashboard.html        # Dashboard 前端页面
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
│   ├── box2driver_v0.4.4_firmware.bin      # 应用固件 (更新只烧这个)
│   ├── box2driver_v0.4.4_bootloader.bin    # 引导程序 (首次烧录)
│   └── box2driver_v0.4.4_partitions.bin    # 分区表 (首次烧录)
├── flash_download_tool/              # 乐鑫烧录工具
├── ESP32-CAM/                        # ESP32-CAM 摄像头资料
├── requirements.txt                  # 基础依赖
├── requirements-lerobot.txt          # LeRobot 完整依赖
└── VERSION
```

## 快速开始

### 1. 创建环境

```bash
conda create -n box2driver python=3.11 -y
conda activate box2driver
pip install -r requirements.txt
python scripts/check_env.py
```

### 2. 虚拟舵机串口桥接 (一键启动)

无需理解底层协议，一条命令将所有 ESP32 设备映射为虚拟 COM 口，用 FD 软件或 scservo_sdk 直接控制。

**方式一: Gateway Dashboard + 虚拟串口 (推荐)**

```bash
# 一条命令启动所有功能：Web 监控 + STS TCP + com0com/socat 虚拟串口
python scripts/gateway_dashboard.py --bridge
```

**方式二: 纯虚拟串口 (不启动 Web)**

```bash
# 只启动串口数据+虚拟串口桥接，不启动 Web 界面
python scripts/gateway_dashboard.py --no-web
```

**方式三: 独立桥接脚本 (连接已运行的 Dashboard)**

```bash
# 先启动 Dashboard
python scripts/gateway_dashboard.py

# 另开终端，一键启动虚拟串口桥接
python scripts/virtual_servo_bridge.py
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

### 3. 启动 Gateway Dashboard

将 Gateway 模式的 ESP32 通过 USB 连接到电脑：

```bash
cd scripts
python gateway_dashboard.py            # 自动检测 CP210x 串口
python gateway_dashboard.py -p COM5    # 指定串口
python gateway_dashboard.py --bridge   # 同时启动 com0com/socat 虚拟串口
python gateway_dashboard.py --no-web   # 不启动 Web，只启动虚拟串口
python gateway_dashboard.py --list     # 列出可用串口
```

自动检测串口逻辑：优先查找 Silicon Labs CP210x USB to UART Bridge，如果只有一个则直接选择，多个时才提示用户选择。

浏览器会自动打开 `http://localhost:8080`，可看到所有在线设备的实时数据。

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
python scripts/gateway_dashboard.py -p COM5 --port 8080
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

## 固件烧录

预编译固件在 `bin/` 目录下。

### 更新固件 (出厂已烧录过)

出厂已完整烧录过一次，后续版本更新**只需烧录 firmware.bin 一个文件**：

```bash
pip install esptool
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x10000 bin/box2driver_v0.4.4_firmware.bin
```

或使用乐鑫烧录工具：firmware.bin → 地址 0x10000

### 首次完整烧录 (新板子)

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

## 安装指南

### 基础安装 (Gateway + Client + 虚拟串口)

```bash
conda create -n box2driver python=3.11 -y
conda activate box2driver
pip install -r requirements.txt
```

### LeRobot 完整安装

```bash
conda create -n box2driver-lerobot python=3.11 -y
conda activate box2driver-lerobot
pip install -r requirements-lerobot.txt
git clone https://github.com/huggingface/lerobot.git
cd lerobot && pip install -e .
```

### 虚拟串口驱动安装

**Windows (com0com)**:
1. 下载安装 [com0com](https://sourceforge.net/projects/com0com/)
2. 以管理员身份运行 CMD：
   ```
   "C:\Program Files (x86)\com0com\setupc.exe" install PortName=COM50 PortName=COM51
   ```
3. 如需多个设备，创建更多端口对：
   ```
   "C:\Program Files (x86)\com0com\setupc.exe" install PortName=COM52 PortName=COM53
   ```

**Ubuntu 20.04 / 22.04 / 24.04**:
```bash
sudo apt install -y socat
```

**macOS**:
```bash
brew install socat
```

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
