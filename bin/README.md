# Box2Driver D1 Firmware Binaries (v0.4.5)

当前固件版本: **v0.4.5** (详见 `../VERSION`)

## 文件说明

最新固件在 `v0_4_5/` 目录下：

| 文件 | 烧录地址 | 说明 |
|------|---------|------|
| box2driver_v0.4.5_bootloader.bin | 0x1000 | 引导程序 (仅首次烧录) |
| box2driver_v0.4.5_partitions.bin | 0x8000 | 分区表 (仅首次烧录) |
| box2driver_v0.4.5_firmware.bin | 0x10000 | 应用固件 |

## 更新固件 (出厂已烧录过)

出厂已完整烧录过一次，后续版本更新**只需烧录 firmware.bin 一个文件**：

```bash
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x10000 v0_4_5/box2driver_v0.4.5_firmware.bin
```

## 首次完整烧录 (新板子)

需要烧录全部 3 个文件：

```bash
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x1000 v0_4_5/box2driver_v0.4.5_bootloader.bin \
    0x8000 v0_4_5/box2driver_v0.4.5_partitions.bin \
    0x10000 v0_4_5/box2driver_v0.4.5_firmware.bin
```

或使用 `../flash_download_tool/flash_download_tool_3.9.9_R2.exe` (Windows GUI)。

## 更新日志

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.4.5 | 2026-03-23 | 新增幻尔 (Hiwonder) LX 系列舵机支持，自动检测舵机类型 |
| v0.4.4 | 2026-03-19 | RGB 双灯系统、RMT 通道冲突修复、集成 STS TCP 虚拟串口 |

## 注意：幻尔舵机接线

幻尔舵机的端子线序与飞特不同，接入前需要**交换白色和黑色线**（串口总线端子左右两边的线对调），否则可能导致通信失败或损坏。
