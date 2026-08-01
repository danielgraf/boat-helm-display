import cairosvg, math, os, csv

W=H=240; CX=CY=120.0
DST="chrome_v2"; 
import shutil; shutil.rmtree(DST, ignore_errors=True)
for s in ["Icon","Picture","Needle"]: os.makedirs(f"{DST}/{s}",exist_ok=True)

WHITE="#FFFFFF"; CYAN="#00E5FF"; AMBER="#FFB300"; DIM="#4A4A4A"
R_OUTER=112; R_INNER=75; R_TICK_OUT=108; R_MIN_IN=100; R_MAJ_IN=92; R_TRI=112
S0=135.0; SW=270.0; N_MIN=24; N_MAJ=12

def pol(r,a): a=math.radians(a); return (CX+r*math.cos(a), CY+r*math.sin(a))
def wrap(b): return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{b}</svg>'
def render(fn,b): cairosvg.svg2png(bytestring=wrap(b).encode(), write_to=f"{DST}/Icon/{fn}", output_width=W, output_height=H)
def arc(r,a0,a1):
    x0,y0=pol(r,a0); x1,y1=pol(r,a1); large=1 if (a1-a0)%360>180 else 0
    return f'M {x0:.2f} {y0:.2f} A {r} {r} 0 {large} 1 {x1:.2f} {y1:.2f}'
def tk(a,ri,ro,c,w):
    x0,y0=pol(ri,a); x1,y1=pol(ro,a)
    return f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" stroke="{c}" stroke-width="{w}" stroke-linecap="round"/>'

# asset numbers: true hex-style 4-digit in the 0300 block (0300,0301,... as DECIMAL-look but we keep <10000)
# We'll use plain incrementing decimal from 300 so filenames read 0300,0301,...  (UI_Editor treats them as 4-digit IDs)
manifest=[]; anum=300
def emit(meaning, waddr=None):
    global anum
    num=f"{anum:04d}"; fn=f"{num}_{meaning}.png"
    manifest.append((num, meaning, fn, f"0x{waddr:04X}" if waddr is not None else ""))
    anum+=1; return fn

# rings — not addressable (always shown)
render(emit("ringouter"), f'<path d="{arc(R_OUTER,S0,S0+SW)}" fill="none" stroke="{WHITE}" stroke-width="2" stroke-linecap="round" opacity="0.9"/>')
render(emit("ringinner"), f'<path d="{arc(R_INNER,S0,S0+SW)}" fill="none" stroke="{CYAN}" stroke-width="1.5" stroke-linecap="round" opacity="0.55"/>')

# minor ticks 24: off (static) + on (addressable, 0x0320+)
wa=0x0320
for i in range(N_MIN):
    a=S0+SW*i/(N_MIN-1)
    render(emit(f"tickmin{i:02d}off"),      tk(a,R_MIN_IN,R_TICK_OUT,DIM,1.5))
    render(emit(f"tickmin{i:02d}on", wa),    tk(a,R_MIN_IN,R_TICK_OUT,WHITE,1.5)); wa+=1

# major ticks 12: dim(static)+norm(addr 0x0340+)+hi(static alt)
wa=0x0340
for i in range(N_MAJ):
    a=S0+SW*i/(N_MAJ-1)
    render(emit(f"tickmaj{i:02d}dim"),       tk(a,R_MAJ_IN,R_TICK_OUT,DIM,2.5))
    render(emit(f"tickmaj{i:02d}norm", wa),   tk(a,R_MAJ_IN,R_TICK_OUT,CYAN,2.5))
    render(emit(f"tickmaj{i:02d}hi"),        tk(a,R_MAJ_IN,R_TICK_OUT,AMBER,2.5)); wa+=1

# edge triangles 4: norm(static)+act(addr 0x0350+)
def tri(a,c):
    tp=pol(R_TRI-14,a); b1=pol(R_TRI,a-3); b2=pol(R_TRI,a+3)
    return f'<polygon points="{tp[0]:.1f},{tp[1]:.1f} {b1[0]:.1f},{b1[1]:.1f} {b2[0]:.1f},{b2[1]:.1f}" fill="{c}"/>'
wa=0x0350
for k,a in {"S":S0,"A":S0+SW/3,"B":S0+2*SW/3,"E":S0+SW}.items():
    render(emit(f"edgetri{k}norm"),     tri(a,WHITE))
    render(emit(f"edgetri{k}act", wa),   tri(a,AMBER)); wa+=1

with open(f"{DST}/chrome_manifest.csv","w",newline="") as f:
    w=csv.writer(f); w.writerow(["asset_id","meaning","filename","write_addr"]); w.writerows(manifest)

tot=len(manifest); addr=sum(1 for m in manifest if m[3])
print(f"total assets: {tot}  |  addressable: {addr}")
print(f"asset id range: 0300-{anum-1:04d}")
print("addr blocks: minor-on 0x0320-0x0337 | major-norm 0x0340-0x034B | tri-act 0x0350-0x0353")
# consistency checks
addrs=[m[3] for m in manifest if m[3]]
assert len(addrs)==len(set(addrs)), "DUP ADDR!"
assert anum-1 < 10000, "asset id overflow"
# no collision with existing project addrs (0x0011-0x0054)
lo=min(int(a,16) for a in addrs); print(f"lowest chrome addr 0x{lo:04X} (project uses 0x0011-0x0054, no overlap: {lo>0x0054})")
print("CHECKS PASS")
