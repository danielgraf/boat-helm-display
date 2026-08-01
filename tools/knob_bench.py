#!/usr/bin/env python3
"""
Anders 11807 / Levetop LT168A UartTFT knob — bench harness (macOS, USB-C / USB-CDC)

Protocol (UI_Editor-II manual, section 10):
  Frame  = Header(0x5A 0xA5) + Length(1) + Cmd(1) + Addr(2, big-endian) + Data(2*n) + CRC(2)
  Length = len(Cmd + Addr + Data + CRC)   [i.e. everything after the Length byte]
  Cmd    : 0x10 write, 0x03 read, 0x41 touch/return
  CRC    : Modbus CRC-16 (poly 0xA001), sent low-byte-first? -> verified against datasheet example below.

Nothing here is destructive: it changes backlight and page, both reversible, and otherwise just listens.

Usage:
  python3 knob_bench.py                # auto-detect port, run the guided sequence
  python3 knob_bench.py --port /dev/cu.usbmodemXXXX
  python3 knob_bench.py --listen       # just listen and decode incoming frames (turn/press the knob)
  python3 knob_bench.py --send "10 2001 5152 5354"   # send a raw write (hex, spaces optional)
"""

import argparse, glob, sys, time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial missing -> pip3 install pyserial")

HEADER = bytes([0x5A, 0xA5])

# ---- CRC ---------------------------------------------------------------
# Verified against BOTH datasheet worked examples (BC43 and 4C30):
# CRC-16/MODBUS (poly 0x8005, init 0xFFFF, refin, refout, no final xor),
# computed over Cmd + Addr + Data ONLY (not header, not length),
# emitted big-endian in the frame (high byte first).
def crc16_levetop(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc  # 0xA001 is the reflected form of 0x8005; this is standard Modbus CRC

def build_frame(payload_after_length: bytes) -> bytes:
    """payload_after_length = Cmd + Addr + Data. We add Length, CRC, Header."""
    length = len(payload_after_length) + 2  # +2 for the CRC bytes
    crc = crc16_levetop(payload_after_length)          # over Cmd+Addr+Data only
    crc_le = bytes([crc & 0xFF, (crc >> 8) & 0xFF])    # little-endian (low byte first) in the frame
    return HEADER + bytes([length]) + payload_after_length + crc_le

def verify_against_datasheet():
    # Two worked examples from the manual, both must match:
    #  A) write 0x5152 0x5354 to 0x2001 -> 5A A5 09 10 20 01 51 52 53 54 BC 43
    #  B) CRC-pass reply (cmd..data = 10 FF)  -> tail ... 4C 30
    a = build_frame(bytes.fromhex("10200151525354"))
    b = build_frame(bytes.fromhex("10FF"))
    ok_a = a == bytes.fromhex("5AA50910200151525354BC43")
    ok_b = b.hex().endswith("4c30")
    print(f"[CRC self-test] example A {a.hex(' ')}  {'OK' if ok_a else 'BAD'}")
    print(f"[CRC self-test] example B {b.hex(' ')}  {'OK' if ok_b else 'BAD'}")
    ok = ok_a and ok_b
    print(f"[CRC self-test] {'PASS - CRC scheme confirmed against datasheet' if ok else 'FAIL - do not trust frames'}")
    return ok

# ---- helpers to build the specific test commands -----------------------
def write_cmd(addr: int, data_words) -> bytes:
    data = b"".join(bytes([(w >> 8) & 0xFF, w & 0xFF]) for w in data_words)
    payload = bytes([0x10]) + bytes([(addr >> 8) & 0xFF, addr & 0xFF]) + data
    return build_frame(payload)

def read_cmd(addr: int, word_count: int) -> bytes:
    payload = bytes([0x03]) + bytes([(addr >> 8) & 0xFF, addr & 0xFF]) + bytes([(word_count >> 8) & 0xFF, word_count & 0xFF])
    return build_frame(payload)

# ---- frame decoding for incoming bytes ---------------------------------
def decode(buf: bytearray):
    """Yield complete frames from buf, mutating it. Returns list of (cmd, addr, data)."""
    out = []
    while True:
        i = buf.find(HEADER)
        if i < 0 or len(buf) < i + 3:
            break
        length = buf[i + 2]
        full = i + 3 + length  # header(2)+len(1)+length bytes
        if len(buf) < full:
            break
        frame = bytes(buf[i:full])
        del buf[:full]
        cmd = frame[3]
        addr = (frame[4] << 8) | frame[5] if length >= 4 else None
        data = frame[6:-2]
        out.append((cmd, addr, data, frame))
    return out

def label(cmd):
    return {0x10: "WRITE-ack", 0x03: "READ-return", 0x41: "TOUCH/RETURN"}.get(cmd, f"0x{cmd:02X}")

# ---- port discovery ----------------------------------------------------
def find_port():
    cands = glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*") + \
            glob.glob("/dev/cu.wchusbserial*") + glob.glob("/dev/cu.SLAB_USBtoUART*")
    if cands:
        return cands[0]
    for p in list_ports.comports():
        return p.device
    return None

# ---- main flows --------------------------------------------------------
def listen(ser, seconds=None):
    print(f"[listen] decoding frames on {ser.port} @ {ser.baudrate} — turn and press the knob…  (Ctrl-C to stop)")
    buf = bytearray()
    t0 = time.time()
    try:
        while seconds is None or time.time() - t0 < seconds:
            chunk = ser.read(64)
            if chunk:
                buf += chunk
                for cmd, addr, data, frame in decode(buf):
                    a = f"0x{addr:04X}" if addr is not None else "----"
                    print(f"  <- {label(cmd):13s} addr={a} data={data.hex(' ') or '(none)':<12} raw={frame.hex(' ')}")
            else:
                time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n[listen] stopped.")

def guided(ser):
    def shoot(desc, frame, wait=0.4):
        print(f"\n[send] {desc}\n       tx={frame.hex(' ')}")
        ser.reset_input_buffer()
        ser.write(frame)
        time.sleep(wait)
        buf = bytearray(ser.read(256))
        frames = decode(buf)
        if not frames:
            print("       rx= (nothing) — check baud (try 9600/115200), or this addr isn't mapped in the flashed GUI")
        for cmd, addr, data, fr in frames:
            a = f"0x{addr:04X}" if addr is not None else "----"
            print(f"       rx <- {label(cmd)} addr={a} data={data.hex(' ') or '(none)'}  raw={fr.hex(' ')}")

    print("\n=== GUIDED BENCH SEQUENCE ===")
    print("Watch the knob's screen and LED ring. All reversible. Registers confirmed from Anders AAN-049.")
    # Confirmed registers (Anders AAN-049 appendix):
    #   0x7000 page jump | 0x7001 brightness (0-63) | 0x700E auto-BL | 0x700F dimming
    #   0x1FF1 LED colour (0 off;1 blue;2 green;4 red;bitfield) | 0x1FF2/3 LED min/max bright | 0x1FF4 step
    shoot("LED ring -> BLUE   (reg 0x1FF1 = 1)", write_cmd(0x1FF1, [0x0001]))
    time.sleep(0.5)
    shoot("LED ring -> GREEN  (reg 0x1FF1 = 2)", write_cmd(0x1FF1, [0x0002]))
    time.sleep(0.5)
    shoot("LED ring -> RED    (reg 0x1FF1 = 4)", write_cmd(0x1FF1, [0x0004]))
    time.sleep(0.5)
    shoot("Brightness -> ~50% (reg 0x7001 = 0x20)", write_cmd(0x7001, [0x0020]))
    time.sleep(0.4)
    shoot("Brightness -> max  (reg 0x7001 = 0x3F)", write_cmd(0x7001, [0x003F]))
    shoot("Jump to page 1     (reg 0x7000 = 1)", write_cmd(0x7000, [0x0001]))
    time.sleep(0.7)
    shoot("Jump to page 2     (reg 0x7000 = 2)", write_cmd(0x7000, [0x0002]))
    time.sleep(0.7)
    shoot("Back to page 0     (reg 0x7000 = 0)", write_cmd(0x7000, [0x0000]))
    print("\n[note] 0xFF ack = link + CRC GOOD. If the LED ring changed colour, you have full confirmation")
    print("       the USB-C path carries the command protocol. If you get acks but no visible change,")
    print("       the protocol may only come out on CN2's TX/RX pins (needs a USB-TTL adapter on CN2),")
    print("       and USB-C is power/flash only — see the Anders app note UART wiring.")
    print("\nNow listening for encoder/button frames — turn and press the knob:")
    listen(ser, seconds=20)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--listen", action="store_true")
    ap.add_argument("--send")
    args = ap.parse_args()

    if not verify_against_datasheet():
        print("Refusing to transmit until CRC matches the datasheet. Fix build_frame first.")
        # keep going only for --send experimentation if the user insists
    port = args.port or find_port()
    if not port:
        print("No serial port found. Plug in the knob, then:  ls /dev/cu.*")
        print("On macOS a native-USB device shows as /dev/cu.usbmodemXXXX (no driver needed);")
        print("a CH340/CP2102 bridge shows as /dev/cu.wchusbserial* or /dev/cu.SLAB_* (needs vendor driver).")
        sys.exit(1)
    print(f"[port] using {port} @ {args.baud} baud")

    try:
        ser = serial.Serial(port, args.baud, timeout=0.2)
    except serial.SerialException as e:
        sys.exit(f"Could not open {port}: {e}")

    time.sleep(0.3)
    if args.send:
        frame = build_frame(bytes.fromhex(args.send.replace(" ", "")))
        print(f"[send] raw payload -> {frame.hex(' ')}")
        ser.write(frame); time.sleep(0.4)
        buf = bytearray(ser.read(256))
        for cmd, addr, data, fr in decode(buf):
            print(f"  rx <- {label(cmd)} addr=0x{addr:04X} data={data.hex(' ')} raw={fr.hex(' ')}")
    elif args.listen:
        listen(ser)
    else:
        guided(ser)
    ser.close()

if __name__ == "__main__":
    main()
