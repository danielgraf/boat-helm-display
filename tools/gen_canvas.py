#!/usr/bin/env python3
"""
Canvas compiler for the generic instrument canvas (LT168A / Anders knob).

Generates BOTH the assets and the page cfg programmatically — this is the
"multi-use framework": one baked canvas of addressable slots that the host
composes into any instrument at runtime (RPM, autopilot, depth, …) with no
reflash. See tools/instrumentctl (Go) for the host-side compositions.

Canvas slots (all driven by writing an image index / value to an address):
  - 60 tick ring, 360deg     : each tick an Icon w/ 6 colours (off/grey/white/green/yellow/red)
  - top reference triangle    : lubber line, Icon w/ off/white/green/red
  - 12-o'clock symbol slot     : Icon, pick a glyph by index
  - mode-text slot            : Icon, pick a pre-rendered label (RPM / COG HOLD / …)
  - value                     : 4-digit pngNumber (0..9999)

Widget addresses (write here to drive):
  ticks   0x0100 .. 0x013B   (tick i = 0x0100+i; index 0..5)
  triangle 0x0140            symbol 0x0142          mode 0x0141
  value    0x0150            LED ring 0x1FF1 (system)  brightness 0x7001 (system)

Icon record format (verified against the Anders Shower demo):
  icon_NAME:parAddr,2,writeAddr,2,X,Y,W,H,firstImg,lastImg,RGB565,,defaultIdx,1,NAME,Disable,0xFFFF,1
Images are opaque RGB565 → tick sprites bake a BLACK background (canvas is black) so
they composite seamlessly; "off" is a plain black sprite.
"""
import math, os, csv, sys, glob, colorsys
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, "build", "Icon")
PIC  = os.path.join(ROOT, "build", "Picture")
PLUG = os.path.join(ROOT, "build", "Plugin")
for d in (ICON, PIC, PLUG):
    os.makedirs(d, exist_ok=True)

W = H = 240
CX = CY = 120.0
N = 60                    # ticks around the full circle
R_TICK = 105              # tick ring radius
BLACK = (0, 0, 0)

# index 0 is the FIRST image (what the editor previews and the boot default shows), so
# put a VISIBLE colour there and "off" last. Keep in sync with instrumentctl tk* consts.
def _dim(c, f=0.36): return (int(c[0]*f), int(c[1]*f), int(c[2]*f))
_W=(232,232,232); _G=(62,199,107); _Y=(255,210,62); _R=(255,59,48); _M=(210,40,170)
# index: 0 grey | 1 white 2 green 3 yellow 4 red 5 magenta | 6-10 dim of 1-5 | 11 off
# (dim colour index = bright index + 5). Keep in sync with instrumentctl tk* consts.
TICK_COLORS = [
    ("grey",     (74,74,74)),
    ("white",    _W), ("green", _G), ("yellow", _Y), ("red", _R), ("magenta", _M),
    ("dwhite", _dim(_W)), ("dgreen", _dim(_G)), ("dyellow", _dim(_Y)), ("dred", _dim(_R)), ("dmagenta", _dim(_M)),
    ("off",      None),
]
TRI_COLORS = [("white", (232,232,232)), ("green",(62,199,107)), ("red",(255,59,48)), ("off", None)]
MODES   = ["RPM","DEPTH","SPEED","SOG","STW","TEMP","FUEL","WIND","AWA","TWA","AWS","TWS",
           "HDG","COG","CRS","VOLTS","AMPS","TRIP","OFF"]
SYMBOLS = ["gauge","depth","speed","temp","fuel","wind","autopilot","battery","anchor","engine","water","gps"]

def font(sz, bold=True):
    names = (["Arial Bold.ttf","Helvetica.ttc"] if bold else ["Arial.ttf","Helvetica.ttc"])
    for base in ("/System/Library/Fonts/Supplemental/","/System/Library/Fonts/","/Library/Fonts/"):
        for n in names:
            p = base + n
            if os.path.exists(p):
                try: return ImageFont.truetype(p, sz)
                except Exception: pass
    return ImageFont.load_default()

def save_icon(img, num, meaning):
    fn = f"{num:04d}_{meaning}.png"
    img.save(os.path.join(ICON, fn))   # RGBA PNG; icon format "Softpng" preserves alpha
    return fn

# ---- radial tick sprites ----------------------------------------------------
# A tick is a short RADIAL line. Positions i and i+30 lie on the same line (180deg
# symmetry), so we only render 30 orientations x 6 colours = 180 sprites; each tick
# Icon references the 6-colour set for its orientation (i % 30). ids 0500..0679.
TICK_S = 16          # sprite box
TICK_LEN = 12        # radial line length
TICK_WID = 3
N_ORIENT = N // 2    # 30
SS = 8               # supersample factor: draw big, downscale smooth, then Softpng thresholds
                     # the α at 127 along a clean contour -> minimal staircase on the 1-bit edge.
def _tick_sprite(dx, dy, col):
    big = Image.new("RGBA", (TICK_S*SS, TICK_S*SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    c = TICK_S*SS / 2.0
    hl = TICK_LEN*SS / 2.0
    d.line([(c - dx*hl, c - dy*hl), (c + dx*hl, c + dy*hl)], fill=col, width=TICK_WID*SS)
    return big.resize((TICK_S, TICK_S), Image.LANCZOS)

def gen_ticks():
    by_orient = {}   # orient -> (firstImg, lastImg) ; colour order = TICK_COLORS
    for o in range(N_ORIENT):
        a = math.radians(-90 + o * 360.0 / N)
        dx, dy = math.cos(a), math.sin(a)
        first = last = None
        for c, (name, col) in enumerate(TICK_COLORS):
            im = _tick_sprite(dx, dy, col) if col else Image.new("RGBA", (TICK_S, TICK_S), (0, 0, 0, 0))
            fn = save_icon(im, 500 + o*len(TICK_COLORS) + c, f"tk{o:02d}{name}")
            if c == 0:
                first = fn
            if c == len(TICK_COLORS) - 1:
                last = fn
        by_orient[o] = (first, last)
    return by_orient

# ---- top reference triangle (16x12, pointing down) -------------------------
TRI_W, TRI_H = 18, 12
def gen_triangle():
    ids = {}
    for i, (name, col) in enumerate(TRI_COLORS):
        im = Image.new("RGBA", (TRI_W, TRI_H), (0, 0, 0, 0))
        if col:
            d = ImageDraw.Draw(im)
            d.polygon([(TRI_W/2, 1), (2, TRI_H-1), (TRI_W-2, TRI_H-1)], fill=col)
        ids[i] = save_icon(im, 900 + i, f"tri{name}")
    return ids

# ---- mode-text labels (rendered text -> image) -----------------------------
MODE_W, MODE_H = 130, 22
def gen_modes():
    f = font(17)
    ids = {}
    for i, txt in enumerate(MODES):
        im = Image.new("RGBA", (MODE_W, MODE_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        w = d.textbbox((0,0), txt, font=f)[2]
        d.text(((MODE_W-w)/2, 1), txt, font=f, fill=(150,160,175))
        ids[i] = save_icon(im, 910 + i, f"mode{i}")
    return ids

# ---- 12-o'clock symbol glyphs ----------------------------------------------
SYM = 40
def gen_symbols():
    C = (150, 160, 175); ids = {}
    for i, name in enumerate(SYMBOLS):
        im = Image.new("RGBA", (SYM, SYM), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        if name == "gauge":
            d.arc([6,6,SYM-6,SYM-6], 150, 30, fill=C, width=3); d.line([(20,26),(28,16)], fill=C, width=3)
        elif name == "autopilot":
            d.line([(20,8),(20,28)], fill=C, width=3); d.polygon([(14,28),(26,28),(20,36)], fill=C)
        elif name == "wind":
            d.line([(20,32),(20,8)], fill=C, width=3); d.polygon([(20,8),(13,18),(27,18)], fill=C)
        elif name == "depth":
            d.line([(20,8),(20,22)], fill=C, width=3); d.polygon([(14,20),(26,20),(20,28)], fill=C)
            d.line([(8,33),(13,30),(18,33),(23,30),(28,33),(32,30)], fill=C, width=2)
        elif name == "speed":
            d.line([(8,20),(26,20)], fill=C, width=3); d.polygon([(26,14),(26,26),(36,20)], fill=C)
            d.line([(9,13),(17,13)], fill=C, width=2); d.line([(9,27),(17,27)], fill=C, width=2)
        elif name == "temp":
            d.line([(20,8),(20,27)], fill=C, width=3); d.ellipse([14,25,26,37], outline=C, width=2)
            d.ellipse([17,28,23,34], fill=C)
        elif name == "fuel":  # petrol pump
            d.rectangle([12,13,24,36], outline=C, width=2)   # body
            d.rectangle([15,16,21,21], fill=C)               # display window
            d.line([(24,20),(29,20)], fill=C, width=2)       # hose out
            d.line([(29,20),(29,11)], fill=C, width=2)       # hose up
            d.line([(29,11),(26,11)], fill=C, width=2)       # nozzle
            d.line([(10,36),(26,36)], fill=C, width=2)
        elif name == "battery":
            d.rectangle([10,15,30,33], outline=C, width=2); d.rectangle([30,20,33,28], fill=C)
            d.rectangle([13,18,19,30], fill=C)
        elif name == "anchor":
            d.ellipse([17,7,23,13], outline=C, width=2); d.line([(20,12),(20,33)], fill=C, width=2)
            d.line([(13,19),(27,19)], fill=C, width=2); d.arc([9,20,31,37], 20, 160, fill=C, width=2)
        elif name == "engine":
            d.rectangle([9,16,27,32], outline=C, width=2); d.rectangle([27,20,33,28], outline=C, width=2)
            d.line([(14,16),(14,11)], fill=C, width=2); d.line([(14,11),(22,11)], fill=C, width=2)
        elif name == "water":
            for yy in (15,23,31):
                d.line([(8,yy),(13,yy-3),(18,yy),(23,yy-3),(28,yy),(32,yy-3)], fill=C, width=2)
        elif name == "gps":
            d.ellipse([12,8,28,24], outline=C, width=2); d.ellipse([17,13,23,19], fill=C)
            d.polygon([(15,21),(25,21),(20,34)], fill=C)
        ids[i] = save_icon(im, 930 + i, f"sym{i}")
    return ids

UNITS = ["", "\u00b0C", "\u00b0F", "\u00b0", "%", "kn", "V", "A", "m", "ft", "nm", "rpm"]
UNIT_W, UNIT_H = 56, 20
def gen_units():
    f = font(16, bold=False); ids = {}
    for i, txt in enumerate(UNITS):
        im = Image.new("RGBA", (UNIT_W, UNIT_H), (0, 0, 0, 0))
        if txt:
            d = ImageDraw.Draw(im); w = d.textbbox((0,0), txt, font=f)[2]
            d.text(((UNIT_W-w)/2, 1), txt, font=f, fill=(150,160,175))
        ids[i] = save_icon(im, 970 + i, f"unit{i}")
    return ids

# ---- black page background --------------------------------------------------
def gen_background():
    Image.new("RGB", (W, H), BLACK).save(os.path.join(PIC, "0100_canvas.png"))

RING_BAND = 20               # thickness of the colour ring behind the ticks
def _g_tach(f):      # green -> amber -> red
    if f < 0.5:
        t=f/0.5; return (int(62+193*t), int(199-49*t), int(107-107*t))
    t=(f-0.5)/0.5;  return (255, int(150-91*t), int(48*t))
def _g_rainbow(f):
    r,g,b=colorsys.hsv_to_rgb(f % 1.0, 0.85, 1.0); return (int(r*255), int(g*255), int(b*255))
def _g_coolwarm(f):  # blue -> red
    return (int(60+195*f), int(120-61*f), int(255-207*f))
RING_ORDER = ["tach","rainbow","coolwarm","white","cyan","green","amber","red","blue","magenta","off"]
def gen_ring():
    bbox=[CX-R_TICK, CY-R_TICK, CX+R_TICK, CY+R_TICK]
    grads={"tach":_g_tach, "rainbow":_g_rainbow, "coolwarm":_g_coolwarm}
    solids={"white":(220,220,220),"cyan":(0,200,220),"green":(62,199,107),"amber":(255,150,0),
            "red":(255,59,48),"blue":(60,120,255),"magenta":(210,40,170)}
    files=[]
    for k,name in enumerate(RING_ORDER):
        im=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(im)
        if name in grads:
            gf=grads[name]
            for deg in range(0,360,2):
                d.arc(bbox, deg-90, deg-90+3, fill=gf(deg/360.0), width=RING_BAND)
        elif name in solids:
            d.arc(bbox, 0, 360, fill=solids[name], width=RING_BAND)
        # "off" -> fully transparent
        files.append(save_icon(im, 950+k, f"ring{name}"))
    return files[0], files[-1]

# ---- cfg record builders ----------------------------------------------------
def icon_rec(name, waddr, x, y, w, h, first, last, default=0):
    return (f"{name}:0xFFFF,2,0x{waddr:04X},2,{x},{y},{w},{h},"
            f"{first},{last},softpng,,{default},1,{name},Disable,0xFFFF,1;")

def tick_xy(i):
    a = math.radians(-90 + i * 360.0 / N)
    return round(CX + R_TICK*math.cos(a) - TICK_S/2), round(CY + R_TICK*math.sin(a) - TICK_S/2)

def gen_cfg(ring, ticks, tri, modes, syms, units):
    recs = []
    # UI_Editor identifies widget TYPE by the name prefix, so every Icon widget MUST be
    # named icon_<n> (unique per page) or the editor silently drops it on load.
    n = 0
    def ic(addr, x, y, w, h, first, last, default=0):
        nonlocal n
        rec = icon_rec(f"icon_{n}", addr, x, y, w, h, first, last, default)
        n += 1
        return rec
    # colour/gradient ring, full-frame, drawn FIRST so it sits BEHIND the ticks
    recs.append(ic(0x0160, 0, 0, W, H, ring[0], ring[1], default=0))
    # 60 tick icons around the ring (share the 0500..0505 image set); default grey(0)
    # so the device shows a ring on boot before the host composes an instrument.
    for i in range(N):
        x, y = tick_xy(i)
        first, last = ticks[i % N_ORIENT]
        recs.append(ic(0x0100 + i, x, y, TICK_S, TICK_S, first, last, default=0))
    # top reference triangle at 12 o'clock (just inside the ring); default white(0)
    recs.append(ic(0x0140, round(CX-TRI_W/2), 5, TRI_W, TRI_H, tri[0], tri[3], default=0))
    # mode-text slot
    recs.append(ic(0x0141, round(CX-MODE_W/2), 92, MODE_W, MODE_H, modes[0], modes[len(MODES)-1]))
    # 12-o'clock symbol slot
    recs.append(ic(0x0142, round(CX-SYM/2), 50, SYM, SYM, syms[0], syms[len(SYMBOLS)-1]))
    # units slot (below the value)
    recs.append(ic(0x0143, round(CX-UNIT_W/2), 156, UNIT_W, UNIT_H, units[0], units[len(UNITS)-1]))
    # 4-digit value (reuse existing 36px digit images 0200..0209)
    recs.append("pngNumber_0:0xFFFF,0x0150,2,72,116,96,36,4,0,short,Middle,"
                "0200_digit36.png,0209_digit36.png,24,pngNumber_0,Disable,0xFFFF,1;")
    # neutralized encoder (stay on page 0)
    enc = ("encoder_0:0x00F0,0,0,240,240,encoder_0,1,Page0000," + "0xFFFF,"*15 +
           "0xFFFF,Enable;NULL;NULL;NULL;1,Page0000," + "0xFFFF,"*15 +
           "0xFFFF,Enable;1,Page0000," + "0xFFFF,"*15 +
           "0xFFFF,Enable;NULL;NULL;NULL;NULL;NULL;NULL;NULL;NULL;NULL;NULL;")
    recs.append(enc)
    with open(os.path.join(PLUG, "Page0000.cfg"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(recs) + "\n")
    for n in (1,2,3,4):
        open(os.path.join(PLUG, f"Page{n:04d}.cfg"), "w", encoding="utf-8-sig").write("")

def gen_uiprj():
    with open(os.path.join(ROOT, "build", "boat-helm.uiprj"), "w", encoding="utf-8") as f:
        f.write("Page0000:0100_canvas.png;0100_canvas.png;\n")

def write_map(ticks, tri, modes, syms):
    with open(os.path.join(ROOT, "build", "canvas_map.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["slot","addr","indices"])
        w.writerow(["ticks(0..59)", "0x0100..0x013B", "0=off,1=grey,2=white,3=green,4=yellow,5=red"])
        w.writerow(["triTop", "0x0140", "0=off,1=white,2=green,3=red"])
        w.writerow(["modeLbl", "0x0141", "|".join(f"{i}={m}" for i,m in enumerate(MODES))])
        w.writerow(["symSlot", "0x0142", "|".join(f"{i}={s}" for i,s in enumerate(SYMBOLS))])
        w.writerow(["value", "0x0150", "0..9999"])
        w.writerow(["screenRing", "0x0160", "|".join(f"{i}={n}" for i,n in enumerate(RING_ORDER))])
        w.writerow(["ledRing", "0x1FF1", "0 off,1 blue,2 green,4 red"])

def clean_generated():
    # remove any previously-generated canvas icons (05xx) so renames don't leave
    # stale files that collide on icon-id (icon-id = the 4-digit filename number).
    for f in sum([glob.glob(os.path.join(ICON, f"0{d}*.png")) for d in (5,6,7,8,9)], []):
        os.remove(f)

def main():
    clean_generated()
    ring = gen_ring(); ticks = gen_ticks(); tri = gen_triangle(); modes = gen_modes(); syms = gen_symbols(); units = gen_units()
    gen_background(); gen_cfg(ring, ticks, tri, modes, syms, units); gen_uiprj(); write_map(ticks, tri, modes, syms)
    print(f"canvas compiled: {N} ticks + triangle + symbol + mode + 4-digit value")
    print("assets -> build/Icon (0500-0532) + build/Picture/0100_canvas.png")
    print("cfg -> build/Plugin/Page0000.cfg ; project -> build/boat-helm.uiprj ; map -> build/canvas_map.csv")

if __name__ == "__main__":
    try:
        import PIL  # noqa
    except ImportError:
        sys.exit("Pillow missing -> pip3 install pillow")
    main()
