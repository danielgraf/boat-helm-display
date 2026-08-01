#!/usr/bin/env python3
"""
Generic instrument driver for the single-canvas boat-helm image (LT168A knob).

The flashed image is ONE reusable gauge page. There is no "rpm page" or "temp page"
baked in — the host decides, at runtime, what instrument this is by driving:

    needle_0     @ 0x0011   vector needle, angle 45..315 (0deg=south, clockwise)
    pngNumber_0  @ 0x0012   numeric readout, 0..999
    LED ring     @ 0x1FF1   system reg: 0 off / 1 blue / 2 green / 4 red (bitfield)
    brightness   @ 0x7001   system reg: 0..63

So `set_instrument(value, vmax)` maps a physical value onto the needle sweep and the
digits, and you can call it for rpm, depth, temperature, battery %, whatever — the
same page, reconfigured on the fly. That is the whole point of this first iteration:
prove the host can drive an arbitrary instrument over USB-CDC.

Framing is identical to knob_bench.py and is self-tested against the datasheet on
every run (CRC-16/MODBUS over Cmd+Addr+Data, little-endian, header 5A A5).

Usage:
  python3 instrument.py --demo                 # sweep the needle+number 0->max->0, cycle ring
  python3 instrument.py --value 320 --max 600  # show 320 on a 0..600 instrument
  python3 instrument.py --ring green           # LED ring colour
  python3 instrument.py --value 55 --max 100 --dry-run   # print frames, no hardware needed
  python3 instrument.py --port /dev/cu.usbmodemXXXX ...
"""
import argparse, glob, sys, time

# ---- addresses (match Plugin/Page0000.cfg + system registers) --------------
ADDR_NEEDLE = 0x0011
ADDR_NUMBER = 0x0012
ADDR_RING   = 0x1FF1
ADDR_BRIGHT = 0x7001
NEEDLE_MIN, NEEDLE_MAX = 45, 315   # widget startAngle..finalAngle
NUMBER_MAXDIGITS = 999             # 3-digit readout in this iteration
RING = {"off": 0, "blue": 1, "green": 2, "red": 4}

HEADER = bytes([0x5A, 0xA5])

# ---- framing (verified against datasheet, same as knob_bench.py) -----------
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc

def build_frame(payload: bytes) -> bytes:
    """payload = Cmd + Addr + Data. Prepend Length, append little-endian CRC, header."""
    length = len(payload) + 2
    crc = crc16_modbus(payload)
    return HEADER + bytes([length]) + payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF])

def write_cmd(addr: int, word: int) -> bytes:
    payload = bytes([0x10, (addr >> 8) & 0xFF, addr & 0xFF, (word >> 8) & 0xFF, word & 0xFF])
    return build_frame(payload)

def selftest() -> bool:
    # Datasheet worked example: write 0x5152 0x5354 to 0x2001 -> 5A A5 09 10 20 01 51 52 53 54 BC 43
    got = build_frame(bytes.fromhex("10200151525354"))
    ok = got == bytes.fromhex("5AA50910200151525354BC43")
    print(f"[crc selftest] {got.hex(' ')}  {'OK' if ok else 'BAD — refusing to transmit'}")
    return ok

# ---- value mapping ---------------------------------------------------------
def value_to_angle(value: float, vmax: float) -> int:
    frac = 0.0 if vmax <= 0 else max(0.0, min(1.0, value / vmax))
    return round(NEEDLE_MIN + frac * (NEEDLE_MAX - NEEDLE_MIN))

def instrument_frames(value: float, vmax: float):
    """Frames to render `value` (of full-scale `vmax`) as needle + numeric readout."""
    angle = value_to_angle(value, vmax)
    digits = max(0, min(NUMBER_MAXDIGITS, int(round(value))))
    return [("needle", write_cmd(ADDR_NEEDLE, angle)),
            ("number", write_cmd(ADDR_NUMBER, digits))]

# ---- port / io -------------------------------------------------------------
def find_port():
    for pat in ("/dev/cu.usbmodem*", "/dev/cu.usbserial*",
                "/dev/cu.wchusbserial*", "/dev/cu.SLAB_USBtoUART*"):
        c = glob.glob(pat)
        if c:
            return c[0]
    return None

class Link:
    def __init__(self, port, baud, dry):
        self.dry = dry
        self.ser = None
        if dry:
            print("[dry-run] not opening a port; frames are printed only")
            return
        import serial  # lazy: dry-run needs no pyserial
        self.ser = serial.Serial(port, baud, timeout=0.2)
        time.sleep(0.3)
    def send(self, label, frame):
        print(f"  -> {label:8s} {frame.hex(' ')}")
        if self.ser:
            self.ser.write(frame)
    def close(self):
        if self.ser:
            self.ser.close()

# ---- flows -----------------------------------------------------------------
def do_set(link, value, vmax):
    for label, fr in instrument_frames(value, vmax):
        link.send(label, fr)
    print(f"[set] value={value} of {vmax}  -> needle {value_to_angle(value,vmax)}deg, readout {int(round(value))}")

def do_ring(link, name):
    link.send("ring", write_cmd(ADDR_RING, RING[name]))
    print(f"[ring] {name}")

def do_demo(link, vmax, step=0.02, dwell=0.03):
    print(f"[demo] sweeping 0..{vmax}..0 and cycling the LED ring — watch the knob")
    seq = [i / 50 * vmax for i in range(51)] + [i / 50 * vmax for i in range(50, -1, -1)]
    for name in ("blue", "green", "red"):
        do_ring(link, name)
        for v in seq:
            for _, fr in instrument_frames(v, vmax):
                link.send("sweep", fr)
            time.sleep(dwell)
    do_ring(link, "blue")
    do_set(link, vmax * 0.5, vmax)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--value", type=float)
    ap.add_argument("--max", type=float, default=999, dest="vmax")
    ap.add_argument("--ring", choices=list(RING))
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not selftest():
        sys.exit(1)

    port = args.port or find_port()
    if not port and not args.dry_run:
        sys.exit("No serial port found. Plug in the knob (ls /dev/cu.*), or use --dry-run.")
    if not args.dry_run:
        print(f"[port] {port} @ {args.baud}")

    link = Link(port, args.baud, args.dry_run)
    try:
        if args.demo:
            do_demo(link, args.vmax)
        elif args.value is not None:
            do_set(link, args.value, args.vmax)
        elif args.ring:
            do_ring(link, args.ring)
        else:
            ap.print_help()
    finally:
        link.close()

if __name__ == "__main__":
    main()
