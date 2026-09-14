#!/usr/bin/env python3
"""Decoders for the custom resource formats in The Urbz: Sims in the City (GBA, AGB-BOCE, SHA-1 8efd2737...).

The engine's resource loader (ROM 0x1EC00) reads a 4-byte header `type:4 | flags:4 | size:24` and dispatches:
  0 raw copy, 1 LZ77 (BIOS), 2 Huffman (BIOS), 3 RLE (BIOS),
  4 -> decoder A (ROM 0xD274, copied to IWRAM 0x03003530), source includes the header,
  6 -> decoder B (ROM 0x2EC,  copied to IWRAM 0x030031D0), source starts after the header.
Bit 3 of the type nibble (header byte bit 7, e.g. 0xE0 = type 6 + filter) requests the Diff16 post-filter (ROM 0x1EE00).

Both decoders below are straight translations of the ARM routines; see FINDINGS.md for the disassembly notes.
"""
from __future__ import annotations
import struct, sys


class BitReader:
    """MSB-first bits from little-endian 32-bit words, with the ARM code's sentinel-buffer semantics."""

    def __init__(self, data: bytes, pos: int):
        self.data, self.pos = data, pos
        self.buf = 0x80000000

    def bit(self) -> int:
        carry = self.buf >> 31
        self.buf = (self.buf << 1) & 0xFFFFFFFF
        if self.buf == 0:
            w = struct.unpack_from("<I", self.data, self.pos)[0]
            self.pos += 4
            self.buf = ((w << 1) | carry) & 0xFFFFFFFF
            carry = w >> 31
        return carry

    def bits(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

    def code(self) -> int:
        """Elias-gamma style: n leading 1s (max 7) then a 0, then n bits; value = (1<<n)|bits, range 1..255."""
        n = 0
        while n < 7 and self.bit():
            n += 1
        return (1 << n) | self.bits(n) if n else 1


def decode_type6(data: bytes, offset: int, size: int | None = None, info: dict | None = None) -> bytes:
    """Decoder B. `offset` points at the 4-byte resource header; size defaults to the header's.
    If `info` is given, info["end"] receives the offset just past the last bitstream word."""
    hdr = struct.unpack_from("<I", data, offset)[0]
    if size is None:
        size = hdr >> 8
    p = offset + 4
    w0 = struct.unpack_from("<I", data, p)[0]
    p += 4
    tbl_len = w0 & 0xFF
    esc = (w0 >> 8) & 0xFF
    n_b = (w0 >> 16) & 0xFF        # extra distance bits
    n_c = w0 >> 24                 # high bits of a literal (compared with the escape value)
    n_a = 8 - n_c                  # low bits of a literal
    table = data[p:p + tbl_len]
    p += tbl_len
    br = BitReader(data, p)
    out = bytearray()

    def copy(dist: int, length: int):
        start = len(out) - dist - 1
        if start < 0:
            raise ValueError("back-reference before start (dist %d at %d)" % (dist, len(out)))
        for i in range(length):
            out.append(out[start + i])

    while True:
        v = br.bits(n_c) if n_c else 0
        if v != esc:
            if n_a:
                v = (v << n_a) | br.bits(n_a)
            out.append(v & 0xFF)
            continue
        c = br.code()
        if c >= 2:
            d = br.code()
            if d == 0xFF:
                break
            dist = d - 1
            if n_b:
                dist = (dist << n_b) | br.bits(n_b)
            dist = (dist << 8) | br.bits(8)
            copy(dist, c + 1)
        elif not br.bit():            # blo = carry clear: 8-bit distance, length 2
            copy(br.bits(8), 2)
        elif not br.bit():            # bits 1,0: literal whose high bits equal the escape; escape value changes
            new_esc = br.bits(n_c) if n_c else 0
            v = esc
            esc = new_esc
            if n_a:
                v = (v << n_a) | br.bits(n_a)
            out.append(v & 0xFF)
        else:                         # bits 1,1: fill run
            c1 = br.code()
            cnt, hi = c1, 0
            if c1 >= 0x80:
                cnt = ((c1 << 1) | br.bit()) & 0xFF
                hi = br.code() - 1
            f = br.code()
            if f < 0x20:
                fill = table[f - 1]
            else:
                fill = ((f << 3) | br.bits(3)) & 0xFF
            out += bytes([fill]) * (cnt + 1 + (hi << 8))
    if info is not None:
        info["end"] = br.pos
    return bytes(out[:size]) if size else bytes(out)


def type6_end(data: bytes, offset: int) -> int:
    """Offset just past the last bitstream word of a type-6 blob (used to reclaim its bytes when it is replaced)."""
    info: dict = {}
    decode_type6(data, offset, None, info)
    return info["end"]


def decode_type4(data: bytes, offset: int) -> bytes:
    """Decoder A: control-byte LZ/RLE. Source includes the 4-byte header.
    ctl == 0: end. ctl & 0x80: back-reference (ctl&0x7F, next byte -> dist/len). ctl & 0x40: fill run. else literal run."""
    hdr = struct.unpack_from("<I", data, offset)[0]
    size = hdr >> 8
    p = offset + 4
    out = bytearray()
    while True:
        ctl = data[p]; p += 1
        if ctl == 0:
            break
        if ctl & 0x80:
            r3 = ctl & 0x7F
            b = data[p]; p += 1
            r2 = b & 0x1F
            length = r2 + 2
            dist = (b >> 5) | (r3 << 3)
            if r2 == 0:               # long fill run: length = dist + 0x42, fill byte follows
                length = dist + 0x42
                fill = data[p]; p += 1
                out += bytes([fill]) * length
            else:
                start = len(out) - dist
                for i in range(length):
                    out.append(out[start + i])
        elif ctl & 0x40:
            length = (ctl & 0x3F) + 3
            fill = data[p]; p += 1
            out += bytes([fill]) * length
        else:
            out += data[p:p + ctl]; p += ctl
    return bytes(out[:size])


def unfilter16(data: bytes) -> bytes:
    """Post-filter selected by header flag bit 3 (ROM 0x1EE00): running sum over little-endian halfwords."""
    out = bytearray(data)
    n = len(out) // 2
    acc = 0
    for i in range(n):
        acc = (acc + (out[2 * i] | (out[2 * i + 1] << 8))) & 0xFFFF
        out[2 * i] = acc & 0xFF
        out[2 * i + 1] = acc >> 8
    return bytes(out)


def decode(data: bytes, offset: int) -> tuple[bytes, str]:
    """Decode any resource by its header type (applies the flag-8 post-filter). Returns (bytes, format name)."""
    out, kind = _decode_raw(data, offset)
    if data[offset] & 0x80:  # bit 3 of the type nibble
        out, kind = unfilter16(out), kind + "+diff16"
    return out, kind


def _decode_raw(data: bytes, offset: int) -> tuple[bytes, str]:
    sys.path.insert(0, "/home/user/claude-reverse-engineer-GBA/.claude/skills/gba-reverse-engineering/scripts")
    import gba_compress as gc
    t = data[offset] >> 4
    kind = t & 7
    size = struct.unpack_from("<I", data, offset)[0] >> 8
    if kind == 0:
        return data[offset + 4:offset + 4 + size], "raw"
    if kind in (1, 2, 3):
        # the game masks the filter bit (0x90 = LZ77 + Diff16) before handing the header to the BIOS decoders
        buf = bytes([kind << 4]) + bytes(data[offset + 1:offset + 4 + size * 2 + 256])
        if kind == 1:
            return gc.lz77_decompress(buf, 0), "lz77"
        if kind == 2:
            return gc.huffman_decompress(buf, 0), "huffman"
        return gc.rle_decompress(buf, 0), "rle"
    if kind == 4:
        return decode_type4(data, offset), "type4"
    if kind == 6:
        return decode_type6(data, offset), "type6"
    raise ValueError("unsupported type %d" % kind)


# ----------------------------------------------------------------------------- encoder
class BitWriter:
    """MSB-first bits packed into little-endian 32-bit words (the layout BitReader consumes)."""

    def __init__(self):
        self.words, self.cur, self.n = [], 0, 0

    def bit(self, b: int):
        self.cur = (self.cur << 1) | (b & 1)
        self.n += 1
        if self.n == 32:
            self.words.append(self.cur)
            self.cur, self.n = 0, 0

    def bits(self, v: int, n: int):
        for i in range(n - 1, -1, -1):
            self.bit((v >> i) & 1)

    def code(self, v: int):
        """Inverse of BitReader.code: n ones, a zero (omitted when n == 7), then the n low bits of v (1..255)."""
        assert 1 <= v <= 255, v
        n = v.bit_length() - 1
        for _ in range(n):
            self.bit(1)
        if n < 7:
            self.bit(0)
        self.bits(v & ((1 << n) - 1), n)

    def finish(self) -> bytes:
        if self.n:
            self.words.append(self.cur << (32 - self.n))
        self.words.append(0)  # one spare word: the decoder may fetch a word while consuming the end code
        return b"".join(struct.pack("<I", w) for w in self.words)


def filter16(data: bytes) -> bytes:
    """Inverse of unfilter16: halfword differences, so the decoder's running sum rebuilds `data` (even length)."""
    out = bytearray(data)
    prev = 0
    for i in range(0, len(out) - 1, 2):
        v = out[i] | (out[i + 1] << 8)
        d = (v - prev) & 0xFFFF
        prev = v
        out[i], out[i + 1] = d & 0xFF, d >> 8
    return bytes(out)


def _cost_code(v: int) -> int:
    k = v.bit_length() - 1
    return 2 * k + (1 if k < 7 else 0)


def _encode_type6_stream(raw: bytes, n_c: int, esc: int, table: bytes, max_fill: int = 65280) -> bytes:
    """Optimal parse (dynamic programming over token costs) followed by emission."""
    n_a = 8 - n_c
    n = len(raw)
    tbl_index = {b: i + 1 for i, b in enumerate(table)}
    MATCH_LENS = (3, 4, 8, 16, 32, 64, 128, 256)
    FILL_LENS = (2, 3, 4, 8, 16, 32, 64, 128)

    def lit_cost(v):
        return 8 if n_c and (v >> n_a) != esc else n_c + 11   # n_c == 0: every token goes through the escape path

    def fill_cost(r, f):
        # Only dictionary fills: EA's own data never uses the 3-extra-bit literal fill form and the game's decoder
        # rejects streams that do (checked with the emulator oracle), so that branch is never emitted.
        if f not in tbl_index:
            return 1 << 40
        c = n_c + 3 + (_cost_code(r - 1) if r <= 128 else 15 + 1 + _cost_code(((r - 1) >> 8) + 1))
        return c + _cost_code(tbl_index[f])

    # forward pass: run lengths, short-match distances, long-match candidates
    runlen = [1] * (n + 1)
    for i in range(n - 2, -1, -1):
        runlen[i] = runlen[i + 1] + 1 if raw[i] == raw[i + 1] else 1
    short = [0] * n
    pairs: dict = {}
    heads: dict = {}
    cands = [()] * n
    for i in range(n):
        if i + 2 <= n:
            k2 = raw[i:i + 2]
            j = pairs.get(k2)
            if j is not None and i - j <= 256:
                short[i] = i - j
            pairs[k2] = i
        if i + 3 <= n:
            k3 = raw[i:i + 3]
            lst = heads.setdefault(k3, [])
            cands[i] = tuple(lst[-12:])
            lst.append(i)
    # backward DP
    INF = 1 << 60
    cost = [INF] * (n + 1)
    choice = [None] * (n + 1)
    cost[n] = 0
    for i in range(n - 1, -1, -1):
        b = raw[i]
        best, ch = lit_cost(b) + cost[i + 1], ("lit", 1, 0)
        r = min(runlen[i], max_fill)
        if r >= 2:
            for L in FILL_LENS + (r,):
                if 2 <= L <= r:
                    c = fill_cost(L, b) + cost[i + L]
                    if c < best:
                        best, ch = c, ("fill", L, 0)
        if short[i]:
            c = n_c + 10 + cost[i + 2]
            if c < best:
                best, ch = c, ("short", 2, short[i])
        if cands[i]:
            lim = min(256, n - i)
            for j in reversed(cands[i]):
                dist = i - j
                if dist > 254 * 256:
                    continue
                L = 3
                while L < lim and raw[j + L] == raw[i + L]:
                    L += 1
                dc = n_c + _cost_code(((dist - 1) >> 8) + 1) + 8
                for LL in MATCH_LENS + (L,):
                    if 3 <= LL <= L:
                        c = dc + _cost_code(LL - 1) + cost[i + LL]
                        if c < best:
                            best, ch = c, ("match", LL, dist)
        cost[i], choice[i] = best, ch
    # emit
    bw = BitWriter()
    i = 0
    while i < n:
        kind, L, dist = choice[i]
        if kind == "lit":
            v = raw[i]
            if n_c and (v >> n_a) != esc:
                bw.bits(v, 8)
            else:
                bw.bits(esc, n_c); bw.bit(0); bw.bit(1); bw.bit(0); bw.bits(esc, n_c); bw.bits(v & ((1 << n_a) - 1), n_a)
        elif kind == "fill":
            f = raw[i]
            bw.bits(esc, n_c); bw.bit(0); bw.bit(1); bw.bit(1)
            if L <= 128:
                bw.code(L - 1)
            else:
                cnt, hi = (L - 1) & 0xFF, (L - 1) >> 8
                bw.code(0x80 | (cnt >> 1)); bw.bit(cnt & 1); bw.code(hi + 1)
            bw.code(tbl_index[f])
        elif kind == "match":
            d = dist - 1
            bw.bits(esc, n_c); bw.code(L - 1); bw.code((d >> 8) + 1); bw.bits(d & 0xFF, 8)
        else:
            bw.bits(esc, n_c); bw.bit(0); bw.bit(0); bw.bits(dist - 1, 8)
        i += L
    bw.bits(esc, n_c); bw.code(2); bw.code(0xFF)
    return bw.finish()


def encode_type6(raw: bytes, filtered: bool = False, n_c_choices=(0, 1, 2, 3), max_fill: int = 65280) -> bytes:
    """Produce a complete type-6 resource (header, parameter word, dictionary, bitstream) that decode() turns back
    into `raw`. filtered=True stores halfword differences and sets the Diff16 flag (header 0xE0), which is what the
    game uses for maps and metatiles. Tries a few escape configurations and keeps the smallest."""
    if filtered:
        assert len(raw) % 2 == 0, "Diff16 needs an even length"
    payload = filter16(raw) if filtered else bytes(raw)
    # dictionary: the most frequent run bytes (up to 31)
    runs: dict = {}
    i = 0
    while i < len(payload):
        j = i
        while j < len(payload) and payload[j] == payload[i]:
            j += 1
        if j - i >= 2:
            runs[payload[i]] = runs.get(payload[i], 0) + 1
        i = j
    table = bytes(b for b, cnt in sorted(runs.items(), key=lambda kv: -kv[1])[:28] if cnt >= 2)
    # The IWRAM decoder fetches the bitstream with word loads, so the stream must start 4-byte aligned: pad the
    # dictionary to a multiple of 4 (EA's tables are 4, 8, 12, ... bytes for the same reason) and place blobs aligned.
    while len(table) % 4:
        table += bytes([table[0] if table else 0])
    best = None
    for n_c in n_c_choices:
        hist = [0] * (1 << n_c)
        for b in payload:
            hist[b >> (8 - n_c)] += 1
        esc = min(range(1 << n_c), key=lambda v: hist[v]) if n_c else 0
        stream = _encode_type6_stream(payload, n_c, esc, table, max_fill)
        if best is None or len(stream) < len(best[0]):
            best = (stream, n_c, esc)
    stream, n_c, esc = best
    hdr = struct.pack("<I", (0x60 | (0x80 if filtered else 0)) | (len(raw) << 8))
    param = struct.pack("<I", len(table) | (esc << 8) | (0 << 16) | (n_c << 24))
    return hdr + param + table + stream


if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    off = int(sys.argv[2], 0)
    if off >= 0x08000000:
        off -= 0x08000000
    out, kind = decode(rom, off)
    dest = sys.argv[3] if len(sys.argv) > 3 else "res_%X.bin" % off
    open(dest, "wb").write(out)
    print("%s: %d bytes -> %s" % (kind, len(out), dest))
