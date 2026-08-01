# Tomorrow — one flash → holiday-ready

Everything below is already generated + synced to `Z:`. You compile once, flash once, and
then every instrument is drivable in **pure software** (no more flashing needed on holiday).

## 1. Before compiling — verify the encoder toggle
In UI_Editor-II, open `Z:\boat-helm-project 2\build\boat-helm.uiprj`, select **`encoder_0`**,
and confirm **`reportToHost` = Enable** in its properties. (I set it in the cfg, but the
editor is the source of truth — this is the one thing worth eyeballing, it's what makes the
knob's twist/press report back to the host.)

## 2. Compile + flash
- Compile. Confirm **`build/Make_error_info.txt` is empty** (the many `icon N does not exist`
  warnings for gaps in the sparse ID space are harmless).
- Flash `UartTFT-II_Flash.bin` with LT_VCom (hold **BUSY** while plugging in USB).

## 3. Test (from Mac/Pi over the 3.3V USB-TTL on CN2 — not USB-C)
```bash
cd tools/instrumentctl && go build -o instrumentctl .
P=/dev/cu.usbserial-02R2FQC9
./instrumentctl -instrument temp  -value 75  -port $P   # thermometer, °C
./instrumentctl -instrument depth -value 4   -port $P   # shallow → red
./instrumentctl -instrument fuel  -value 60  -port $P   # pump, %
./instrumentctl -instrument volts -value 12  -port $P   # battery, V
./instrumentctl -instrument rpm                -port $P # animates
./instrumentctl -autopilot cog -heading 120 -current 90 -port $P   # starboard → green
./instrumentctl -autopilot crs -heading 110 -port $P              # magenta
./instrumentctl -listen -port $P    # then TWIST + PRESS the knob → should stream 0x41 frames
```

## What this flash bakes in (all reusable in software)
- **6 instruments + autopilot** — add more just by editing `instruments{}` in `main.go`
- **19 mode labels** (RPM/DEPTH/SPEED/SOG/STW/TEMP/FUEL/WIND/AWA/TWA/AWS/TWS/HDG/COG/CRS/VOLTS/AMPS/TRIP/OFF)
- **12 symbols** (gauge/depth/speed/temp/fuel/wind/autopilot/battery/anchor/engine/water/gps)
- **12 units** (°C/°F/°/%/kn/V/A/m/ft/nm/rpm) · **12 tick colours** · **11 ring backdrops**
- **Live knob input** via `-listen`

## NOT in this flash (do iteratively when you're back, needs the Windows Font Tool)
**Arbitrary text** (any label / boat name / dynamic units) via String_Label + a baked font.
Staged + documented in [STRING_LABEL.md](STRING_LABEL.md). Don't gamble your one flash on it.
