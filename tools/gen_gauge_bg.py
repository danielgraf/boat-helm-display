#!/usr/bin/env python3
"""
Generate the neutral generic-gauge background for the "single generic canvas" image.

The Anders/LT168A is a smart display: it renders the GUI from flash and the host
drives widgets over serial. For the generic instrument we want ONE reusable dial
face with no instrument-specific text ("RPM", "TEMP", ...). The host turns it into
any instrument at runtime by driving the needle angle + numeric readout + LED ring.

Rather than place ~40 tick widgets (and get the geometry wrong), we bake the static
dial into a single background image by flattening the chrome PNGs that gen_chrome.py
already produced at the correct 270deg / bottom-gap geometry. Because we reuse the
very same chrome assets the needle was designed against, the baked ticks line up
with the vector needle automatically.

Output: build/Picture/0100_gauge.png  (240x240, opaque, black field + lit dial)

Layers used (the "lit" variants, for legibility on the round black panel):
  - 0300 ring outer (white)         - 0301 ring inner (cyan)
  - 24x minor ticks   "on"  (white) - 12x major ticks "norm" (cyan)
  -  4x edge triangles "norm" (white)
The center is left clear for the driven pngNumber readout.
"""
import os, glob, re, sys
try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow missing -> pip3 install pillow")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHROME = os.path.join(ROOT, "chrome_assets", "Icon")
OUT_DIR = os.path.join(ROOT, "build", "Picture")
OUT = os.path.join(OUT_DIR, "0100_gauge.png")
W = H = 240

# Which baked layers make up the neutral dial. Order = bottom..top.
def pick(patterns):
    files = []
    for pat in patterns:
        files += sorted(glob.glob(os.path.join(CHROME, pat)))
    return files

LAYERS = (
    pick(["0300_ringouter.png", "0301_ringinner.png"])   # rings
    + pick(["*tickmin*on.png"])                            # minor ticks, lit
    + pick(["*tickmaj*norm.png"])                          # major ticks, lit
    + pick(["*edgetri?norm.png"])                          # edge triangles
)

def main():
    if not os.path.isdir(CHROME):
        sys.exit(f"chrome assets not found at {CHROME} (run tools/gen_chrome.py first)")
    if not LAYERS:
        sys.exit(f"no chrome PNGs matched under {CHROME}")
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))  # opaque black field
    for f in LAYERS:
        layer = Image.open(f).convert("RGBA")
        if layer.size != (W, H):
            sys.exit(f"layer {f} is {layer.size}, expected {(W, H)} — chrome geometry mismatch")
        canvas.alpha_composite(layer)
    os.makedirs(OUT_DIR, exist_ok=True)
    # Backgrounds are opaque -> flatten to RGB. (The existing project backgrounds
    # are RGB PNGs in Picture/ and compile fine, so we match that proven format.)
    canvas.convert("RGB").save(OUT)
    print(f"wrote {OUT}  ({W}x{H} RGB, {len(LAYERS)} baked layers)")

if __name__ == "__main__":
    main()
