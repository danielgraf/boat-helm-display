// Command bintool inspects and diffs Levetop UartTFT-II_Flash.bin images
// (Anders 11807 / LT168A). It's the reverse-engineering aid for building a bin
// generator that can replace UI_Editor-II: `dump` reveals the structure, `diff`
// isolates what a source change moved in the bytes, `strings` finds the ini/version
// metadata. Generation is grown incrementally from what these teach us.
//
//	bintool dump  <bin>            structure: section directory + metadata
//	bintool diff  <a> <b>          byte diff (16-byte rows) + header-word deltas
//	bintool words <bin> [n]        first n uint32 words (LE) as hex+dec
//	bintool strings <bin> [min]    printable ASCII runs (ini/version/asset names)
package main

import (
	"encoding/binary"
	"fmt"
	"os"
	"sort"
)

func read(path string) []byte {
	b, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	return b
}

func u32(b []byte, off int) uint32 {
	if off+4 > len(b) {
		return 0
	}
	return binary.LittleEndian.Uint32(b[off:])
}

// looksAddr reports whether v plausibly points inside the file (a section offset).
func looksAddr(v uint32, size int) bool { return v > 0x40 && int(v) < size }

func dump(b []byte) {
	fmt.Printf("file: %d bytes (0x%X)\n\n", len(b), len(b))
	fmt.Println("=== section directory (uint32 LE pairs {addr,val}) ===")
	// The header opens with {address, count/flag} entries; print until it turns to
	// non-pointer data (the config/metadata region around the UserID).
	for off := 0; off < 0x80 && off+8 <= len(b); off += 8 {
		a, v := u32(b, off), u32(b, off+4)
		tag := ""
		if looksAddr(a, len(b)) {
			tag = "  <- addr"
			if v > 0 && v < 0x10000 {
				tag += fmt.Sprintf(" (count/flag=%d)", v)
			}
		}
		fmt.Printf("  [0x%02X] %08X  %08X%s\n", off, a, v, tag)
	}
	fmt.Println("\n=== metadata / config region (0x80..0xE0) ===")
	dumpStrings(b[0x80:min(0xE0, len(b))], 3, 0x80)
	fmt.Println("\n=== all printable runs >=5 (asset names, versions) ===")
	dumpStrings(b, 5, 0)
}

func dumpStrings(b []byte, minLen, base int) {
	start := -1
	emit := func(end int) {
		if start >= 0 && end-start >= minLen {
			fmt.Printf("  0x%06X  %q\n", base+start, string(b[start:end]))
		}
		start = -1
	}
	for i, c := range b {
		if c >= 0x20 && c < 0x7f {
			if start < 0 {
				start = i
			}
		} else {
			emit(i)
		}
	}
	emit(len(b))
}

func words(b []byte, n int) {
	fmt.Printf("file: %d bytes\n", len(b))
	for i := 0; i < n && i*4+4 <= len(b); i++ {
		v := u32(b, i*4)
		fmt.Printf("  [0x%04X] w%-3d = %08X  (%d)\n", i*4, i, v, v)
	}
}

func diff(a, b []byte) {
	fmt.Printf("a: %d bytes   b: %d bytes   Δsize: %+d\n\n", len(a), len(b), len(b)-len(a))
	// Header word-level deltas (most informative for the directory).
	fmt.Println("=== header word deltas (first 0x80, uint32 LE) ===")
	for off := 0; off < 0x80; off += 4 {
		av, bv := u32(a, off), u32(b, off)
		if av != bv {
			fmt.Printf("  [0x%02X]  a=%08X  b=%08X  Δ=%+d\n", off, av, bv, int64(bv)-int64(av))
		}
	}
	// Byte-range diffs (coalesce contiguous differing 16-byte rows into ranges).
	fmt.Println("\n=== differing byte ranges (16-byte granularity) ===")
	n := min(len(a), len(b))
	type rng struct{ start, end int }
	var ranges []rng
	inDiff := false
	var cur rng
	for off := 0; off < n; off += 16 {
		e := min(off+16, n)
		differ := false
		for i := off; i < e; i++ {
			if a[i] != b[i] {
				differ = true
				break
			}
		}
		if differ && !inDiff {
			cur = rng{off, e}
			inDiff = true
		} else if differ {
			cur.end = e
		} else if inDiff {
			ranges = append(ranges, cur)
			inDiff = false
		}
	}
	if inDiff {
		ranges = append(ranges, cur)
	}
	sort.Slice(ranges, func(i, j int) bool { return ranges[i].start < ranges[j].start })
	total := 0
	for _, r := range ranges {
		total += r.end - r.start
	}
	fmt.Printf("  %d differing ranges, %d bytes total (%.1f%% of overlap)\n", len(ranges), total, 100*float64(total)/float64(n))
	show := len(ranges)
	if show > 40 {
		show = 40
	}
	for _, r := range ranges[:show] {
		fmt.Printf("  0x%06X .. 0x%06X  (%d bytes)\n", r.start, r.end, r.end-r.start)
	}
	if len(ranges) > show {
		fmt.Printf("  … +%d more ranges\n", len(ranges)-show)
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func main() {
	if len(os.Args) < 3 {
		fmt.Println("usage: bintool <dump|diff|words|strings> <bin> [arg]")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "dump":
		dump(read(os.Args[2]))
	case "diff":
		if len(os.Args) < 4 {
			fmt.Println("usage: bintool diff <a> <b>")
			os.Exit(2)
		}
		diff(read(os.Args[2]), read(os.Args[3]))
	case "words":
		n := 32
		if len(os.Args) > 3 {
			fmt.Sscan(os.Args[3], &n)
		}
		words(read(os.Args[2]), n)
	case "strings":
		m := 5
		if len(os.Args) > 3 {
			fmt.Sscan(os.Args[3], &m)
		}
		dumpStrings(read(os.Args[2]), m, 0)
	default:
		fmt.Println("unknown command:", os.Args[1])
		os.Exit(2)
	}
}
