# LT168A/LT268A UART flash protocol — RE notes (toward a Mac-native flasher)

Goal: replace the Windows `LT_Uart_GUI_V3.33.exe` with a Go tool so we can flash the
knob from the Mac (no VM). This documents what's **confirmed**, what's **inferred**, and
the one step that makes it exact (a serial capture). Companion tool: `tools/flashprobe`.

## Confirmed (from the SDK: flasher manual + `LT_Uart_GUI.exe` static RE via radare2)

**Physical / mode**
- Enter update mode: power the module with **BUSY held to GND** → it enters USB_Update mode
  and enumerates on the Mac as **`/dev/cu.usbmodem*`** (the runtime CN2 path `cu.usbserial-*`
  is a *different* port and is NOT used for flashing).
- The boot ROM announces itself in ASCII: `LT268A Boot_Version:21060301` (family-specific;
  the tool recognises LT268A/LT168A/LT168B/LT268B/LT269/LT268C/LT268D/LT7689/LT776).
- Baud: default **115200**, selectable 9600–921600 (`New Rate` raises it mid-session:
  change rate → Close Comm → reopen). Flash mode is USB-CDC so baud may be cosmetic.

**Two independent targets (keep these straight)**
- **MCU code** (`*_MCU_*.bin`, firmware, runs at ≥`0x8008000`) — the flasher's `Update MCU`.
  RISKY. We do NOT touch this.
- **External NOR flash** (`UartTFT-II_Flash.bin`, our GUI content) — the flasher's
  `Update Flash`. SAFE + recoverable (a bad content-flash is fixed by re-flashing).

**Command architecture** (radare2 on the stripped PE, cross-refed by status strings)
- A **shared send-frame routine** (`fcn.0040b640`) builds a **fixed-length (~64-byte) frame**:
  a small constant prefix, a command/variant selector byte, then zero padding — one
  `QIODevice::write` per command.
- After sending it **polls for a reply** (Sleep + `processEvents`, ~200 tries), watching a
  readyRead flag and an ACK/terminator byte (candidate `0x7D '}'`). Every command has an
  `...and return error command` failure string, so each op is acknowledged.
- Distinct code paths exist for the full command set, matching the GUI buttons:
  `BootMode-I` / `BootMode-II` (enter boot, variant 0/1) · read JEDEC **Flash ID**
  (→ size/name via `Flash.ini` table) · **Set 32-bit Addr** · **Erase** (sector or whole) ·
  **Program flash** (256-byte pages per `Flash.ini`) · **Get/Check flash CRC** ·
  **Run Uart Application** (reset + run).
- Programming granularity = **256-byte page**, from `Flash.ini` (e.g. `0x5E4018 → ZB25VQ128,
  256-byte page, 16 MB`). Erase is by sector or whole-chip.

## NOT yet exact (do not hardcode from static RE — one misread already caught)
The precise **opcode byte values**, the **exact prefix/length**, the **ACK/terminator**, and
the **flash CRC algorithm** (the `Flash code,CRC=0x...` the tool computes over the image).
Static x86 gives the skeleton but is error-prone on these specifics — e.g. the arg to the
send routine turned out to be a boot-mode *flag* (0/1), not the opcode.

## The one step that makes it exact: capture one real flash
Next time at a Windows machine, run the real `LT_Uart_GUI` flashing our `.bin` **with a
serial sniffer** on the COM port (e.g. free serial port monitor / a passive TX/RX tap).
Capture host→device **and** device→host for: Open Comm (handshake+banner) → Flash Info →
Update Flash (erase+program+CRC) → Run. That single log turns every "inferred" above into
bytes we can replay from Go.

## What you CAN do now while travelling (safe, read-only): `tools/flashprobe`
Put the knob in flash mode (BUSY→GND at power-on → `cu.usbmodem*`), then:
```bash
cd tools/flashprobe && go build -o flashprobe .
./flashprobe -listen               # hexdump whatever the boot ROM emits (the banner)
./flashprobe -listen -sweepbaud    # if silent, try every baud rate
./flashprobe -probe 5A             # (opt-in) send one byte, show the reply
./flashprobe -sweep                # (opt-in) try safe 1-byte sync probes
```
Listening never writes, so it cannot erase/program. Paste the hexdump back and we decode the
banner + handshake — the first real bytes of the protocol, gathered without Windows.

## Bonus RE asset on disk
`tools/bintool/samples/lt168a-bootrom-v1.45.bin` (17 KB) = **the boot ROM itself** — the code
that implements this protocol. If static RE of the PE stalls, disassembling the boot ROM (once
we ID the core arch — the LT168 datasheet describes a 32-bit RISC w/ supervisor/user modes,
PIT/EPORT/crossbar — likely a licensed ColdFire-class core) is the deepest ground truth.
