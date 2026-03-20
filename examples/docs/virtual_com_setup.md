# Windows / Linux 虚拟 COM 口设置指南

> Box2Driver D1 机械臂 — 让远端无线机械臂在电脑上像 USB 直连舵机一样使用

## 概述

Box2Driver Gateway 通过 ESP-NOW 无线接收机械臂数据，PC 端脚本将其模拟成标准的飞特 ST3215 串口总线。
最终效果：**飞特官方上位机 / LeRobot / scservo_sdk 选择一个 COM 口，和 USB 直连舵机驱动板完全一致。**

### 系统架构

```
                    ESP-NOW 无线              USB 串口
  Follower/Leader ──────────────► Gateway ESP32 ──────► PC
                                                         │
                                                         ▼
                                            gateway_dashboard.py
                                            (读取 JSON → WebSocket 推送)
                                                         │
                                                         ▼
                                            virtual_servo_bridge.py
                                            (STS 二进制协议 ↔ WebSocket JSON)
                                                         │
                                                   虚拟串口对 (VSPD / com0com)
                                                   COM1 ←──────→ COM2
                                                    │               │
                                                 脚本端          用户端
                                                               (飞特上位机 /
                                                                LeRobot /
                                                                scservo_sdk)
```

### 为什么需要虚拟串口驱动？

Windows 上真正的 COM 口需要内核驱动支持串口 IOCTL (SetCommState 等)。
经验证，纯用户态方案 (Named Pipe + DefineDosDevice) **无法**被 pyserial 和飞特上位机识别为串口，
因为 Named Pipe 不支持 `SetCommState`/`GetCommState` 等串口配置 API。
因此 Windows 上**必须使用虚拟串口驱动**。

---

## Windows 方案对比

| 方案 | 费用 | 驱动签名 | 难度 | 适用场景 |
|------|------|----------|------|----------|
| **VSPD** (推荐入门) | 14天试用 / 商业 | 已签名，直接安装 | 低 | 快速验证、短期使用 |
| **com0com** (推荐长期) | 免费开源 | 需开启测试签名 | 中 | 长期开发、免费部署 |
| **TCP 模式** | 免费 | 无需驱动 | 最低 | 仅限 Python (LeRobot/scservo_sdk) |

---

## 方案 A: VSPD — 推荐快速入门 (5分钟完成)

Virtual Serial Port Driver by Electronic Team，已签名驱动，Win10/11 直接安装无需关安全启动。

### 1. 一键安装

打开 **管理员终端 (PowerShell 或 CMD)**:

```cmd
winget install ElectronicTeam.VirtualSerialPortDriver --accept-package-agreements
```

或从官网手动下载安装: https://www.virtual-serial-port.org/

> 安装过程会弹出 UAC 授权窗口，点"是"。

### 2. 创建虚拟串口对

安装完成后打开 VSPD 软件 (开始菜单搜索 "Virtual Serial Port Driver"):

1. 左侧 **"First port"** 下拉选择一个**空闲 COM 号**（如 COM1）
2. 右侧 **"Second port"** 选另一个（如 COM2）
3. 点击 **"Add Pair"** 按钮
4. 底部列表出现 `COM1 <-> COM2` 即成功

> **端口号选择建议**: 避开系统已占用的 COM 口。可在设备管理器 → 端口 查看哪些被占用。
> 推荐选 COM10/COM11 或更大的数字，避免和实体设备冲突。

### 3. 验证

```powershell
# PowerShell 查看可用串口
[System.IO.Ports.SerialPort]::GetPortNames()
```

应该能看到你刚创建的两个 COM 口。

### 4. 注意事项

- VSPD **14天免费试用**，到期后需购买许可证 (~$139)
- 试用期间功能完整，无任何限制
- 电脑重启后 VSPD 服务自动启动，串口对自动恢复
- 卸载: `winget uninstall ElectronicTeam.VirtualSerialPortDriver`

---

## 方案 B: com0com — 免费开源永久使用

com0com 是开源的 Windows 虚拟串口驱动，完全免费，但在 Win10/11 上需要开启**测试签名模式**。

### 1. 开启测试签名模式 (必须先做)

以 **管理员身份** 打开 CMD:

```cmd
bcdedit /set testsigning on
```

**重启电脑。** 重启后桌面右下角会出现 "测试模式" 水印（不影响使用，纯显示）。

> 如果开启了 **Secure Boot (安全启动)**，需要先在 BIOS 中关闭，否则 `bcdedit` 命令会被拒绝。
> 进入 BIOS 的方法: 开机时按 F2/Del/F12 (品牌不同按键不同) → Security → Secure Boot → Disabled。

### 2. 下载

从 SourceForge 下载签名版本:

https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/

下载文件: **`com0com-3.0.0.0-i386-and-amd64-signed.zip`**

### 3. 安装

1. 解压 zip 文件
2. 进入 `amd64` 文件夹 (64位系统) 或 `i386` (32位系统)
3. 右键 **`setup.exe`** → 以管理员身份运行
4. 按提示完成安装
5. 如果弹出 "Windows 无法验证此驱动" 警告 → 选择 **"始终安装此驱动"**

### 4. 创建虚拟串口对

**方式一: GUI (推荐)**

1. 开始菜单 → com0com → **Setup**
2. 点击 **"Add Pair"**
3. 左侧 Port name 改为 `COM10`，右侧改为 `COM11`
4. **两边都勾选** "enable buffer overrun"
5. 点击 **"Apply"**

**方式二: 命令行**

```cmd
:: 管理员 CMD
cd "C:\Program Files (x86)\com0com"

:: 创建串口对
setupc install PortName=COM10 PortName=COM11

:: 查看已创建的串口对
setupc list

:: 删除串口对 (如需)
setupc uninstall 0
```

### 5. 验证

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
```

应该能看到 COM10 和 COM11。

### 6. 用完后恢复 (可选)

如果不想保留测试签名水印:

```cmd
:: 管理员 CMD
bcdedit /set testsigning off
```

重启后水印消失。**但 com0com 驱动也会失效**，需要重新开启测试签名才能使用。

---

## 方案 C: TCP 模式 — 仅限 Python，零驱动

如果只用 Python 程序 (LeRobot / scservo_sdk)，无需虚拟串口驱动。
`gateway_dashboard.py` 已内置 TCP STS 服务，每个臂自动分配端口:

```python
import serial

# pyserial 原生支持 socket:// URL
ser = serial.serial_for_url("socket://localhost:6560", baudrate=1000000, timeout=1)

# 发送 STS PING (ID=1)
ser.write(b'\xff\xff\x01\x02\x01\xfb')
resp = ser.read(6)
print(resp.hex())  # 应返回状态包
```

### scservo_sdk 使用 TCP (需 Monkey-patch)

scservo_sdk 的 PortHandler 原版不支持 `socket://` URL，需要在代码开头加一段 patch:

```python
import serial
from scservo_sdk import PortHandler

# --- Monkey-patch: 让 PortHandler 支持 socket:// URL ---
_orig_setup = PortHandler.setupPort
def _patched_setup(self, cflag_baud):
    if self.port_name.startswith("socket://"):
        if self.is_open:
            self.closePort()
        self.ser = serial.serial_for_url(
            self.port_name, baudrate=self.baudrate, timeout=0
        )
        self.is_open = True
        self.tx_time_per_byte = (1000.0 / self.baudrate) * 10.0
        return True
    return _orig_setup(self, cflag_baud)
PortHandler.setupPort = _patched_setup
# --- Patch 结束 ---

# 然后正常使用
port = PortHandler("socket://localhost:6560")
port.openPort()
port.setBaudRate(1000000)
```

> 注意: TCP 模式**不能**被飞特官方上位机使用（它只认真实 COM 口）。

---

## 使用方法 — 完整步骤

### 前提

- 已安装 VSPD 或 com0com，并创建了虚拟串口对 (如 COM1↔COM2)
- Gateway ESP32 通过 USB 连接到电脑
- 至少一个 Follower/Leader 已上电

### 步骤 1: 启动 Gateway Dashboard

```bash
conda activate box2driver
cd Lerobot-ESP32

# 自动检测 ESP32 串口并启动
python scripts/gateway_dashboard.py

# 或指定串口
python scripts/gateway_dashboard.py -p COM35
```

等待控制台出现 feedback 数据 (如 `[Serial] dev=228 ...`) 说明设备在线。

### 步骤 2: 启动虚拟串口桥接

**另开一个终端**:

```bash
conda activate box2driver
cd Lerobot-ESP32

# 连接到 WebSocket，STS 协议写入虚拟串口的"脚本端"
python scripts/virtual_servo_bridge.py -p COM1
```

等待出现 `[WS] 已连接` 和 `[WS] 发现 Follower` 说明桥接就绪。

### 步骤 3: 使用虚拟串口

**飞特官方上位机:**
1. 打开 FD 上位机软件
2. 串口选择 **COM2** (虚拟串口对的"用户端")
3. 波特率 **1000000** (1Mbps)
4. 点击 "打开串口" → "搜索"
5. 应该能看到远端机械臂上所有舵机 (ID 1~6)

**LeRobot:**
```python
from lerobot.common.robot_devices.motors.feetech import FeetechMotorsBus

# 使用虚拟串口 COM2，和直连 USB 完全一样
motors = FeetechMotorsBus(port="COM2", motors={
    "shoulder_pan":  (1, "sts3215"),
    "shoulder_lift": (2, "sts3215"),
    "elbow_flex":    (3, "sts3215"),
    "wrist_flex":    (4, "sts3215"),
    "wrist_roll":    (5, "sts3215"),
    "gripper":       (6, "sts3215"),
})
motors.connect()
positions = motors.read("Present_Position")
```

**scservo_sdk:**
```python
from scservo_sdk import PortHandler, PacketHandler

port = PortHandler("COM2")
port.openPort()
port.setBaudRate(1000000)
pkt = PacketHandler()

model, result, error = pkt.ping(port, 1)
print(f"Servo 1 model: {model}")

pos, result, error = pkt.read2ByteTxRx(port, 1, 56)  # Present_Position
print(f"Servo 1 position: {pos}")
```

---

## Linux 用户

Linux 不需要虚拟串口驱动，有两种方式:

### 方式一: socat 创建 PTY (推荐)

```bash
# 安装 socat
sudo apt install socat

# 启动 gateway
python scripts/gateway_dashboard.py -p /dev/ttyUSB0

# 另一个终端: 创建 PTY 设备，桥接到 STS TCP 端口
socat pty,raw,echo=0,link=/tmp/box2d_arm1 tcp:localhost:6560 &

# 使用
python -c "
import serial
ser = serial.Serial('/tmp/box2d_arm1', 1000000)
ser.write(b'\xff\xff\x01\x02\x01\xfb')  # PING ID=1
print(ser.read(6).hex())
"
```

### 方式二: TCP 模式 (最简单)

```python
import serial
ser = serial.serial_for_url("socket://localhost:6560", baudrate=1000000, timeout=1)
```

---

## 端口对应关系速查

| 端口 | 角色 | 谁打开 |
|------|------|--------|
| COMxx (ESP32 USB) | Gateway 硬件串口 | gateway_dashboard.py |
| COM1 (虚拟串口-脚本端) | STS 协议桥接 | virtual_servo_bridge.py |
| COM2 (虚拟串口-用户端) | 用户使用 | 飞特上位机 / LeRobot / scservo_sdk |

> COM1/COM2 可以换成任意编号，只要是同一虚拟串口对的两端即可。

---

## 故障排查

| 现象 | 原因 | 解决方案 |
|------|------|----------|
| 串口列表看不到虚拟 COM | VSPD/com0com 未安装或未创建串口对 | 打开 VSPD/com0com GUI 检查 |
| com0com 安装失败 | Win10/11 未开启测试签名 | `bcdedit /set testsigning on` + 重启 |
| virtual_servo_bridge 打开 COM 失败 | 端口被其他程序占用 | 关闭占用端口的程序，或换一个端口号 |
| 飞特上位机连接超时 | bridge 未启动或连错端口 | 确认 bridge 连"脚本端"，上位机连"用户端" |
| 搜索不到舵机 | Gateway 无设备在线 | 检查 gateway_dashboard.py 控制台有无 feedback |
| 位置读数全为 0 | feedback 数据未到达 | 等 2~3 秒让数据填充，检查设备是否上电 |
| VSPD 试用过期 | 14天到期 | 换 com0com (免费) 或用 TCP 模式 |
| Named Pipe 方案不行？ | Windows 串口需内核驱动 | 已验证不可行，必须用 VSPD/com0com |
