// Command flashprobe is a SAFE, read-first probe of the LT168A/LT268A boot ROM
// while the module is in flash/update mode. It is step 1 of replacing the Windows
// LT_Uart_GUI flasher with our own Go tool: before we can send erase/program
// commands we need to see the boot ROM's handshake + banner, and confirm the baud.
//
// SAFETY: by default this ONLY LISTENS — it never writes to the device, so it
// cannot erase or program anything. Writing happens solely via -probe / -sweep,
// which send only the short bytes you pass (never an erase/program/address frame),
// so at worst the boot ROM ignores them. It never touches the MCU-code region.
//
// How to enter flash mode (from the LT268x MCU/Flash Programming Note):
//   power the module with its BUSY pin held to GND → it enters USB_Update mode and
//   enumerates on the Mac as /dev/cu.usbmodem*  (NOT the runtime cu.usbserial-* CN2
//   port). The boot ROM announces itself as e.g. "LT268A Boot_Version:21060301".
//
// Usage:
//   flashprobe -listen                 # open first cu.usbmodem*, hexdump anything it sends
//   flashprobe -listen -port /dev/cu.usbmodem1101 -secs 15
//   flashprobe -listen -sweepbaud      # cycle common baud rates, listening at each
//   flashprobe -probe 5A               # (opt-in) send one byte, then show the reply
//   flashprobe -sweep                  # (opt-in) try a curated set of 1-byte sync probes
//
// Paste the hexdump back and we decode the handshake to build the real flasher.
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"go.bug.st/serial"
)

// commonBauds are the rates the LT_Uart_GUI exposes (9600..921600); flash mode is a
// USB-CDC virtual port so baud is often ignored, but we sweep to be sure.
var commonBauds = []int{115200, 9600, 19200, 38400, 57600, 230400, 256000, 460800, 921600}

// safeSyncProbes: single bytes only. A real erase/program command is a multi-byte
// framed packet with a 32-bit address, so no single byte here can trigger one — the
// boot ROM either replies with its banner/ack or ignores the byte. These are the
// usual suspects for "wake up / report version" on UART boot ROMs.
var safeSyncProbes = []byte{0x5A, 0xA5, 0x00, 0xAA, 0x55, 0x7F, 0x0D}

func autoPort() string {
	// Flash mode shows as cu.usbmodem*; prefer that, then any cu.* serial device.
	for _, pat := range []string{
		"/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.SLAB*",
	} {
		if m, _ := filepath.Glob(pat); len(m) > 0 {
			return m[0]
		}
	}
	return ""
}

func openPort(name string, baud int) (serial.Port, error) {
	return serial.Open(name, &serial.Mode{
		BaudRate: baud, DataBits: 8, Parity: serial.NoParity, StopBits: serial.OneStopBit,
	})
}

// hexdump prints classic offset | hex | ascii rows and returns total bytes shown.
func hexdump(b []byte) {
	for off := 0; off < len(b); off += 16 {
		end := off + 16
		if end > len(b) {
			end = len(b)
		}
		row := b[off:end]
		var hx, as strings.Builder
		for i := 0; i < 16; i++ {
			if i < len(row) {
				fmt.Fprintf(&hx, "%02X ", row[i])
				c := row[i]
				if c >= 0x20 && c < 0x7f {
					as.WriteByte(c)
				} else {
					as.WriteByte('.')
				}
			} else {
				hx.WriteString("   ")
			}
			if i == 7 {
				hx.WriteByte(' ')
			}
		}
		fmt.Printf("  %04X  %s |%s|\n", off, hx.String(), as.String())
	}
}

// drain reads for d, returning everything received (boot ROM may send in bursts).
func drain(p serial.Port, d time.Duration) []byte {
	_ = p.SetReadTimeout(300 * time.Millisecond)
	var got []byte
	deadline := time.Now().Add(d)
	buf := make([]byte, 4096)
	for time.Now().Before(deadline) {
		n, err := p.Read(buf)
		if n > 0 {
			got = append(got, buf[:n]...)
		}
		if err != nil {
			break
		}
	}
	return got
}

func report(tag string, b []byte) {
	if len(b) == 0 {
		fmt.Printf("  [%s] (silence — no bytes)\n", tag)
		return
	}
	fmt.Printf("  [%s] %d bytes:\n", tag, len(b))
	hexdump(b)
	// surface any ASCII run (the "…Boot_Version:…" banner is ASCII)
	if s := asciiRuns(b, 4); s != "" {
		fmt.Printf("  [%s] ascii: %s\n", tag, s)
	}
}

func asciiRuns(b []byte, min int) string {
	var out []string
	start := -1
	for i := 0; i <= len(b); i++ {
		printable := i < len(b) && b[i] >= 0x20 && b[i] < 0x7f
		if printable && start < 0 {
			start = i
		} else if !printable && start >= 0 {
			if i-start >= min {
				out = append(out, fmt.Sprintf("%q", string(b[start:i])))
			}
			start = -1
		}
	}
	return strings.Join(out, " ")
}

func main() {
	var (
		portName  = flag.String("port", "", "serial port (default: first /dev/cu.usbmodem*)")
		baud      = flag.Int("baud", 115200, "baud rate")
		secs      = flag.Int("secs", 10, "seconds to listen")
		listen    = flag.Bool("listen", false, "listen only (safe; never writes)")
		sweepBaud = flag.Bool("sweepbaud", false, "listen at each common baud rate in turn")
		probe     = flag.String("probe", "", "OPT-IN: send these hex bytes (e.g. 5A) then listen")
		sweep     = flag.Bool("sweep", false, "OPT-IN: try a curated set of safe 1-byte sync probes")
	)
	flag.Parse()

	if !*listen && !*sweepBaud && *probe == "" && !*sweep {
		fmt.Println("flashprobe — safe boot-ROM probe (LT168A/LT268A flash mode)")
		fmt.Println("  put the knob in flash mode first: power it with BUSY held to GND,")
		fmt.Println("  it then appears as /dev/cu.usbmodem*  (not the CN2 cu.usbserial-*)")
		fmt.Println()
		fmt.Println("  flashprobe -listen                 listen only, hexdump what it sends")
		fmt.Println("  flashprobe -listen -sweepbaud      try every baud rate")
		fmt.Println("  flashprobe -probe 5A               send one byte, show reply (opt-in)")
		fmt.Println("  flashprobe -sweep                  try safe 1-byte sync probes (opt-in)")
		os.Exit(0)
	}

	name := *portName
	if name == "" {
		name = autoPort()
	}
	if name == "" {
		fmt.Println("No serial port found. Is the knob in flash mode (BUSY→GND at power-on)?")
		fmt.Println("Check with:  ls /dev/cu.*")
		os.Exit(1)
	}
	fmt.Printf("port: %s\n", name)

	// -sweepbaud: listen at each rate, report which produces bytes.
	if *sweepBaud {
		fmt.Printf("listening %ds at each baud (looking for the boot banner)…\n", *secs)
		for _, b := range commonBauds {
			p, err := openPort(name, b)
			if err != nil {
				fmt.Printf("  baud %-6d: open error: %v\n", b, err)
				continue
			}
			got := drain(p, time.Duration(*secs)*time.Second/time.Duration(len(commonBauds))+time.Second)
			p.Close()
			fmt.Printf("  --- baud %d ---\n", b)
			report(fmt.Sprintf("%d", b), got)
		}
		return
	}

	p, err := openPort(name, *baud)
	if err != nil {
		fmt.Printf("open %s @ %d failed: %v\n", name, *baud, err)
		os.Exit(1)
	}
	defer p.Close()
	fmt.Printf("baud: %d\n", *baud)

	// -sweep: send each safe single-byte sync, watch for a reply.
	if *sweep {
		fmt.Println("sending curated safe 1-byte sync probes (each is harmless on its own)…")
		for _, sb := range safeSyncProbes {
			_ = p.ResetInputBuffer()
			if _, err := p.Write([]byte{sb}); err != nil {
				fmt.Printf("  probe 0x%02X: write error: %v\n", sb, err)
				continue
			}
			got := drain(p, 1500*time.Millisecond)
			fmt.Printf("  --- probe 0x%02X ---\n", sb)
			report(fmt.Sprintf("0x%02X", sb), got)
		}
		return
	}

	// -probe: send caller-supplied hex, then listen.
	if *probe != "" {
		raw, perr := parseHex(*probe)
		if perr != nil {
			fmt.Printf("bad -probe hex %q: %v\n", *probe, perr)
			os.Exit(2)
		}
		fmt.Printf("sending % X …\n", raw)
		_ = p.ResetInputBuffer()
		if _, err := p.Write(raw); err != nil {
			fmt.Printf("write error: %v\n", err)
			os.Exit(1)
		}
	}

	// listen (also the tail of -probe)
	fmt.Printf("listening %ds… (Ctrl-C to stop)\n", *secs)
	got := drain(p, time.Duration(*secs)*time.Second)
	report("rx", got)
}

func parseHex(s string) ([]byte, error) {
	s = strings.NewReplacer(" ", "", ",", "", "0x", "", "0X", "").Replace(s)
	if len(s)%2 != 0 {
		return nil, fmt.Errorf("odd number of hex digits")
	}
	out := make([]byte, len(s)/2)
	for i := 0; i < len(out); i++ {
		var v int
		if _, err := fmt.Sscanf(s[i*2:i*2+2], "%02x", &v); err != nil {
			return nil, err
		}
		out[i] = byte(v)
	}
	return out, nil
}
