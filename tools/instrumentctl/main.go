// Command instrumentctl drives the generic LT168A gauge canvas over UART (CN2).
//
// It is a Go port of tools/instrument.py — identical 5A A5 framing, verified on
// every run against the datasheet example (CRC-16/MODBUS over Cmd+Addr+Data,
// little-endian, header 5A A5). The flashed image is ONE reusable gauge page; the
// host turns it into any instrument at runtime by driving:
//
//	needle_0    @ 0x0011  vector needle, angle 45..315 (0deg=south, clockwise)
//	pngNumber_0 @ 0x0012  numeric readout, 0..999
//	LED ring    @ 0x1FF1  system reg: 0 off / 1 blue / 2 green / 4 red
//	brightness  @ 0x7001  system reg: 0..63
//
// Runtime commands are UART on CN2 (3.3V), NOT USB-C — see BUILD.md.
//
// Usage:
//
//	instrumentctl -demo                       # sweep needle+number, cycle ring
//	instrumentctl -value 320 -max 600         # show 320 on a 0..600 instrument
//	instrumentctl -ring green
//	instrumentctl -send "10 1FF1 0001"        # raw payload (cmd+addr+data)
//	instrumentctl -listen                     # decode incoming frames (turn/press knob)
//	instrumentctl -value 55 -max 100 -dry     # print frames, no hardware
//	instrumentctl -port /dev/cu.usbserial-XXXX -baud 115200 ...
package main

import (
	"bytes"
	"encoding/hex"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"go.bug.st/serial"
)

// Widget + system-register addresses (match Plugin/Page0000.cfg).
const (
	addrNeedle = 0x0011
	addrNumber = 0x0012
	addrRing   = 0x1FF1
	addrBright = 0x7001
	needleMin  = 45  // widget startAngle
	needleMax  = 315 // widget finalAngle
	numberMax  = 999 // 3-digit readout
)

var ringColors = map[string]uint16{"off": 0, "blue": 1, "green": 2, "red": 4}

// ---- framing (verified against datasheet, same as instrument.py) -----------

func crc16Modbus(data []byte) uint16 {
	crc := uint16(0xFFFF)
	for _, b := range data {
		crc ^= uint16(b)
		for i := 0; i < 8; i++ {
			if crc&1 != 0 {
				crc = (crc >> 1) ^ 0xA001
			} else {
				crc >>= 1
			}
		}
	}
	return crc
}

// buildFrame wraps payload (Cmd+Addr+Data) with Length, little-endian CRC, header.
func buildFrame(payload []byte) []byte {
	out := []byte{0x5A, 0xA5, byte(len(payload) + 2)}
	out = append(out, payload...)
	crc := crc16Modbus(payload)
	return append(out, byte(crc&0xFF), byte(crc>>8))
}

func writeCmd(addr, word uint16) []byte {
	return buildFrame([]byte{0x10, byte(addr >> 8), byte(addr), byte(word >> 8), byte(word)})
}

func selftest() bool {
	got := buildFrame(mustHex("10200151525354"))
	want := mustHex("5AA50910200151525354BC43")
	ok := bytes.Equal(got, want)
	status := "OK"
	if !ok {
		status = "BAD — refusing to transmit"
	}
	fmt.Printf("[crc selftest] % X  %s\n", got, status)
	return ok
}

func mustHex(s string) []byte {
	b, err := hex.DecodeString(strings.ReplaceAll(s, " ", ""))
	if err != nil {
		panic(err)
	}
	return b
}

// ---- value mapping ---------------------------------------------------------

func valueToAngle(value, vmax float64) uint16 {
	frac := 0.0
	if vmax > 0 {
		frac = value / vmax
	}
	frac = math.Max(0, math.Min(1, frac))
	return uint16(math.Round(needleMin + frac*(needleMax-needleMin)))
}

func clampDigits(value float64) uint16 {
	n := int(math.Round(value))
	if n < 0 {
		n = 0
	}
	if n > numberMax {
		n = numberMax
	}
	return uint16(n)
}

// ---- link ------------------------------------------------------------------

type Link struct {
	port serial.Port
	dry  bool
}

func (l *Link) send(label string, frame []byte) {
	fmt.Printf("  -> %-8s % X\n", label, frame)
	if l.port != nil {
		l.port.Write(frame)
	}
}

// sendQuiet writes without per-frame logging — for smooth animations.
func (l *Link) sendQuiet(frame []byte) {
	if l.port != nil {
		l.port.Write(frame)
	}
}

func (l *Link) setInstrument(value, vmax float64) {
	angle := valueToAngle(value, vmax)
	digits := clampDigits(value)
	l.send("needle", writeCmd(addrNeedle, angle))
	l.send("number", writeCmd(addrNumber, digits))
	fmt.Printf("[set] value=%.0f of %.0f -> needle %d deg, readout %d\n", value, vmax, angle, digits)
}

func (l *Link) ring(name string) {
	l.send("ring", writeCmd(addrRing, ringColors[name]))
	fmt.Printf("[ring] %s\n", name)
}

func (l *Link) demo(vmax float64) {
	fmt.Printf("[demo] sweeping 0..%.0f..0 and cycling the LED ring — watch the knob\n", vmax)
	var seq []float64
	for i := 0; i <= 50; i++ {
		seq = append(seq, float64(i)/50*vmax)
	}
	for i := 50; i >= 0; i-- {
		seq = append(seq, float64(i)/50*vmax)
	}
	for _, name := range []string{"blue", "green", "red"} {
		l.ring(name)
		for _, v := range seq {
			l.send("sweep", writeCmd(addrNeedle, valueToAngle(v, vmax)))
			l.send("sweep", writeCmd(addrNumber, clampDigits(v)))
			time.Sleep(30 * time.Millisecond)
		}
	}
	l.ring("blue")
	l.setInstrument(vmax*0.5, vmax)
}

// sweep drives ONLY the needle back and forth (no number, no ring) so you can judge
// the needle's smoothness in isolation — a diagnostic for the flicker.
func (l *Link) sweep(fullScale float64) {
	fmt.Println("[sweep] needle-only, no number/ring — watch: does the WHOLE panel blink, or is the needle smooth?")
	const dt = 55 * time.Millisecond
	lastAngle := uint16(0xFFFF)
	for cyc := 0; cyc < 3; cyc++ {
		for _, up := range []bool{true, false} {
			for k := 0; k <= 100; k++ {
				f := float64(k) / 100
				if !up {
					f = 1 - f
				}
				if ang := valueToAngle(f*fullScale, fullScale); ang != lastAngle {
					l.sendQuiet(writeCmd(addrNeedle, ang))
					lastAngle = ang
				}
				time.Sleep(dt)
			}
		}
	}
	fmt.Println("[sweep] done")
}

// rev animates a gentle marine-engine RPM sweep: idle 0 -> 1500 -> 2000 -> 1000 -> 0.
// The needle tracks RPM against fullScale; the 3-digit readout shows RPM/10 (a "x10"
// tach convention, since the flashed number widget maxes at 999); the LED ring shifts
// blue (idle) -> green (cruise) -> red (high rev). Motion is smoothstep-eased with a
// subtle flutter so it reads like a running engine rather than a linear ramp.
func (l *Link) rev(fullScale float64) {
	type waypoint struct{ rpm, secs float64 }
	profile := []waypoint{{1500, 4.0}, {2000, 2.5}, {1000, 3.0}, {0, 3.0}}
	fmt.Printf("[rev] marine engine 0->1500->2000->1000->0  |  full-scale %.0f rpm (needle), readout = rpm/10\n", fullScale)

	// ~18 fps is plenty: the needle only has ~55 discrete steps, so faster just adds
	// redundant redraws. We also send needle/number/ring ONLY when their value actually
	// changes — that alone kills most of the flicker, since a full-screen needle repaint
	// and a number repaint no longer fire on every tick.
	const dt = 55 * time.Millisecond
	cur, phase := 0.0, 0.0
	lastRing := ""
	lastAngle, lastNum := uint16(0xFFFF), uint16(0xFFFF)
	frame := 0
	for _, w := range profile {
		steps := int(w.secs / dt.Seconds())
		if steps < 1 {
			steps = 1
		}
		start := cur
		for i := 1; i <= steps; i++ {
			t := float64(i) / float64(steps)
			eased := t * t * (3 - 2*t) // smoothstep: gentle accel out, decel in
			rpm := start + (w.rpm-start)*eased
			phase += 0.5
			rpm += math.Sin(phase) * 12 * math.Min(rpm/400, 1) // subtle engine flutter, calm near idle
			if rpm < 0 {
				rpm = 0
			}
			if ang := valueToAngle(rpm, fullScale); ang != lastAngle {
				l.sendQuiet(writeCmd(addrNeedle, ang))
				lastAngle = ang
			}
			if num := uint16(math.Round(rpm / 10)); num != lastNum {
				l.sendQuiet(writeCmd(addrNumber, num))
				lastNum = num
			}
			ringName := "green"
			switch {
			case rpm < 250:
				ringName = "blue"
			case rpm > 1750:
				ringName = "red"
			}
			if ringName != lastRing {
				l.sendQuiet(writeCmd(addrRing, ringColors[ringName]))
				lastRing = ringName
			}
			if frame%3 == 0 {
				fmt.Printf("\r  %5.0f rpm   ", rpm)
			}
			frame++
			time.Sleep(dt)
		}
		cur = w.rpm
	}
	l.sendQuiet(writeCmd(addrNumber, 0))
	l.sendQuiet(writeCmd(addrRing, ringColors["off"]))
	fmt.Println("\n[rev] done — engine off")
}

// ---- generic tick canvas (gen_canvas.py) -----------------------------------
// One baked canvas of addressable slots; the host composes any instrument by
// writing image indices (ticks/slots) and a value. Flicker-free: only the ticks
// that change are re-written.
const (
	addrTick0 = 0x0100 // 60 ticks at 0x0100..0x013B; index 0..5
	addrTri   = 0x0140 // top reference triangle: 0 off,1 white,2 green,3 red
	addrMode  = 0x0141 // mode label: 0 RPM,1 COG HOLD,2 TWA HOLD,3 AP HOLD,4 OFF
	addrSym   = 0x0142 // symbol slot (symNames)
	addrUnit  = 0x0143 // unit slot (unitNames)
	addrValue = 0x0150 // 4-digit value 0..9999
	addrScreenRing = 0x0160 // ring behind ticks: 0 tach,1 rainbow,2 coolwarm,3 white,4 cyan,5 green,6 amber,7 red,8 blue,9 magenta,10 off
	nTicks    = 60
	// tick image indices — must match gen_canvas.py TICK_COLORS order.
	// bright 1..5, dim of a bright colour = bright+5, off last.
	tkGrey, tkWhite, tkGreen, tkYellow, tkRed, tkMagenta        = 0, 1, 2, 3, 4, 5
	tkDimWhite, tkDimGreen, tkDimYellow, tkDimRed, tkDimMagenta = 6, 7, 8, 9, 10
	tkOff                                                       = 11
	triWhite                                                    = 0 // triangle: 0 white,1 green,2 red,3 off
)

type Canvas struct {
	l    *Link
	tick [nTicks]int
}

func newCanvas(l *Link) *Canvas {
	c := &Canvas{l: l}
	for i := range c.tick {
		c.tick[i] = -1
	}
	return c
}
func (c *Canvas) set(addr uint16, v int) { c.l.sendQuiet(writeCmd(addr, uint16(v))) }
func (c *Canvas) setTick(i, idx int) { // change-gated -> no redundant redraws
	if idx != c.tick[i] {
		c.l.sendQuiet(writeCmd(uint16(addrTick0+i), uint16(idx)))
		c.tick[i] = idx
	}
}

// ---- instrument definitions ("active infrastructure per instrument type") ---
// Each instrument type is DATA: mode label, symbol, ring backdrop, min/max, a list
// of value-zones (each a tick colour), a sweep, and a render style. The host composes
// the SAME canvas into any instrument by name + a live value — no reflash.
const (
	ringOff = 10 // screen-ring "off" index (gen_canvas RING_ORDER)
	triOff  = 3  // triangle "off" index
)

type zone struct {
	from, to float64
	color    int // tick colour index (tkWhite/tkGreen/tkYellow/tkRed)
}
type instStyle int

const (
	styleFill    instStyle = iota // ticks light up to value, coloured by the zone they fall in
	stylePointer                  // single marker tick at the value's angle (compass/heading)
)

// Names mirror gen_canvas.py MODES / SYMBOLS / UNITS — looked up by name, so reordering
// the baked canvas lists can never desync the driver.
var modeNames = []string{"RPM", "DEPTH", "SPEED", "SOG", "STW", "TEMP", "FUEL", "WIND", "AWA", "TWA", "AWS", "TWS", "HDG", "COG", "CRS", "VOLTS", "AMPS", "TRIP", "OFF"}
var symNames = []string{"gauge", "depth", "speed", "temp", "fuel", "wind", "autopilot", "battery", "anchor", "engine", "water", "gps"}
var unitNames = []string{"", "\u00b0C", "\u00b0F", "\u00b0", "%", "kn", "V", "A", "m", "ft", "nm", "rpm"}

func nameIdx(list []string, name string) int {
	for i, n := range list {
		if n == name {
			return i
		}
	}
	return 0
}

type instrument struct {
	mode, symbol, unit string // looked up in modeNames / symNames / unitNames
	ring               int
	min, max           float64
	zones              []zone
	sweep              float64 // 270 = gauge (gap at bottom) · 360 = compass
	style              instStyle
	numDiv             float64
}

// The instrument set — add an entry and it's immediately drivable (-instrument NAME).
var instruments = map[string]instrument{
	"rpm":   {mode: "RPM", symbol: "gauge", unit: "rpm", ring: ringOff, min: 0, max: 3000, sweep: 270, style: styleFill, numDiv: 1, zones: []zone{{0, 2000, tkWhite}, {2000, 2500, tkYellow}, {2500, 3000, tkRed}}},
	"temp":  {mode: "TEMP", symbol: "temp", unit: "\u00b0C", ring: ringOff, min: 40, max: 90, sweep: 270, style: styleFill, numDiv: 1, zones: []zone{{40, 50, tkWhite}, {50, 70, tkGreen}, {70, 80, tkYellow}, {80, 90, tkRed}}},
	"fuel":  {mode: "FUEL", symbol: "fuel", unit: "%", ring: ringOff, min: 0, max: 100, sweep: 270, style: styleFill, numDiv: 1, zones: []zone{{0, 10, tkRed}, {10, 25, tkYellow}, {25, 100, tkWhite}}},
	"depth": {mode: "DEPTH", symbol: "depth", unit: "m", ring: ringOff, min: 0, max: 100, sweep: 270, style: styleFill, numDiv: 1, zones: []zone{{0, 3, tkRed}, {3, 10, tkYellow}, {10, 100, tkWhite}}},
	"speed": {mode: "SPEED", symbol: "speed", unit: "kn", ring: ringOff, min: 0, max: 30, sweep: 270, style: styleFill, numDiv: 1, zones: []zone{{0, 30, tkWhite}}},
	"volts": {mode: "VOLTS", symbol: "battery", unit: "V", ring: ringOff, min: 10, max: 15, sweep: 270, style: styleFill, numDiv: 1, zones: []zone{{10, 12, tkRed}, {12, 12.5, tkYellow}, {12.5, 15, tkGreen}}},
}

func zoneColor(zs []zone, v float64) int {
	for _, z := range zs {
		if v >= z.from && v < z.to {
			return z.color
		}
	}
	if len(zs) > 0 {
		if v >= zs[len(zs)-1].to {
			return zs[len(zs)-1].color
		}
		return zs[0].color
	}
	return tkGrey
}

// tickFrac gives tick i's 0..1 position along the sweep and whether it's visible.
// A 270deg sweep hides the bottom 90deg (a 15-tick gap centred on 6 o'clock).
func tickFrac(i int, sweep float64) (float64, bool) {
	if sweep >= 360 {
		return float64(i) / float64(nTicks), true
	}
	gap := int(math.Round(float64(nTicks) * (360 - sweep) / 360))
	vis := nTicks - gap
	firstVis := ((nTicks/2 - gap/2) + gap) % nTicks
	si := (i - firstVis + nTicks) % nTicks
	if si >= vis {
		return 0, false
	}
	return float64(si) / float64(vis-1), true
}

// compose renders one instrument at `value` onto the canvas (change-gated ticks).
func (c *Canvas) compose(in instrument, value float64) {
	c.set(addrMode, nameIdx(modeNames, in.mode))
	c.set(addrSym, nameIdx(symNames, in.symbol))
	c.set(addrUnit, nameIdx(unitNames, in.unit))
	c.set(addrScreenRing, in.ring)
	c.set(addrTri, triOff) // no top triangle on these gauges
	for i := 0; i < nTicks; i++ {
		frac, vis := tickFrac(i, in.sweep)
		idx := tkOff
		if vis && in.style == styleFill {
			tv := in.min + frac*(in.max-in.min)
			base := zoneColor(in.zones, tv) // bright zone colour 1..5
			if tv <= value {
				idx = base // bright fill up to the reading
			} else {
				idx = base + 5 // dim of the zone colour, out to max
			}
		}
		c.setTick(i, idx)
	}
	n := int(math.Round(value / in.numDiv))
	if n < 0 {
		n = 0
	} else if n > 9999 {
		n = 9999
	}
	c.set(addrValue, n)
}

// animate sweeps an instrument min..max..min as a visual demo.
func (c *Canvas) animate(name string, in instrument) {
	fmt.Printf("[instrument] %s  %.0f..%.0f\n", name, in.min, in.max)
	const dt = 45 * time.Millisecond
	const steps = 60
	val := func(k int) float64 { return in.min + float64(k)/steps*(in.max-in.min) }
	for k := 0; k <= steps; k++ {
		c.compose(in, val(k))
		fmt.Printf("\r  %.0f    ", val(k))
		time.Sleep(dt)
	}
	for k := steps; k >= 0; k-- {
		c.compose(in, val(k))
		time.Sleep(dt)
	}
	fmt.Println("\n[instrument] done")
}

// composeAutopilot renders the heading/autopilot instrument: a 270deg ring of DIM
// target-colour ticks (red = port / green = starboard / magenta = CRS), 2 BRIGHT
// ticks at the desired heading, and a white lubber triangle at top-dead-centre
// (= current heading). Off mode shows a plain grey ring.
func (c *Canvas) composeAutopilot(mode string, desired, current float64) {
	c.set(addrMode, nameIdx(modeNames, strings.ToUpper(mode)))
	c.set(addrSym, nameIdx(symNames, "autopilot"))
	c.set(addrUnit, nameIdx(unitNames, "\u00b0"))
	c.set(addrScreenRing, ringOff)
	c.set(addrTri, triWhite) // white triangle top-dead-centre = current heading

	if mode == "off" {
		for i := 0; i < nTicks; i++ {
			if _, vis := tickFrac(i, 270); vis {
				c.setTick(i, tkGrey)
			} else {
				c.setTick(i, tkOff)
			}
		}
		c.set(addrValue, 0)
		return
	}
	offset := math.Mod(desired-current+540, 360) - 180 // relative bearing -180..+180
	bright, dim := tkGreen, tkDimGreen                 // starboard (right of centre)
	if mode == "crs" {
		bright, dim = tkMagenta, tkDimMagenta
	} else if offset < 0 {
		bright, dim = tkRed, tkDimRed // port (left of centre)
	}
	tgt := ((int(math.Round(offset/6)) % nTicks) + nTicks) % nTicks
	for i := 0; i < nTicks; i++ {
		_, vis := tickFrac(i, 270)
		idx := tkOff
		if vis {
			if i == tgt || i == (tgt+1)%nTicks {
				idx = bright // 2 bright ticks mark the desired heading
			} else {
				idx = dim
			}
		}
		c.setTick(i, idx)
	}
	c.set(addrValue, int(math.Round(math.Mod(desired+360, 360))))
}

// ---- incoming frame decode (for -listen) -----------------------------------

func label(cmd byte) string {
	switch cmd {
	case 0x10:
		return "WRITE-ack"
	case 0x03:
		return "READ-return"
	case 0x41:
		return "TOUCH/RETURN"
	default:
		return fmt.Sprintf("0x%02X", cmd)
	}
}

// decodeFrames pulls complete frames out of buf, printing each, and returns the remainder.
func decodeFrames(buf []byte) []byte {
	for {
		i := bytes.Index(buf, []byte{0x5A, 0xA5})
		if i < 0 || len(buf) < i+3 {
			return buf
		}
		length := int(buf[i+2])
		end := i + 3 + length
		if len(buf) < end {
			return buf
		}
		frame := buf[i:end]
		cmd := frame[3]
		addr := "----"
		data := []byte{}
		if length >= 4 {
			addr = fmt.Sprintf("0x%02X%02X", frame[4], frame[5])
			data = frame[6 : len(frame)-2]
		}
		fmt.Printf("  <- %-13s addr=%s data=% X  raw=% X\n", label(cmd), addr, data, frame)
		buf = buf[end:]
	}
}

func (l *Link) listen(dur time.Duration) {
	if l.port == nil {
		fmt.Println("[listen] no port (dry run)")
		return
	}
	fmt.Printf("[listen] decoding frames for %s — turn/press the knob…\n", dur)
	l.port.SetReadTimeout(200 * time.Millisecond)
	var buf []byte
	tmp := make([]byte, 128)
	deadline := time.Now().Add(dur)
	for time.Now().Before(deadline) {
		n, err := l.port.Read(tmp)
		if n > 0 {
			buf = append(buf, tmp[:n]...)
			buf = decodeFrames(buf)
		}
		if err != nil {
			break
		}
	}
}

// ---- port discovery --------------------------------------------------------

func findPort() string {
	for _, pat := range []string{
		"/dev/cu.usbserial*", "/dev/cu.usbmodem*", "/dev/cu.wchusbserial*",
		"/dev/cu.SLAB_USBtoUART*", "/dev/ttyUSB*", "/dev/ttyACM*",
	} {
		if m, _ := filepath.Glob(pat); len(m) > 0 {
			return m[0]
		}
	}
	return ""
}

// ---- main ------------------------------------------------------------------

func main() {
	port := flag.String("port", "", "serial port (auto-detect if empty)")
	baud := flag.Int("baud", 115200, "baud rate")
	value := flag.Float64("value", math.NaN(), "value to display on the instrument")
	vmax := flag.Float64("max", 999, "full-scale value")
	ring := flag.String("ring", "", "LED ring colour: off|blue|green|red")
	demo := flag.Bool("demo", false, "sweep the needle+number and cycle the ring")
	rev := flag.Bool("rev", false, "animate a gentle marine-engine rev: 0->1500->2000->1000->0 rpm")
	sweep := flag.Bool("sweep", false, "diagnostic: needle-only back-and-forth (no number/ring)")
	rpm := flag.Bool("rpm", false, "tick-canvas: gentle RPM rev (0->2800->0, redline 2500-3000)")
	screenring := flag.Int("screenring", -1, "set ring behind ticks 0-10 (0 tach,1 rainbow,2 coolwarm,3 white,4 cyan,5 green,6 amber,7 red,8 blue,9 magenta,10 off)")
	instrument := flag.String("instrument", "", "instrument type: rpm|temp|fuel  (add -value N for a static reading, else it animates)")
	autopilot := flag.String("autopilot", "", "autopilot mode: off|cog|twa|crs")
	heading := flag.Float64("heading", math.NaN(), "autopilot desired heading (deg)")
	current := flag.Float64("current", 0, "autopilot current heading (deg, 0 = top-dead-centre)")
	ping := flag.Bool("ping", false, "write a harmless command and print any device reply (verifies device->host RX path)")
	send := flag.String("send", "", "raw payload hex (cmd+addr+data), e.g. \"10 1FF1 0001\"")
	listen := flag.Bool("listen", false, "listen and decode incoming frames")
	dry := flag.Bool("dry", false, "print frames without opening a port")
	flag.Parse()

	if !selftest() {
		os.Exit(1)
	}

	l := &Link{dry: *dry}
	if !*dry {
		p := *port
		if p == "" {
			p = findPort()
		}
		if p == "" {
			fmt.Println("No serial port found. Plug in the USB-TTL adapter (ls /dev/cu.*), or use -dry.")
			os.Exit(1)
		}
		sp, err := serial.Open(p, &serial.Mode{BaudRate: *baud})
		if err != nil {
			fmt.Printf("could not open %s: %v\n", p, err)
			os.Exit(1)
		}
		defer sp.Close()
		l.port = sp
		fmt.Printf("[port] %s @ %d\n", p, *baud)
	} else {
		fmt.Println("[dry-run] not opening a port; frames are printed only")
	}

	switch {
	case *ping:
		l.send("bright", writeCmd(addrBright, 0x3F))
		if l.port != nil {
			l.port.SetReadTimeout(1500 * time.Millisecond)
			buf := make([]byte, 256)
			n, _ := l.port.Read(buf)
			if n > 0 {
				fmt.Printf("  <- %d bytes: % X   device->host OK — input will work once reportToHost is enabled\n", n, buf[:n])
			} else {
				fmt.Println("  <- silent. Check adapter RXD <- CN2 pin4(TX), or the module isn't ACKing over CN2.")
			}
		}
	case *screenring >= 0:
		newCanvas(l).set(addrScreenRing, *screenring)
		fmt.Printf("[screenring] set to %d\n", *screenring)
	case *instrument != "":
		in, ok := instruments[*instrument]
		if !ok {
			fmt.Printf("unknown instrument %q — have: rpm, temp, fuel\n", *instrument)
			os.Exit(1)
		}
		cv := newCanvas(l)
		if !math.IsNaN(*value) {
			cv.compose(in, *value)
			fmt.Printf("[instrument] %s = %.0f\n", *instrument, *value)
		} else {
			cv.animate(*instrument, in)
		}
	case *autopilot != "":
		h := *heading
		if math.IsNaN(h) {
			h = 90 // demo default
		}
		newCanvas(l).composeAutopilot(*autopilot, h, *current)
		fmt.Printf("[autopilot] %s  desired=%.0f  current=%.0f\n", *autopilot, h, *current)
	case *rpm:
		newCanvas(l).animate("rpm", instruments["rpm"])
	case *sweep:
		fs := *vmax
		if fs == 999 {
			fs = 3000
		}
		l.sweep(fs)
	case *rev:
		fs := *vmax
		if fs == 999 { // default -max -> sensible marine tach full-scale
			fs = 3000
		}
		l.rev(fs)
	case *demo:
		l.demo(*vmax)
	case *send != "":
		payload, err := hex.DecodeString(strings.ReplaceAll(*send, " ", ""))
		if err != nil {
			fmt.Printf("bad -send hex: %v\n", err)
			os.Exit(1)
		}
		l.send("raw", buildFrame(payload))
	case *listen:
		l.listen(20 * time.Second)
	case !math.IsNaN(*value):
		l.setInstrument(*value, *vmax)
	case *ring != "":
		if _, ok := ringColors[*ring]; !ok {
			fmt.Println("ring must be off|blue|green|red")
			os.Exit(1)
		}
		l.ring(*ring)
	default:
		flag.Usage()
	}
}
