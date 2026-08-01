# Arbitrary text — String_Label + font (do iteratively, not on the holiday flash)

The mode/unit labels are currently **baked images** (an Icon library). That's reliable but
fixed — a new label needs a reflash. The generic alternative is a **String_Label** widget:
the host writes a *string* to its address and it renders any text. This is the real unlock
(dynamic units, boat name, any label), but it needs a baked **font**, which requires the
Windows-only **Font Tool**, and there's no String_Label cfg example in the SDK to copy — so
it's an iterative editor build, not something to gamble the single holiday flash on.

## Steps (all in UI_Editor-II on Windows)

1. **Project Setting → language** — enable the language(s) you need (English is enough).
2. **Font Tool** (Tools menu) — create a font library in Unicode covering your character
   range (ASCII + `°`). Save into `build/FontBin/` named `NN_Font-...` (00–35). The SDK ships
   examples, e.g. `…/Demo - Shower GUI/FontBin/02_Font-32x32_2bit.bin` (staged a copy in
   `build/FontBin/` here as a reference — regenerate your own with the Font Tool for the
   right size/range).
3. **Add a String_Label** widget where the mode/unit currently sits. Set:
   - **WriteAddr** — where the host writes the string (pick free addrs, e.g. `0x0180` mode,
     `0x0181` units; keep clear of the tick block `0x0100–0x013B` and slots `0x0140–0x0160`).
   - **font** — the library from step 2. **colour/size**, **wordLength** (max chars).
4. **Read back its cfg record** from `build/Plugin/Page0000.cfg` and paste it to me — I'll
   fold String_Label generation into `tools/gen_canvas.py` so it's reproducible.

## Driving it (host side)
Write string data to the WriteAddr (§10 "Write Command to Change Texts" / §9 "Write String
Data to Variable Address"): send the character codes as the data payload. Once we have the
exact record + font, I'll add `instrumentctl -text <addr> "<string>"`.

## Migration once this works
Replace the baked mode-image + unit-image slots with two String_Labels; drop `MODES`/`UNITS`
image generation from `gen_canvas.py`; instruments then carry literal `mode`/`unit` strings
the host writes directly. Much more generic, far fewer baked assets.
