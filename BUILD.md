# CURRENT: generic TICK CANVAS — build, flash, drive

> ⚠️ **Two copies exist.** This session edits `~/Downloads/boat-helm-project 2`, but the
> Windows editor compiles `Z:\boat-helm-project 2` (= `~/Windows/boat-helm-project 2`).
> After regenerating, sync before compiling:
> ```bash
> rsync -a "/Users/dangraf/Downloads/boat-helm-project 2/build/" "/Users/dangraf/Windows/boat-helm-project 2/build/"
> ```
> Then in the editor **close the project and File→Open the uiprj fresh** (it caches the old one).

The flashed image is ONE canvas of addressable slots; the host composes any instrument at
runtime. Regenerate the whole thing (assets + cfg) with `python3 tools/gen_canvas.py`.

## Canvas address map (write here to drive)

| Slot | Addr | Indices / value |
|------|------|-----------------|
| 60 ticks (360°) | `0x0100`–`0x013B` | 0 off · 1 grey · 2 white · 3 green · 4 yellow · 5 red |
| top triangle | `0x0140` | 0 off · 1 white · 2 green · 3 red |
| mode label | `0x0141` | 0 RPM · 1 COG HOLD · 2 TWA HOLD · 3 AP HOLD · 4 OFF |
| symbol | `0x0142` | 0 gauge · 1 autopilot · 2 wind |
| value | `0x0150` | 4-digit 0..9999 |
| LED ring / brightness | `0x1FF1` / `0x7001` | system regs |

## Steps
1. `python3 tools/gen_canvas.py` (only if you changed the canvas) → then rsync (see warning above).
2. UI_Editor-II: File→Open `boat-helm.uiprj`, compile, confirm `Make_error_info.txt` empty
   (harmless `icon N does not exist` warnings for gaps in 05xx are fine).
3. Flash with LT_VCom (hold BUSY while plugging USB).
4. Drive from the Mac over the CN2 USB-TTL adapter:
   ```bash
   cd tools/instrumentctl && go build -o instrumentctl .
   ./instrumentctl -rpm -port /dev/cu.usbserial-XXXX     # gentle RPM rev, redline-aware
   ```

---

# (superseded) Iteration 1 — single vector-needle gauge: build, flash, verify

Goal of this iteration: prove the loop end-to-end. **One** reusable gauge page is
flashed; the host turns it into any instrument at runtime by driving three things
over USB-CDC — the needle, the numeric readout, and the LED ring. No instrument is
baked in.

Everything below the "compile" step needs Windows (UI_Editor-II to compile, LT_VCom
to flash). The source in `build/` is prepared and validated on the Mac side; the
serial driver (`tools/instrument.py`) runs from the Mac/Pi once flashed.

## What's on the page (address map)

| Element        | Widget        | Write addr | Range / encoding                         |
|----------------|---------------|-----------:|------------------------------------------|
| Needle         | `needle_0`    | `0x0011`   | angle **45..315** (0°=south, clockwise)  |
| Numeric readout| `pngNumber_0` | `0x0012`   | integer **0..999** (3-digit, 36 px)      |
| LED ring       | *system reg*  | `0x1FF1`   | 0 off · 1 blue · 2 green · 4 red (bits)  |
| Brightness     | *system reg*  | `0x7001`   | 0..63                                    |

The driver maps a physical value onto the needle sweep: `angle = 45 + (value/max)·270`.

## 1. Compile (Windows · UI_Editor-II)

1. Open `build/boat-helm.uiprj`. It now lists a **single page**, Page 0, with the
   neutral dial `0100_gauge.png` as background.
2. Confirm Page 0 shows: one **needle**, one **number**, one **encoder** (rotation
   is neutralized to stay on Page 0 this iteration).
3. Compile / "Make bin".
4. **Confirm `build/Make_error_info.txt` is empty** before flashing. Expected clean,
   because: needle default (180) is inside 45..315; the digit set `0200–0209` is now
   present in `build/Icon/`; and only proven, UI_Editor-authored widget records are used.

If the `.cfg` import ever looks wrong in the editor, reproduce Page 0 by hand — the
records were derived from UI_Editor's own output, so these settings match:

- **Needle** `needle_0`: write addr `0x0011`, center (120,120), start 45, final 315,
  default 180, length 90, width 6, colour `0xFFFFFF`, mode `2Dsmooth`.
- **pngNumber** `pngNumber_0`: write addr `0x0012`, pos (84,102), size 72×36, 3 digits,
  align Middle, first image `0200_digit36.png`, last image `0209_digit36.png`.

## 2. Flash (Windows · LT_VCom)

1. Hold the **BUSY** button while plugging in USB-C to enter flash mode.
2. Flash `build/UartTFT-II_Flash.bin`.
3. Replug normally; it enumerates as `/dev/cu.usbmodem*` (macOS) / a COM port.

## 3. Verify (Mac / Pi · tools/instrument.py)

**Wiring — runtime commands are UART on CN2, NOT USB-C.** Confirmed on hardware: USB-C is
power + flashing only; in run mode the module presents no serial port. Drive it with a
3.3 V USB-TTL adapter wired to CN2:
- adapter TX → CN2 RX, adapter RX → CN2 TX, adapter GND → CN2 GND
- keep USB-C plugged for power (or power from the adapter per the board's input)
- the adapter shows up as `/dev/cu.usbserial-*` (or `cu.wchusbserial*` / `cu.SLAB_*`)

```bash
python3 -m pip install pyserial pillow
# point --port at the USB-TTL adapter; try 115200 first, then 19200 / 9600
python3 tools/instrument.py --demo                --port /dev/cu.usbserial-XXXX
python3 tools/instrument.py --value 320 --max 600 --port /dev/cu.usbserial-XXXX
python3 tools/instrument.py --ring green          --port /dev/cu.usbserial-XXXX
python3 tools/instrument.py --value 55 --max 100 --dry-run   # prints frames, no hardware
```

**Pass criteria:** the needle tracks `--value`, the readout shows the number, the ring
changes colour. Cross-check already done on the bench: the value-0 needle frame
`5A A5 07 10 00 11 00 2D 75 DB` is byte-identical to UI_Editor's own
`SerialPortCommands.csv` export — so a matching CRC on hardware means the widget moved.

If frames are ACKed (`10 FF …`) but nothing moves, the command path may be on CN2's
TX/RX pins rather than USB-CDC — see CLAUDE.md open questions.

## Regenerating assets

```bash
python3 tools/gen_chrome.py     # the 03xx chrome layers (already generated)
python3 tools/gen_gauge_bg.py   # flatten chrome -> build/Picture/0100_gauge.png
```

## Next iterations (not in this test)

- **Text labels** (title / units) — needs a Text widget + font; `pngNumber` is digits only.
- **Encoder → host reporting** — bind a return address so turning/pressing reports deltas
  to the host instead of navigating locally.
- **Addressable ticks / redline** — promote the baked static ticks to the `03xx`
  addressable chrome widgets (`0x0320+` minor, `0x0340+` major) for live warn/alarm zones.
- **4-digit readout** — add a digit slot for 0..9999.
