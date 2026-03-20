#!/usr/bin/env python3
# Copyright (c) 2026 boxjod / Box2AI Team
# 版权所有 (c) 2026 boxjod / Box2AI 团队
# All Rights Reserved. 保留所有权利。
# March 2026 / 2026年3月
"""
虚拟串口桥接 (com0com / socat -> STS TCP)。

为每个 STS TCP 端口创建一个 com0com/socat 虚拟串口对,
FD 软件 / scservo_sdk 可以通过真实 COM 口操作远程舵机。

依赖: sts_server (STSPortManager)
"""

import atexit
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time

import serial

from box2driver.sts_server import STSPortManager, _ROLE_NAMES


class _VirtualPortManager:
    """Cross-platform virtual serial port pair manager."""

    def __init__(self):
        self._system = platform.system()
        self._socat_procs = []
        self._pairs = []
        self._used_bridges = set()
        self._pair_idx = 0
        atexit.register(self.cleanup)

    @property
    def driver_name(self) -> str:
        if self._system == 'Windows':
            return 'com0com' if self._list_com0com_pairs() else 'TCP-only'
        return 'socat' if shutil.which('socat') else 'TCP-only'

    def check(self) -> tuple:
        """Returns (can_create: bool, message: str)"""
        if self._system == 'Windows':
            pairs = self._list_com0com_pairs()
            if pairs:
                return True, f'com0com: {", ".join(f"{a}<->{b}" for a, b in pairs)}'
            return False, 'com0com not installed (https://sourceforge.net/projects/com0com/)'
        if shutil.which('socat'):
            return True, 'socat ready'
        pkg = 'brew install socat' if self._system == 'Darwin' else 'sudo apt install -y socat'
        return False, f'socat not found. Install: {pkg}'

    def create_pair(self, acm_idx=None) -> tuple:
        if self._system == 'Windows':
            return self._alloc_com0com()
        return self._create_socat(acm_idx=acm_idx)

    def release_pair(self, bridge_path: str):
        self._used_bridges.discard(bridge_path)

    def _list_com0com_pairs(self) -> list:
        if self._system != 'Windows':
            return []
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r'HARDWARE\DEVICEMAP\SERIALCOMM')
            entries = {}
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    if 'com0com' in name.lower():
                        entries[name] = value
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
            ports = sorted(entries.values(),
                           key=lambda x: int(re.search(r'\d+', x).group()))
            return [(ports[i], ports[i + 1]) for i in range(0, len(ports) - 1, 2)]
        except Exception:
            return []

    def _alloc_com0com(self):
        for bridge, user in self._list_com0com_pairs():
            if bridge not in self._used_bridges:
                self._used_bridges.add(bridge)
                self._pairs.append((bridge, user))
                return bridge, user
        return None, None

    def _find_free_ttyACM(self):
        """Find the next available /dev/ttyACM* number."""
        used = set()
        for i in range(100):
            if os.path.exists(f'/dev/ttyACM{i}'):
                used.add(i)
        for i in range(100):
            if i not in used:
                return i
        return None

    def _create_socat(self, acm_idx=None):
        idx = self._pair_idx
        self._pair_idx += 1
        bridge_link = f'/tmp/vservo{idx}_bridge'

        # Linux: create /dev/ttyACM* symlink for FD software compatibility
        # macOS: keep /tmp/vservo* (no /dev/ttyACM convention)
        socat_user_link = f'/tmp/vservo{idx}'
        user_link = socat_user_link

        for p in [bridge_link, socat_user_link]:
            try:
                if os.path.exists(p) or os.path.islink(p):
                    os.remove(p)
            except OSError:
                pass
        proc = subprocess.Popen(
            ['socat', '-d', '-d',
             f'PTY,raw,echo=0,link={bridge_link}',
             f'PTY,raw,echo=0,link={socat_user_link}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._socat_procs.append(proc)
        for _ in range(30):
            if os.path.exists(bridge_link) and os.path.exists(socat_user_link):
                break
            time.sleep(0.1)
        else:
            proc.kill()
            return None, None

        # On Linux, create /dev/ttyACM* symlink pointing to the socat PTY
        if self._system == 'Linux':
            # 使用指定的 acm_idx（基于角色分配），否则自动查找空闲编号
            if acm_idx is None:
                acm_idx = self._find_free_ttyACM()
            if acm_idx is not None:
                dev_ttyACM_link = f'/dev/ttyACM{acm_idx}'
                real_pty = os.path.realpath(socat_user_link)
                try:
                    subprocess.run(
                        ['sudo', 'ln', '-sf', real_pty, dev_ttyACM_link],
                        check=True, timeout=5)
                    subprocess.run(
                        ['sudo', 'chmod', '666', dev_ttyACM_link],
                        check=False, timeout=5)
                    user_link = dev_ttyACM_link
                    if not hasattr(self, '_dev_links'):
                        self._dev_links = []
                    self._dev_links.append(dev_ttyACM_link)
                    print(f"[VPort] Created {dev_ttyACM_link} -> {real_pty}")
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                    print(f"[VPort] Cannot create {dev_ttyACM_link} (sudo failed: {e})")
                    print(f"[VPort] Falling back to {socat_user_link}")
                    user_link = socat_user_link

        self._used_bridges.add(bridge_link)
        self._pairs.append((bridge_link, user_link))
        return bridge_link, user_link

    def cleanup(self):
        for proc in self._socat_procs:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # Clean up /dev/ttyACM* symlinks (need sudo)
        for link in getattr(self, '_dev_links', []):
            try:
                subprocess.run(['sudo', 'rm', '-f', link],
                               check=False, timeout=5)
            except Exception:
                pass
        # Clean up /tmp socat links
        for bridge, user in self._pairs:
            for p in [bridge, user]:
                if p.startswith('/tmp/vservo') or p.startswith('/tmp/ttyACM'):
                    try:
                        os.remove(p)
                    except OSError:
                        pass


def _serial_bridge_worker(bridge_port: str, tcp_port: int):
    """Bridge a virtual COM port to STS TCP port, forwarding bytes bidirectionally."""
    import select as _select
    try:
        ser = serial.Serial(bridge_port, 1000000, timeout=0)
        ser.write_timeout = 0.1
    except serial.SerialException as e:
        print(f"[Bridge] Cannot open {bridge_port}: {e}")
        return

    sock = None
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.connect(("127.0.0.1", tcp_port))
            sock.setblocking(False)
            break
        except (ConnectionRefusedError, OSError):
            if sock:
                sock.close()
            time.sleep(0.5)

    try:
        while True:
            # COM -> TCP
            try:
                waiting = ser.in_waiting
                if waiting > 0:
                    data = ser.read(waiting)
                    if data:
                        sock.sendall(data)
            except (serial.SerialException, OSError):
                # COM port closed/reopened (e.g. FD disconnect)
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1)
                try:
                    ser.open()
                except Exception:
                    time.sleep(2)
                    continue

            # TCP -> COM
            try:
                data = sock.recv(4096)
                if data:
                    ser.write(data)
                    ser.flush()
                elif data == b'':
                    # TCP connection closed, reconnect
                    sock.close()
                    time.sleep(0.5)
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.connect(("127.0.0.1", tcp_port))
                    sock.setblocking(False)
            except BlockingIOError:
                pass
            except (ConnectionResetError, BrokenPipeError, OSError):
                sock.close()
                time.sleep(0.5)
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.connect(("127.0.0.1", tcp_port))
                    sock.setblocking(False)
                except Exception:
                    time.sleep(1)
                    continue

            time.sleep(0.0001)
    except Exception:
        pass
    finally:
        try:
            ser.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


class VirtualSerialBridge:
    """Automatically bridge STS TCP ports to com0com/socat virtual COM ports."""

    def __init__(self, sts_manager: STSPortManager):
        self._sts = sts_manager
        self._pm = _VirtualPortManager()
        self._bridges = {}  # dev_id -> {"bridge": str, "user": str, "tcp": int, "thread": Thread}
        self._running = False

    def check(self) -> tuple:
        return self._pm.check()

    def start(self):
        can, msg = self._pm.check()
        if not can:
            print(f"[Bridge] 虚拟串口不可用: {msg}")
            print(f"[Bridge] 仍可通过 socket://localhost:<port> 连接")
            return
        print(f"[Bridge] 虚拟串口驱动: {msg}")
        self._running = True
        t = threading.Thread(target=self._discovery_loop, daemon=True)
        t.start()

    def _discovery_loop(self):
        # 基于角色分配 ttyACM 编号:
        #   Leader  第N个 -> ttyACM(N*2)    即 0, 2, 4, ...
        #   Follower 第N个 -> ttyACM(N*2+1) 即 1, 3, 5, ...
        #   其他角色 -> 自动查找空闲编号
        self._leader_count = 0
        self._follower_count = 0

        while self._running:
            with self._sts.lock:
                for dev_id, dp in self._sts._ports.items():
                    if dev_id not in self._bridges:
                        # 根据角色计算 ttyACM 编号
                        acm_idx = None
                        if dp.role == 1 or dp.role == 2:  # Leader / M-Leader
                            acm_idx = self._leader_count * 2
                            self._leader_count += 1
                        elif dp.role == 0:  # Follower
                            acm_idx = self._follower_count * 2 + 1
                            self._follower_count += 1
                        # 其他角色 acm_idx=None -> 自动分配

                        bridge, user = self._pm.create_pair(acm_idx=acm_idx)
                        if bridge:
                            t = threading.Thread(
                                target=_serial_bridge_worker,
                                args=(bridge, dp.tcp_port),
                                daemon=True)
                            t.start()
                            self._bridges[dev_id] = {
                                "bridge": bridge, "user": user,
                                "tcp": dp.tcp_port, "thread": t,
                            }
                            role_name = _ROLE_NAMES.get(dp.role, "?")
                            print(f"[Bridge] {role_name} dev={dev_id} -> {user}  "
                                  f"(tcp:{dp.tcp_port})")
            time.sleep(1)

    def get_table(self) -> dict:
        result = {}
        for dev_id, info in self._bridges.items():
            result[dev_id] = info["user"]
        return result

    def cleanup(self):
        self._running = False
        self._pm.cleanup()


_vserial_bridge = None
