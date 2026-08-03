# Boot ROM RE — `LT168A_USB_Uart_Boot_V1.45` (the UART flash bootloader)

The boot ROM is the code that *implements* the flash protocol — the deepest ground truth
for a Mac-native flasher, reachable without a serial capture. This is how far static RE got
and where the tooling wall is. Binary: `tools/bintool/samples/lt168a-bootrom-v1.45.bin`
(17,468 B), pulled from the Anders SDK `Demo - Shower GUI/`.

## Architecture — DEFINITIVE
- **Core: Motorola M·CORE** — a 32-bit RISC, 16 GPRs R0–R15 (R15 = link reg, R0 = SP by
  convention), PSR with S/C bits, VBR. Identified from the LT168 datasheet §6.3/§6.6
  programming model + instruction table (`BGENI`, `BMASKI`, `BCLRI`, `XTRB0-3`, `LRW`,
  `JMPI`/`JSRI`, `MVCV`, `DECGT`, `DOZE`…) — unmistakably M·CORE, not ColdFire/ARM.
- **Endianness: little-endian.** Decisive test: the M·CORE return `jmp r15` = `0x00CF` appears
  as bytes `CF 00` (LE) **103×** vs `00 CF` (BE) 7× — ~100 returns is right for a 17 KB image.
  Instructions are 16-bit halfwords stored low-byte-first.
- **Load base: `0x60000000`.** The opening table and literal pools hold LE32 values like
  `0x6000294c`, `0x60002e0c` — pointers into the image at base `0x60000000`.
- **Vector/dispatch table** at file 0x00 = 7 LE32 pointers (`[0]=0x6000294c`, then …2942/2944/
  2940/2946/2948/294a), all into a tight ~0x2940 region.
- ~100 functions. Contains the flash logic: strings `File size exceeds Flash capacity`
  (@0x06b4), `Checking CRC` (@0x06e4), `Result:` (@0x06f4), `Failed` (@0x06fc).

## Flash-verify CRC — IDENTIFIED (disassembler-independent)
- **Standard CRC-32, reflected polynomial `0xEDB88320`** (== `0x04C11DB7` non-reflected) — the
  constant sits in a literal pool at ROM offset **`0x11c4`** (bytes `20 83 b8 ed`). **No 256-entry
  table** nearby → **bitwise** CRC-32. Surrounding code shows the CRC tell-tales (`xor r14,r3`
  twice, `not r0` = the final `~crc`).
- This is almost certainly the "Flash code,CRC" the flasher (`LT_Uart_GUI`) checks after
  programming. Reference values (standard zlib CRC-32: init 0xFFFFFFFF, reflected, final XOR):
  - `clean-1039.bin` (our keeper, 1,838,412 B) → **`0xB078B128`**
  - `shower-demo-flash.bin` (1,108,880 B)      → `0xD9079DC2`
  - **Validation hook:** when you next flash `clean-1039` with `LT_Uart_GUI`, its reported CRC
    should match `0xB078B128` (or a close variant — the device may init/final-XOR or pad to a
    flash boundary differently). If it matches, our CRC is exactly pinned for the Go flasher.

## Tooling wall (why this stops here, honestly)
- radare2 is the only M·CORE disassembler on the box, and its `mcore` plugin is **unreliable**:
  it ignores `cfg.bigendian` (I work around it with a 16-bit-byteswapped copy), and it
  misdecodes exotic opcodes (emits invalid GPRs r16–r28, garbles coprocessor/control ops).
  Its **assembler is non-functional** (empty output). Common flow (jmp/ld/st/br/bt/bf/xor/subi)
  decodes OK; the dispatch/SPI-sequencing logic does not decode cleanly enough to trust
  byte-exact opcodes from it.
- String-pointer navigation didn't resolve the command handlers: the string pointers
  (`0x600006xx`) don't appear as absolute LRW pool constants, so the ROM likely addresses
  strings via base-register + offset (position-independent boot style).
- **To continue reliably** would need a correct M·CORE disassembler — historically GNU binutils
  had an `mcore-elf` target (removed in modern binutils; would need an older 2.2x–2.3x build).
  That's a heavyweight offline build and still leaves ~100 symbol-less functions to map.

## Verdict / recommendation
The boot-ROM RE delivered the **arch (M·CORE/LE/@0x60000000)** and, more usefully, the
**flash CRC (CRC-32/0xEDB88320)** — a concrete building block for the Go flasher. But
extracting the exact UART command opcodes + framing from symbol-less M·CORE with a buggy
disassembler is high-effort/low-confidence. The **serial capture of one real flash** (2 min,
next time at a Windows box) remains the definitive, cheap path for the opcodes/framing — now
de-risked, because we already know the CRC it must reproduce. See [[flash-and-selfhost-status]]
and `docs/FLASH_PROTOCOL.md`.
