# Box2Driver D1 Firmware Binaries (v0.4.2)

当前固件版本: **v0.4.2** (详见 `../VERSION`)

## 文件说明

| 文件 | 烧录地址 | 说明 |
|------|---------|------|
| box2driver_v0.4.2_bootloader.bin | 0x1000 | 引导程序 (仅首次烧录) |
| box2driver_v0.4.2_partitions.bin | 0x8000 | 分区表 (仅首次烧录) |
| box2driver_v0.4.2_firmware.bin | 0x10000 | 应用固件 |

## 更新固件 (出厂已烧录过)

出厂已完整烧录过一次，后续版本更新**只需烧录 firmware.bin 一个文件**：

```bash
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x10000 box2driver_v0.4.2_firmware.bin
```

## 首次完整烧录 (新板子)

需要烧录全部 3 个文件：

```bash
esptool.py --chip esp32 --port COM5 --baud 921600 write_flash \
    0x1000 box2driver_v0.4.2_bootloader.bin \
    0x8000 box2driver_v0.4.2_partitions.bin \
    0x10000 box2driver_v0.4.2_firmware.bin
```

或使用 `../flash_download_tool/flash_download_tool_3.9.9_R2.exe` (Windows GUI)。
