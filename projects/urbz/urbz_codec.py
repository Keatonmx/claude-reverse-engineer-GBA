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


def decode_type6(data: bytes, offset: int, size: int | None = None) -> bytes:
    """Decoder B. `offset` points at the 4-byte resource header; size defaults to the header's."""
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
    return bytes(out[:size]) if size else bytes(out)


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
    if kind == 1:
        return gc.lz77_decompress(data, offset), "lz77"
    if kind == 2:
        return gc.huffman_decompress(data, offset), "huffman"
    if kind == 3:
        return gc.rle_decompress(data, offset), "rle"
    if kind == 4:
        return decode_type4(data, offset), "type4"
    if kind == 6:
        return decode_type6(data, offset), "type6"
    raise ValueError("unsupported type %d" % kind)


if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    off = int(sys.argv[2], 0)
    if off >= 0x08000000:
        off -= 0x08000000
    out, kind = decode(rom, off)
    dest = sys.argv[3] if len(sys.argv) > 3 else "res_%X.bin" % off
    open(dest, "wb").write(out)
    print("%s: %d bytes -> %s" % (kind, len(out), dest))
