#!/usr/bin/env python3
"""GBA BIOS compression codecs: LZ77 (SWI 0x11/0x12), Huffman (SWI 0x13), RLE (SWI 0x14/0x15).

Pure Python, no dependencies. Every format uses the same 4-byte header:

    byte 0      : type nibble in bits 4-7 (1 = LZ77, 2 = Huffman, 3 = RLE),
                  low nibble = Huffman data-size in bits (4 or 8), else 0
    bytes 1-3   : decompressed size, little-endian 24-bit

Usage examples
    gba_compress.py info       rom.gba 0x1B27FC
    gba_compress.py decompress rom.gba 0x081B27FC -o level.bin          # bus address or file offset
    gba_compress.py decompress rom.gba 0x1B27FC --chain -o level.bin    # keep peeling layers (e.g. Huffman then LZ77)
    gba_compress.py compress   level.bin --type lz77 --vram -o level.lz
    gba_compress.py compress   level.bin --type lz77,huffman -o level.huf   # apply LZ77 first, then Huffman (mirrors --chain)
    gba_compress.py scan       rom.gba --min-size 256 --verify

As a library:
    from gba_compress import lz77_decompress, lz77_compress, huffman_decompress, huffman_compress, rle_decompress, rle_compress
"""
from __future__ import annotations

import argparse
import heapq
import struct
import sys
from collections import Counter, deque

ROM_BASE = 0x08000000


# ----------------------------------------------------------------------------- header helpers

def parse_header(data: bytes, offset: int = 0):
    """Return (type_name, decompressed_size, data_bits) or None if the header is not plausible."""
    if offset + 4 > len(data):
        return None
    b0 = data[offset]
    size = data[offset + 1] | (data[offset + 2] << 8) | (data[offset + 3] << 16)
    kind = b0 >> 4
    low = b0 & 0xF
    if kind == 1 and low == 0:
        return ("lz77", size, 0)
    if kind == 2 and low in (4, 8):
        return ("huffman", size, low)
    if kind == 3 and low == 0:
        return ("rle", size, 0)
    return None


def _header(type_nibble: int, size: int, low: int = 0) -> bytes:
    if size >= 1 << 24:
        raise ValueError("decompressed size does not fit in 24 bits")
    return bytes([(type_nibble << 4) | low, size & 0xFF, (size >> 8) & 0xFF, (size >> 16) & 0xFF])


class DecompressError(ValueError):
    pass


# ----------------------------------------------------------------------------- LZ77

def lz77_decompress(data: bytes, offset: int = 0) -> bytes:
    """Decode a GBA LZ77 (type 0x10) block. Raises DecompressError on malformed input."""
    hdr = parse_header(data, offset)
    if not hdr or hdr[0] != "lz77":
        raise DecompressError("not an LZ77 header (expected 0x10)")
    size = hdr[1]
    out = bytearray()
    pos = offset + 4
    n = len(data)
    while len(out) < size:
        if pos >= n:
            raise DecompressError("truncated LZ77 stream")
        flags = data[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if flags & (1 << bit):
                if pos + 2 > n:
                    raise DecompressError("truncated LZ77 reference")
                b1, b2 = data[pos], data[pos + 1]
                pos += 2
                length = (b1 >> 4) + 3
                disp = (((b1 & 0xF) << 8) | b2) + 1
                if disp > len(out):
                    raise DecompressError("LZ77 back-reference before start of output")
                for _ in range(length):
                    out.append(out[-disp])
            else:
                if pos >= n:
                    raise DecompressError("truncated LZ77 literal")
                out.append(data[pos])
                pos += 1
    return bytes(out[:size])


def lz77_compress(raw: bytes, vram_safe: bool = False) -> bytes:
    """Encode with GBA LZ77. vram_safe=True forbids displacement 1 (required for SWI 0x12 / VRAM targets).

    Greedy longest-match with a hash chain; output is bit-exact decodable by the BIOS.
    """
    min_disp = 2 if vram_safe else 1
    n = len(raw)
    out = bytearray(_header(1, n))
    # hash of 3-byte prefixes -> list of positions (most recent last)
    table: dict[bytes, list[int]] = {}
    i = 0
    while i < n:
        flag_pos = len(out)
        out.append(0)
        flags = 0
        for bit in range(7, -1, -1):
            if i >= n:
                break
            best_len, best_disp = 0, 0
            if i + 3 <= n:
                key = raw[i:i + 3]
                cands = table.get(key, ())
                for j in reversed(cands[-64:]):
                    disp = i - j
                    if disp < min_disp:
                        continue
                    if disp > 0x1000:
                        break
                    length = 0
                    limit = min(18, n - i)
                    while length < limit and raw[j + length] == raw[i + length]:
                        length += 1
                    if length > best_len:
                        best_len, best_disp = length, disp
                        if length == 18:
                            break
            if best_len >= 3:
                flags |= 1 << bit
                out.append(((best_len - 3) << 4) | ((best_disp - 1) >> 8))
                out.append((best_disp - 1) & 0xFF)
                for k in range(i, i + best_len):
                    if k + 3 <= n:
                        table.setdefault(raw[k:k + 3], []).append(k)
                i += best_len
            else:
                out.append(raw[i])
                if i + 3 <= n:
                    table.setdefault(raw[i:i + 3], []).append(i)
                i += 1
        out[flag_pos] = flags
    while len(out) % 4:
        out.append(0)
    return bytes(out)


# ----------------------------------------------------------------------------- Huffman

def huffman_decompress(data: bytes, offset: int = 0) -> bytes:
    """Decode a GBA Huffman (type 0x2N) block; N is the symbol size in bits (4 or 8)."""
    hdr = parse_header(data, offset)
    if not hdr or hdr[0] != "huffman":
        raise DecompressError("not a Huffman header (expected 0x24 or 0x28)")
    size, bits = hdr[1], hdr[2]
    tree_start = offset + 4
    if tree_start >= len(data):
        raise DecompressError("truncated Huffman tree")
    tree_len = (data[tree_start] + 1) * 2
    stream_pos = tree_start + tree_len
    root = tree_start + 1
    out = bytearray()
    node = root
    is_data = False
    units = 0
    total_units = (size * 8) // bits
    cur_byte = 0
    unit_in_byte = 0
    units_per_byte = 8 // bits
    while units < total_units:
        if stream_pos + 4 > len(data):
            raise DecompressError("truncated Huffman bitstream")
        word = struct.unpack_from("<I", data, stream_pos)[0]
        stream_pos += 4
        for shift in range(31, -1, -1):
            bit = (word >> shift) & 1
            if node >= len(data):
                raise DecompressError("Huffman tree walk out of range")
            nb = data[node]
            child = (node & ~1) + ((nb & 0x3F) * 2) + 2 + bit
            if child >= len(data):
                raise DecompressError("Huffman child out of range")
            is_data = bool(nb & (0x80 >> bit))
            if is_data:
                sym = data[child]
                cur_byte |= (sym & ((1 << bits) - 1)) << (unit_in_byte * bits)
                unit_in_byte += 1
                if unit_in_byte == units_per_byte:
                    out.append(cur_byte)
                    cur_byte, unit_in_byte = 0, 0
                units += 1
                node = root
                if units >= total_units:
                    break
            else:
                node = child
    return bytes(out[:size])


def _huffman_layout(freq: Counter):
    """Build a Huffman tree and lay it out breadth-first as GBA node bytes. Returns (table_bytes, codes) or None if offsets overflow."""
    if len(freq) == 1:
        # Degenerate: duplicate the symbol so the root has two children.
        sym = next(iter(freq))
        freq = Counter({sym: freq[sym], (sym + 1) & 0xFF if sym != 0xFF else 0: 0})
    heap = []
    counter = 0
    for sym, f in freq.items():
        heap.append((f, counter, ("leaf", sym)))
        counter += 1
    heapq.heapify(heap)
    while len(heap) > 1:
        f1, _, a = heapq.heappop(heap)
        f2, _, b = heapq.heappop(heap)
        heap.append((f1 + f2, counter, ("node", a, b)))
        counter += 1
        heapq.heapify(heap)
    root = heap[0][2]

    codes: dict[int, tuple[int, int]] = {}

    def walk(t, code, depth):
        if t[0] == "leaf":
            codes[t[1]] = (code, depth)
        else:
            walk(t[1], code << 1, depth + 1)
            walk(t[2], (code << 1) | 1, depth + 1)

    walk(root, 0, 0)

    # Breadth-first layout. Table byte 0 is the size byte; root sits at index 1.
    table = bytearray([0, 0])
    queue = deque([(root, 1)])
    while queue:
        t, idx = queue.popleft()
        left, right = t[1], t[2]
        child_idx = len(table)
        off = (child_idx - (idx & ~1) - 2) // 2
        if off > 0x3F:
            return None
        nb = off
        if left[0] == "leaf":
            nb |= 0x80
        if right[0] == "leaf":
            nb |= 0x40
        table[idx] = nb
        for child in (left, right):
            if child[0] == "leaf":
                table.append(child[1])
            else:
                table.append(0)
                queue.append((child, len(table) - 1))
    while len(table) % 4:
        table.append(0)
    table[0] = (len(table) // 2) - 1
    return bytes(table), codes


def huffman_compress(raw: bytes, bits: int = 8) -> bytes:
    """Encode with GBA Huffman. bits=8 is tried first; a tree whose node offsets overflow 6 bits falls back to 4-bit symbols."""
    if bits not in (4, 8):
        raise ValueError("bits must be 4 or 8")
    for b in ((8, 4) if bits == 8 else (4,)):
        if b == 8:
            symbols = list(raw)
        else:
            symbols = []
            for byte in raw:
                symbols.append(byte & 0xF)
                symbols.append(byte >> 4)
        layout = _huffman_layout(Counter(symbols))
        if layout is None:
            continue
        table, codes = layout
        out = bytearray(_header(2, len(raw), b))
        out += table
        acc, nbits = 0, 0
        for s in symbols:
            code, length = codes[s]
            for i in range(length - 1, -1, -1):
                acc = (acc << 1) | ((code >> i) & 1)
                nbits += 1
                if nbits == 32:
                    out += struct.pack("<I", acc)
                    acc, nbits = 0, 0
        if nbits:
            out += struct.pack("<I", acc << (32 - nbits))
        return bytes(out)
    raise ValueError("could not lay out Huffman tree")


# ----------------------------------------------------------------------------- RLE

def rle_decompress(data: bytes, offset: int = 0) -> bytes:
    hdr = parse_header(data, offset)
    if not hdr or hdr[0] != "rle":
        raise DecompressError("not an RLE header (expected 0x30)")
    size = hdr[1]
    out = bytearray()
    pos = offset + 4
    while len(out) < size:
        if pos >= len(data):
            raise DecompressError("truncated RLE stream")
        flag = data[pos]
        pos += 1
        if flag & 0x80:
            length = (flag & 0x7F) + 3
            if pos >= len(data):
                raise DecompressError("truncated RLE run")
            out += bytes([data[pos]]) * length
            pos += 1
        else:
            length = (flag & 0x7F) + 1
            out += data[pos:pos + length]
            pos += length
    return bytes(out[:size])


def rle_compress(raw: bytes) -> bytes:
    out = bytearray(_header(3, len(raw)))
    i, n = 0, len(raw)
    while i < n:
        run = 1
        while i + run < n and raw[i + run] == raw[i] and run < 130:
            run += 1
        if run >= 3:
            out.append(0x80 | (run - 3))
            out.append(raw[i])
            i += run
            continue
        start = i
        while i < n and (i - start) < 128:
            nxt = 1
            while i + nxt < n and raw[i + nxt] == raw[i] and nxt < 3:
                nxt += 1
            if nxt >= 3:
                break
            i += 1
        if i == start:  # forced progress on a short run at the end
            i += 1
        out.append((i - start) - 1)
        out += raw[start:i]
    while len(out) % 4:
        out.append(0)
    return bytes(out)


# ----------------------------------------------------------------------------- generic

DECODERS = {"lz77": lz77_decompress, "huffman": huffman_decompress, "rle": rle_decompress}


def decompress(data: bytes, offset: int = 0, chain: bool = False):
    """Decompress the block at offset. With chain=True, keep decompressing while the output carries a valid header.

    Returns (bytes, [layer names outermost first]).
    """
    layers = []
    cur = data
    off = offset
    while True:
        hdr = parse_header(cur, off)
        if not hdr:
            if not layers:
                raise DecompressError("no valid compression header at offset 0x%X" % off)
            break
        cur = DECODERS[hdr[0]](cur, off)
        layers.append(hdr[0])
        off = 0
        if not chain:
            break
    return cur, layers


def parse_address(text: str) -> int:
    """Accept a ROM file offset or a GBA bus address (0x08xxxxxx / 0x09xxxxxx) and return a file offset."""
    v = int(text, 0)
    if 0x08000000 <= v < 0x0E000000:
        return v - ROM_BASE
    return v


def scan(data: bytes, min_size: int = 64, max_size: int = 4 << 20, verify: bool = True, align: int = 4):
    """Yield (offset, type, size, ok) for plausible compressed blocks."""
    n = len(data)
    for off in range(0, n - 4, align):
        hdr = parse_header(data, off)
        if not hdr:
            continue
        kind, size, _ = hdr
        if size < min_size or size > max_size:
            continue
        ok = None
        if verify:
            try:
                DECODERS[kind](data, off)
                ok = True
            except DecompressError:
                ok = False
        if ok is False:
            continue
        yield off, kind, size, ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="print the compression header at an offset")
    p.add_argument("file")
    p.add_argument("offset")

    p = sub.add_parser("decompress", help="decompress a block")
    p.add_argument("file")
    p.add_argument("offset", nargs="?", default="0")
    p.add_argument("-o", "--output")
    p.add_argument("--chain", action="store_true", help="keep decompressing nested layers (Huffman over LZ77 etc.)")

    p = sub.add_parser("compress", help="compress a file")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument("--type", default="lz77", help="lz77 | huffman | rle, or a comma list applied in order (e.g. lz77,huffman)")
    p.add_argument("--vram", action="store_true", help="LZ77 VRAM-safe (no displacement of 1)")
    p.add_argument("--bits", type=int, default=8, help="Huffman symbol size, 4 or 8")

    p = sub.add_parser("scan", help="list plausible compressed blocks in a ROM")
    p.add_argument("file")
    p.add_argument("--min-size", type=int, default=64)
    p.add_argument("--max-size", type=int, default=4 << 20)
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--align", type=int, default=4)

    a = ap.parse_args(argv)

    if a.cmd == "info":
        data = open(a.file, "rb").read()
        off = parse_address(a.offset)
        hdr = parse_header(data, off)
        if not hdr:
            print("0x%X: no valid GBA compression header (byte 0x%02X)" % (off, data[off]))
            return 1
        print("0x%X (bus 0x%08X): %s, decompressed size %d bytes%s" % (
            off, off + ROM_BASE, hdr[0], hdr[1], ", %d-bit symbols" % hdr[2] if hdr[2] else ""))
        return 0

    if a.cmd == "decompress":
        data = open(a.file, "rb").read()
        off = parse_address(a.offset)
        out, layers = decompress(data, off, chain=a.chain)
        dest = a.output or (a.file + ".dec")
        open(dest, "wb").write(out)
        print("layers: %s -> %d bytes written to %s" % (" > ".join(layers), len(out), dest))
        return 0

    if a.cmd == "compress":
        raw = open(a.file, "rb").read()
        cur = raw
        for t in a.type.split(","):
            t = t.strip()
            if t == "lz77":
                cur = lz77_compress(cur, vram_safe=a.vram)
            elif t == "huffman":
                cur = huffman_compress(cur, bits=a.bits)
            elif t == "rle":
                cur = rle_compress(cur)
            else:
                ap.error("unknown type %r" % t)
        dest = a.output or (a.file + "." + a.type.replace(",", "."))
        open(dest, "wb").write(cur)
        print("%d -> %d bytes (%.1f%%) written to %s" % (len(raw), len(cur), 100.0 * len(cur) / max(1, len(raw)), dest))
        return 0

    if a.cmd == "scan":
        data = open(a.file, "rb").read()
        count = 0
        for off, kind, size, ok in scan(data, a.min_size, a.max_size, not a.no_verify, a.align):
            print("0x%06X  bus 0x%08X  %-8s %8d bytes%s" % (off, off + ROM_BASE, kind, size, "" if ok is None else ("  verified" if ok else "")))
            count += 1
        print("%d candidate block(s)" % count, file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
