"""
Motor encoder offset calibration script.
Supports both Feetech (STS/SCS) and Hiwonder (LX) bus servos.

Usage:
    python set_motors_half_encode.py                          # Auto-detect, default /dev/ttyACM0
    python set_motors_half_encode.py -p COM5                  # Specify port
    python set_motors_half_encode.py -p COM5 -t feetech       # Force Feetech mode
    python set_motors_half_encode.py -p COM5 -t hiwonder      # Force Hiwonder mode
    python set_motors_half_encode.py -p COM5 --max-id 8       # Scan ID 1~8

Before running:
    1. Manually move all joints to the half-position pose (see assets/half_encode.jpg)
    2. pip install scservo-sdk pyserial
"""

import argparse
import time
import struct
import serial


# ============================================================
# Hiwonder LX protocol (direct serial, no SDK needed)
# ============================================================

class HiwonderServo:
    """Minimal Hiwonder LX bus servo driver via half-duplex serial."""

    SERVO_MOVE_TIME_WRITE = 1
    SERVO_POS_READ = 28
    SERVO_OFFSET_WRITE = 17
    SERVO_OFFSET_READ = 19
    SERVO_ID_READ = 14

    def __init__(self, port, baudrate=115200, timeout=0.1):
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    def _build_packet(self, servo_id, cmd, params=b''):
        length = len(params) + 3
        packet = bytearray([0x55, 0x55, servo_id, length, cmd]) + bytearray(params)
        checksum = (~(servo_id + length + cmd + sum(params))) & 0xFF
        packet.append(checksum)
        return bytes(packet)

    def _send_and_receive(self, servo_id, cmd, params=b'', response_len=0):
        self.ser.reset_input_buffer()
        packet = self._build_packet(servo_id, cmd, params)
        self.ser.write(packet)

        if response_len <= 0:
            return None

        # Response: 0x55 0x55 ID LEN CMD PARAMS... CHECKSUM
        header_size = 5
        total = header_size + response_len + 1  # +1 for checksum
        data = self.ser.read(total)
        if len(data) < total:
            return None
        if data[0] != 0x55 or data[1] != 0x55:
            return None
        return data[header_size:header_size + response_len]

    def ping(self, servo_id):
        """Try to read servo ID to check if it's alive."""
        resp = self._send_and_receive(servo_id, self.SERVO_ID_READ, response_len=1)
        return resp is not None

    def read_position(self, servo_id):
        """Read current position (0~1000)."""
        resp = self._send_and_receive(servo_id, self.SERVO_POS_READ, response_len=2)
        if resp is None:
            return None
        pos = struct.unpack('<h', resp)[0]
        return pos

    def read_offset(self, servo_id):
        """Read current offset (-125~125)."""
        resp = self._send_and_receive(servo_id, self.SERVO_OFFSET_READ, response_len=1)
        if resp is None:
            return None
        offset = struct.unpack('<b', resp)[0]
        return offset

    def write_offset(self, servo_id, offset):
        """Write offset (-125~125), saved to EEPROM immediately."""
        offset = max(-125, min(125, int(offset)))
        params = struct.pack('<b', offset)
        self._send_and_receive(servo_id, self.SERVO_OFFSET_WRITE, params)


# ============================================================
# Feetech STS/SCS driver (uses scservo_sdk)
# ============================================================

class FeetechServo:
    """Feetech STS/SCS servo driver via scservo_sdk."""

    def __init__(self, port, baudrate=1000000):
        from scservo_sdk import PortHandler, sms_sts
        self.port_handler = PortHandler(port)
        self.packet_handler = sms_sts(self.port_handler)
        if not self.port_handler.openPort():
            raise RuntimeError(f"Failed to open port {port}")
        self.port_handler.setBaudRate(baudrate)

    def close(self):
        self.port_handler.closePort()

    def ping(self, servo_id):
        pos, _, _ = self.packet_handler.ReadPos(servo_id)
        return pos != -1 and pos is not None

    def read_position(self, servo_id):
        pos, _, _ = self.packet_handler.ReadPos(servo_id)
        if pos == -1:
            return None
        return pos

    def clear_offset(self, servo_id):
        ph = self.packet_handler
        txpacket = [ph.scs_lobyte(0), ph.scs_hibyte(0)]
        ph.unLockEprom(servo_id)
        ph.writeTxRx(servo_id, 31, 2, txpacket)
        ph.LockEprom(servo_id)

    def write_offset(self, servo_id, offset):
        ph = self.packet_handler
        txpacket = [ph.scs_lobyte(offset), ph.scs_hibyte(offset)]
        ph.unLockEprom(servo_id)
        ph.writeTxRx(servo_id, 31, 2, txpacket)
        ph.LockEprom(servo_id)


# ============================================================
# Main calibration logic
# ============================================================

def scan_servos(driver, max_id):
    """Scan for connected servos, return list of found IDs."""
    found = []
    for sid in range(1, max_id + 1):
        try:
            if driver.ping(sid):
                found.append(sid)
        except Exception:
            pass
        time.sleep(0.02)
    return found


def calibrate_feetech(port, max_id):
    """Calibrate Feetech STS/SCS servos: set current position as center (2048)."""
    CENTER = 2048

    driver = FeetechServo(port)
    print(f"[Feetech] Opened port {port}")

    # Scan
    print(f"[Feetech] Scanning servo ID 1~{max_id} ...")
    servo_ids = scan_servos(driver, max_id)
    if not servo_ids:
        print("[Feetech] No servos found!")
        driver.close()
        return
    print(f"[Feetech] Found {len(servo_ids)} servos: {servo_ids}")

    # Step 1: clear existing offsets
    print("[Feetech] Clearing existing offsets ...")
    for sid in servo_ids:
        driver.clear_offset(sid)
        time.sleep(0.01)

    # Step 2: read current positions
    positions = {}
    for sid in servo_ids:
        pos = driver.read_position(sid)
        positions[sid] = pos
        time.sleep(0.01)
    print(f"[Feetech] Current positions: {positions}")

    # Step 3: calculate and write offsets
    offsets = {}
    for sid in servo_ids:
        offsets[sid] = positions[sid] - CENTER
    print(f"[Feetech] Offsets to write: {offsets}")

    for sid in servo_ids:
        driver.write_offset(sid, offsets[sid])
        time.sleep(0.01)
    print("[Feetech] Offsets written to EEPROM.")

    # Step 4: verify
    print("[Feetech] Verifying (Ctrl+C to exit) ...")
    try:
        while True:
            readout = {}
            for sid in servo_ids:
                readout[sid] = driver.read_position(sid)
                time.sleep(0.01)
            print(f"  Positions: {readout}")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[Feetech] Done.")
    finally:
        driver.close()


def calibrate_hiwonder(port, max_id):
    """Calibrate Hiwonder LX servos: set current position as center (500)."""
    CENTER = 500

    driver = HiwonderServo(port)
    print(f"[Hiwonder] Opened port {port}")

    # Scan
    print(f"[Hiwonder] Scanning servo ID 1~{max_id} ...")
    servo_ids = scan_servos(driver, max_id)
    if not servo_ids:
        print("[Hiwonder] No servos found!")
        driver.close()
        return
    print(f"[Hiwonder] Found {len(servo_ids)} servos: {servo_ids}")

    # Step 1: clear existing offsets
    print("[Hiwonder] Clearing existing offsets ...")
    for sid in servo_ids:
        driver.write_offset(sid, 0)
        time.sleep(0.02)

    # Step 2: read current positions
    positions = {}
    for sid in servo_ids:
        pos = driver.read_position(sid)
        positions[sid] = pos
        time.sleep(0.02)
    print(f"[Hiwonder] Current positions: {positions}")

    # Step 3: calculate and write offsets
    # Hiwonder offset range is -125~125, corresponding to ~-30deg~+30deg
    # Position 0~1000 maps to 0~240 degrees, so 1 unit ~= 0.24 degrees
    # Offset 1 unit ~= 0.24 degrees as well
    offsets = {}
    for sid in servo_ids:
        if positions[sid] is not None:
            raw_offset = positions[sid] - CENTER
            if abs(raw_offset) > 125:
                print(f"  WARNING: Servo {sid} offset {raw_offset} exceeds -125~125 range! "
                      f"Physical position too far from center. Clamping to limit.")
            offsets[sid] = max(-125, min(125, raw_offset))
        else:
            print(f"  WARNING: Servo {sid} read position failed, skipping.")
            offsets[sid] = None
    print(f"[Hiwonder] Offsets to write: {offsets}")

    for sid in servo_ids:
        if offsets[sid] is not None:
            driver.write_offset(sid, offsets[sid])
            time.sleep(0.02)
    print("[Hiwonder] Offsets written.")

    # Step 4: verify
    print("[Hiwonder] Verifying (Ctrl+C to exit) ...")
    try:
        while True:
            readout = {}
            for sid in servo_ids:
                readout[sid] = driver.read_position(sid)
                time.sleep(0.02)
            print(f"  Positions: {readout}")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[Hiwonder] Done.")
    finally:
        driver.close()


def auto_detect_servo_type(port):
    """Try Feetech first (1M baud), then Hiwonder (115200 baud)."""
    # Try Feetech
    try:
        driver = FeetechServo(port)
        for sid in range(1, 7):
            if driver.ping(sid):
                driver.close()
                return 'feetech'
            time.sleep(0.02)
        driver.close()
    except Exception:
        pass

    # Try Hiwonder
    try:
        driver = HiwonderServo(port)
        for sid in range(1, 7):
            if driver.ping(sid):
                driver.close()
                return 'hiwonder'
            time.sleep(0.02)
        driver.close()
    except Exception:
        pass

    return None


def main():
    parser = argparse.ArgumentParser(description='Motor encoder offset calibration (Feetech & Hiwonder)')
    parser.add_argument('-p', '--port', default='/dev/ttyACM0',
                        help='Serial port (default: /dev/ttyACM0)')
    parser.add_argument('-t', '--type', choices=['feetech', 'hiwonder', 'auto'], default='auto',
                        help='Servo type (default: auto-detect)')
    parser.add_argument('--max-id', type=int, default=6,
                        help='Max servo ID to scan (default: 6)')
    args = parser.parse_args()

    servo_type = args.type
    if servo_type == 'auto':
        print(f"Auto-detecting servo type on {args.port} ...")
        servo_type = auto_detect_servo_type(args.port)
        if servo_type is None:
            print("ERROR: No servos detected. Check port and wiring.")
            print("  You can also specify type manually: -t feetech / -t hiwonder")
            return
        print(f"Detected servo type: {servo_type}")

    if servo_type == 'feetech':
        calibrate_feetech(args.port, args.max_id)
    else:
        calibrate_hiwonder(args.port, args.max_id)


if __name__ == '__main__':
    main()
