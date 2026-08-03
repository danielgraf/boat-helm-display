# UartTFT-II_Flash.bin container format — RE notes (toward Mac-native bin gen/patch)

Goal: change the `.bin` without UI_Editor-II (Windows). Two routes — a from-scratch
compiler (big) or a **patcher** that splices into a known-good bin (tractable, recommended).
This documents the decoded container. Tool: `tools/bintool` (dump/diff/words/strings).

## The bin = header directory + six concatenated sections
Straight from the compiler's own build log (`Demo - Shower GUI/Make_info.txt`), UI_Editor
builds six sub-bins and concatenates them under a header directory:

    Page_para_all.bin · Font_all.bin · MultiLang · Gif_all.bin · Picture_all.bin · icon_all.bin

## Header directory (offset 0x00, uint32-LE `{addr, count}` pairs) — CONFIRMED
Validated against the Shower demo bin, whose every count matches `Make_info.txt` exactly
(8 pages, 7 fonts, 1 gif, 310 icons, MultiLang @ 0x1E05F):

| slot  | field                | Shower value        | meaning                                   |
|-------|----------------------|---------------------|-------------------------------------------|
|`0x00` | {addr, 0}            | 0x011DD0            | Picture section start                      |
|`0x08` | {addr, **count**}    | 0x01DDD0, **8**     | **Page** params — count = page count       |
|`0x10` | {addr, **count**}    | 0x01E027, **7**     | **Font** table — count = font count        |
|`0x18` | {addr, 0}            | 0x01E05F            | **MultiLang** strings (== `MultiLang_Msg_Addr`)|
|`0x20` | {addr, count}        | 0x01E068, 8         | page→picture map (count = pages)           |
|`0x28` | {addr, **count**}    | 0x01E060, **1**     | **Gif** table — count = gif count          |
|`0x30` | {addr, **count**}    | 0x0FF0C8, **310**   | **Icon** table — count = **maxIconID+1**   |
|`0x40` | {0x180, size}        | 0x180, 0x011C50     | data base (0x180 = header len) + a size    |
|`0x50` | {addr, 0}            | 0x01E05F            | string base (= MultiLang)                  |
|`0x60` | {end/size, 0}        | (0 here; = filesize in our `clean-1039`) | file end pointer (tool-version dependent) |

Section order in the file is monotonic by address, so each section runs from its `addr` to
the next section's `addr` (last = icon → EOF). `0x80..~0xB0` holds ASCII version tags
(`V3.10`, `V2.2`). Icon count = `maxIconID+1` over the sparse ID space (NOT the file count) —
this is why our maxed canvas shows 982 (highest asset id 0981), and why the sparse-walk prints
harmless "icon N does not exist" for gaps.

## Known-good oracle bins (`tools/bintool/samples/`, gitignored)
- `shower-demo-flash.bin` (1,108,880 B) — SDK demo; **counts cross-checked vs its build log**.
- `clean-1039.bin` (1,838,412 B) — our current maxed canvas (the flashed keeper).
- `tick-canvas-0116.bin`, `maxed-0942.bin` — earlier/dirty ours (0942 was malformed: `[0x60]`
  end-pointer wrong — see [[flash-and-selfhost-status]]).
- `old-needle-5page.bin` — the pre-canvas needle build.

## Still to map before a safe writer/patcher (needs a controlled single-change diff)
- **Per-icon table entry** layout in the Icon section (offset, w, h, format, data ptr) and the
  image encoding (RGB565 opaque vs `softpng` 1-bit-alpha). This is what a patcher must edit.
- **Any CRC/checksum** the *device* validates (distinct from the flash-CRC the flasher checks).
- Page/widget record binary layout (only needed for a full compiler, not for a patcher).

**How to map it (one Windows session):** in UI_Editor change exactly ONE thing (swap one
icon image; then separately, nudge one slot's X), recompile, and `bintool diff` old vs new.
Each single-change diff isolates one field's bytes. A handful of these fully specifies the
Icon-section entry + where positions live → enough to build `bintool patch`.

## Recommended path
Grow `tools/bintool` into a **patcher** (splice new icon data + fix the directory offsets/
counts), NOT a from-scratch compiler. Verify every generated bin by diffing against these
oracles before it ever touches hardware. Compiling stays rare by design: content (which
instrument, colours, labels, values) is host-driven at runtime over UART — only slot
*positions*, the *font*, and the *icon library* require a reflash.
