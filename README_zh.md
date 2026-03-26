[English](README.md) | 中文

# LeRobot-ESP32: 全无线 LeRobot 机械臂控制方案

![LeRobot-ESP32 Demo](assets/capture.png)

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

## 硬件

<div align="center">
  <a href="https://item.taobao.com/item.htm?abbucket=5&id=1030962099420">
    <img src="assets/hardware.jpg" alt="Box2AI 控制板" width="400"/>
  </a>
  <br>
  <a href="https://item.taobao.com/item.htm?abbucket=5&id=1030962099420">购买 Box2AI 控制板 (淘宝)</a>
</div>

**电路原理图：**

![Box2AI 电路原理图](assets/hardware_SchDoc.png)

### 舵机接线 — 飞特 vs 幻尔

Box2Driver v0.4.5+ 同时支持 **飞特 (Feetech)** 和 **幻尔 (Hiwonder)** 总线舵机，固件开机自动检测舵机类型。但**两种品牌的线材接头方向不同**，接线前请先识别你的舵机品牌，准备正确的线材。

![飞特 vs 幻尔线材对比](assets/Hiwonder-feetech.png)

| 品牌 | 接头方向 | 说明 |
|------|---------|------|
| **幻尔 (Hiwonder)** | 同向 | 线材两端接头朝向相同 |
| **飞特 (Feetech)** | 反向 | 线材两端接头朝向相反 |

> **警告：** 使用错误方向的线材会导致引脚顺序反接（信号/电源/GND 错位），可能损坏舵机或控制板。连接前务必确认线材与舵机品牌匹配。

## 快速开始

### 1. 安装

```bash
conda create -n box2driver python=3.12 -y
conda activate box2driver
pip install dist_pkg/box2driver-0.4.4-py3-none-any.whl
```

### 2. 启动

将 Gateway 模式的 ESP32 通过 USB 连接到电脑。首先查看设备分配的串口号：

| 平台 | 命令 | 示例输出 |
|------|------|----------|
| **Windows** (PowerShell) | `Get-CimInstance Win32_SerialPort \| Select Name, DeviceID` | `COM5` |
| **Windows** (CMD) | `mode` | `COM5` |
| **macOS** | `ls /dev/cu.usb*` | `/dev/cu.usbserial-0001` |
| **Ubuntu / Linux** | `ls /dev/ttyUSB* /dev/ttyACM*` | `/dev/ttyUSB0` |

> **提示：** Linux/macOS 下也可以在插入设备后立即运行 `dmesg | tail` 查看分配的端口。Windows 下可打开 **设备管理器 → 端口 (COM 和 LPT)** 查看。

```bash
box2driver                     # 自动检测串口，启动 Web + STS 虚拟串口
box2driver -p COM5             # 指定串口
box2driver --bridge            # 同时启动 com0com/socat 虚拟 COM 口
box2driver --no-web            # 不启动 Web，只启动虚拟串口
box2driver --list              # 列出可用串口
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

### 3. Python API 使用

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

## 固件更新

预编译固件在 `bin/` 目录下，出厂设备已预烧录固件。

将 ESP32 通过 USB 连接到电脑，先查看分配的串口号：

| 平台 | 命令 | 示例输出 |
|------|------|----------|
| **Windows** (PowerShell) | `Get-CimInstance Win32_SerialPort \| Select Name, DeviceID` | `COM5` |
| **Windows** (CMD) | `mode` | `COM5` |
| **macOS** | `ls /dev/cu.usb*` | `/dev/cu.usbserial-0001` |
| **Ubuntu / Linux** | `ls /dev/ttyUSB* /dev/ttyACM*` | `/dev/ttyUSB0` |

将下方命令中的 `COM5` 替换为你实际的串口号。

**烧录固件**

需要烧录全部 3 个文件（引导程序 + 分区表 + 固件）：

| 文件 | 地址 | 说明 |
|------|------|------|
| box2driver_v0.4.5_bootloader.bin | 0x1000 | 引导程序 |
| box2driver_v0.4.5_partitions.bin | 0x8000 | 分区表 |
| box2driver_v0.4.5_firmware.bin | 0x10000 | 应用固件 |

```bash
pip install esptool
esptool.py --chip esp32 --port /dev/ttyUSB0 --baud 921600 write_flash \
    0x1000 bin/box2driver_v0.4.5_bootloader.bin \
    0x8000 bin/box2driver_v0.4.5_partitions.bin \
    0x10000 bin/box2driver_v0.4.5_firmware.bin
```

> **警告：** 不要只烧录 firmware.bin — 引导程序和分区表必须与固件版本匹配，否则板子可能无法启动。

如果烧录后板子没有反应，先完全擦除再重新烧录：

```bash
esptool.py --chip esp32 --port COM5 erase_flash
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x1000 bin/box2driver_v0.4.5_bootloader.bin \
    0x8000 bin/box2driver_v0.4.5_partitions.bin \
    0x10000 bin/box2driver_v0.4.5_firmware.bin
```

> 注意：`erase_flash` 会清除 NVS 存储（已保存的模式、绑定关系等），烧录后需要重新配置设备模式。

或使用 `bin/flash_download_tool/flash_download_tool_3.9.9_R2.exe` (Windows GUI)。

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

### 录制与回放

```bash
python examples/record_replay.py
```

### LeRobot 集成

完整 LeRobot AI 管线：示教数据采集 → 策略训练 → 推理部署。

```bash
# 安装 LeRobot
pip install -r requirements.txt
git clone https://github.com/huggingface/lerobot.git
cd lerobot && pip install -e .

# 数据采集示例
python scripts/example_collect.py
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

## 目录结构

```
lerobot-esp32/
├── assets/                              # 图片资源
│   ├── capture.png                      # 产品演示图
│   ├── half_encode.jpg                  # 半位校准姿态参考图
│   ├── Hiwonder-feetech.png            # 飞特/幻尔线材对比图
│   ├── hardware.jpg                     # 硬件实物图
│   └── hardware_SchDoc.png             # 电路原理图
├── bin/                                 # 预编译固件 (v0.4.5)
│   ├── box2driver_v0.4.5_firmware.bin
│   ├── box2driver_v0.4.5_bootloader.bin
│   ├── box2driver_v0.4.5_partitions.bin
│   └── flash_download_tool/             # 乐鑫烧录工具
├── dist_pkg/                            # 预编译 Python 包
│   └── box2driver-0.4.4-py3-none-any.whl
├── scripts/                             # 工具脚本
│   ├── check_env.py                     # 环境检查工具
│   ├── check_firmware.py                # 固件版本检查
│   ├── example_collect.py               # 数据采集示例
│   ├── compare_servo_protocol.py        # STS 协议调试工具
│   ├── set_motors_half_encode.py        # 电机编码偏置校准
│   ├── start_servo_bridge.bat           # Windows 虚拟串口启动
│   └── start_servo_bridge.sh            # Linux/macOS 虚拟串口启动
├── examples/                            # 示例脚本
│   ├── keyboard_ik_control.py           # 键盘 IK/关节空间 控制
│   ├── record_replay.py                 # 轨迹录制与回放
│   ├── so100_kinematics.py              # SO-100 运动学示例
│   └── docs/
│       └── virtual_com_setup.md         # 虚拟串口配置指南
├── requirements.txt                     # 依赖
└── VERSION
```

## 常见问题 (FAQ)

### Leader 和 Follower 两臂姿态不一致

这是因为电机安装时的编码值不一致导致的。可以通过校准脚本写入电机编码偏置来修复。

**操作步骤：**

1. 将机械臂所有舵机手动摆到半位姿态（每个关节处于机械中心位置），参考下图：

   ![半位校准姿态](assets/half_encode.jpg)

2. 使用 **USB-to-TTL 调试板**（非 Box2Driver 控制板）将**单条机械臂**连接到电脑，每次只连一条臂，然后运行：

   ```bash
   pip install scservo-sdk pyserial
   python scripts/set_motors_half_encode.py -p COM5          # 自动检测舵机类型
   python scripts/set_motors_half_encode.py -p COM5 -t feetech   # 强制飞特模式
   python scripts/set_motors_half_encode.py -p COM5 -t hiwonder  # 强制幻尔模式
   python scripts/set_motors_half_encode.py -p COM5 --max-id 8   # 扫描 ID 1~8
   ```

3. 脚本会自动：
   - 自动检测舵机类型（飞特或幻尔），也可手动指定
   - 自动扫描并发现所有已连接的电机
   - 清除所有电机的现有编码偏置
   - 读取每个电机的当前位置
   - 计算并写入偏置，使当前位置映射到中心值（飞特: 2048，幻尔: 500）
   - 持续打印位置用于验证（Ctrl+C 退出）

4. **Leader 和 Follower 两条臂都需要执行此脚本**，确保编码基准一致。

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.4.5 | 2026-03-23 | 新增幻尔 (Hiwonder) LX 系列总线舵机支持，启动自动检测舵机类型 (飞特/幻尔)，115200 波特率单个通信模式 |
| v0.4.4 | 2026-03-19 | RGB 双灯系统 (模式+状态)、修复 RMT 通道冲突、修复模式切换力矩竞态、集成 STS TCP 虚拟串口 |
| v0.4.3 | 2026-03-18 | 虚拟 COM 桥接完整 STS 协议、Gateway 控制稳定性、WS 断连保护 |
| v0.4.2 | 2026-03-18 | 虚拟舵机串口桥接 (跨平台一键启动)、多设备自动检测、com0com/socat 支持 |
| v0.4.1 | 2026-03-17 | 30Hz 参数修正、快速使用章节、表格优化、键盘 IK 控制示例 |

如果对你有帮助，请点一下 Star！

## 许可证

Apache 2.0 License

## 相关链接

- [LeRobot](https://github.com/huggingface/lerobot)
- [lerobot-kinematics](https://github.com/box-robotics/lerobot-kinematics)
