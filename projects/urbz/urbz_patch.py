#!/usr/bin/env python3
"""Write edited district data back into an Urbz ROM.

Edited blobs are re-encoded with the smallest format the loader accepts (our type-6 encoder, verified byte-exact
against the game's own decoder, or LZ77 with or without the Diff16 flag) and placed best-fit in the free space: the
ROM's zero tail plus the bytes of the blobs the redirected pointers abandon, so most edits land in the original slot.

    python3 urbz_patch.py demo rom.gba out.gba [record] [--ups out.ups]
        The proof of concept: a 2x5-metatile wall to the right of the start position in the first district
        (visual ground layer + collision), see FINDINGS.md section 8.
    python3 urbz_patch.py replace rom.gba out.gba <record> <field> <raw.bin> [--ups out.ups]
        Replace one blob of a district record with raw (decoded) data from a file. Fields: map0 meta0 map1 meta1 map2
        meta2 collmeta collmap objects.
    python3 urbz_patch.py ups-apply rom.gba patch.ups out.gba

Output ROMs are for your own testing; ship the .ups.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "gba-reverse-engineering", "scripts"))
import urbz_codec as uc  # noqa: E402
from gba_compress import lz77_compress, lz77_decompress_ex, rle_decompress_ex  # noqa: E402
from gba_patchfile import ups_make, ups_apply  # noqa: E402

FIELDS = {"map0": 0x00, "meta0": 0x04, "map1": 0x10, "meta1": 0x14, "map2": 0x20, "meta2": 0x24,
          "collmeta": 0x30, "collmap": 0x34, "objects": 0x40}
DEFAULT_RECORD = 0x748C8  # first district (verified pixel-exact in the emulator)


def decoded(rom, off):
    d = uc.decode(rom, off)
    return bytes(d[0] if isinstance(d, tuple) else d)


def blob_extent(rom, off):
    """Bytes a resource occupies in the ROM (header to end of stream, rounded to 4)."""
    t = (rom[off] >> 4) & 7
    size = struct.unpack_from("<I", rom, off)[0] >> 8
    if t == 0:
        end = off + 4 + size
    elif t == 6:
        end = uc.type6_end(rom, off)
    elif t in (1, 3):
        buf = bytes([t << 4]) + bytes(rom[off + 1:off + 4 + size * 2 + 256])
        end = off + (lz77_decompress_ex(buf, 0)[1] if t == 1 else rle_decompress_ex(buf, 0)[1])
    else:
        raise ValueError("unknown blob type %d" % t)
    return ((end + 3) & ~3) - off


def encode_blob(raw, allow_filter=True):
    """Smallest resource blob the loader accepts for `raw`: our type 6 (Diff16-filtered when even), LZ77 + Diff16
    (header 0x90, which the game itself ships), or plain LZ77. Every candidate is decoded back before it may win."""
    cands = []
    even = len(raw) % 2 == 0
    if allow_filter and even:
        cands.append(uc.encode_type6(raw, True))
        lz = bytearray(lz77_compress(uc.filter16(bytes(raw)), vram_safe=True))
        lz[0] = 0x90
        cands.append(bytes(lz))
    cands.append(uc.encode_type6(raw, False))
    cands.append(lz77_compress(bytes(raw), vram_safe=True))
    ok = [c for c in cands if uc.decode(c, 0)[0] == bytes(raw)]
    assert ok, "no encoder produced a valid blob"
    return min(ok, key=len)


class Patcher:
    """Redirects district record pointers to re-encoded blobs. Free space = the ROM's zero tail plus the bytes of
    every blob a redirected pointer abandons (district blobs are referenced exactly once). Best-fit, 4-byte aligned."""

    def __init__(self, rom):
        self.orig = bytes(rom)
        self.rom = bytearray(rom)
        last = len(rom) - 1
        while last > 0 and rom[last] == 0:
            last -= 1
        self.tail_start = (last + 1 + 0x10 + 3) & ~3
        self.regions = [[self.tail_start, len(rom)]]
        self.log = []

    def free(self, start, length):
        start = (start + 3) & ~3
        if length < 8:
            return
        self.regions.append([start, start + length])
        self.regions.sort()
        merged = []
        for r in self.regions:
            if merged and r[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], r[1])
            else:
                merged.append(list(r))
        self.regions = merged

    def place(self, blob):
        fit = [r for r in self.regions if r[1] - r[0] >= len(blob)]
        if not fit:
            raise SystemExit("out of free space: need %d bytes" % len(blob))
        r = min(fit, key=lambda r: r[1] - r[0])
        addr = r[0]
        self.rom[addr:addr + len(blob)] = blob
        r[0] = (addr + len(blob) + 3) & ~3
        if r[1] - r[0] < 8:
            self.regions.remove(r)
        return addr

    def set_ptr(self, off, file_addr):
        struct.pack_into("<I", self.rom, off, 0x08000000 + file_addr)

    def replace(self, rec, field, raw):
        off = rec + FIELDS[field]
        old = struct.unpack_from("<I", self.rom, off)[0]
        try:
            self.free(old - 0x08000000, blob_extent(self.orig, old - 0x08000000))
        except Exception:
            pass  # unknown extent: leave the old bytes alone
        blob = encode_blob(raw, allow_filter=field != "objects")
        addr = self.place(blob)
        self.set_ptr(off, addr)
        self.log.append((rec, field, old, addr, len(raw), len(blob), addr < self.tail_start))
        return addr

    def tail_used(self):
        tail = [r for r in self.regions if r[1] == len(self.rom)]
        return (tail[0][0] if tail else len(self.rom)) - self.tail_start

    def ups(self):
        """UPS patch (IPS offsets are 24-bit and cannot reach the free space past 16 MB)."""
        return ups_make(self.orig, bytes(self.rom))


def demo(rom, rec, x0=8, y0=7, w=2, h=5, wall_meta=0, wall_coll=0):
    """Drop a w x h metatile block at (x0, y0): visual metatile `wall_meta` on the ground layer and collision metatile
    `wall_coll` (id 0 is all 0x03, the value used outside the walkable area)."""
    pt = Patcher(rom)
    m = bytearray(decoded(rom, struct.unpack_from("<I", rom, rec + FIELDS["map0"])[0] - 0x08000000))
    c = bytearray(decoded(rom, struct.unpack_from("<I", rom, rec + FIELDS["collmap"])[0] - 0x08000000))
    mw, mh = struct.unpack_from("<HH", m, 0)
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            struct.pack_into("<H", m, 4 + (y * mw + x) * 2, wall_meta)
            struct.pack_into("<H", c, 4 + (y * mw + x) * 2, wall_coll)  # collision map: u16 count, u16 0, cells
    pt.replace(rec, "map0", m)
    pt.replace(rec, "collmap", c)
    return pt


def main():
    a = sys.argv[1:]
    ups_out = None
    if "--ups" in a:
        i = a.index("--ups")
        ups_out = a[i + 1]
        del a[i:i + 2]
    if not a:
        print(__doc__)
        return
    cmd = a[0]
    if cmd == "ups-apply":
        rom, patch = open(a[1], "rb").read(), open(a[2], "rb").read()
        open(a[3], "wb").write(ups_apply(rom, patch))
        print("written", a[3])
        return
    rom = open(a[1], "rb").read()
    if cmd == "demo":
        rec = int(a[3], 0) if len(a) > 3 else DEFAULT_RECORD
        pt = demo(rom, rec)
    elif cmd == "replace":
        rec, field, raw = int(a[3], 0), a[4], open(a[5], "rb").read()
        pt = Patcher(rom)
        pt.replace(rec, field, raw)
    else:
        raise SystemExit("unknown command " + cmd)
    open(a[2], "wb").write(pt.rom)
    for rec, field, old, addr, n, nb, inplace in pt.log:
        print("record 0x%X %-8s 0x%08X -> 0x%08X (%d bytes raw, %d bytes encoded, type 0x%02X, %s)"
              % (rec, field, old, 0x08000000 + addr, n, nb, pt.rom[addr], "reclaimed slot" if inplace else "tail"))
    print("written %s; %d bytes of the tail used" % (a[2], pt.tail_used()))
    if ups_out:
        p = pt.ups()
        open(ups_out, "wb").write(p)
        assert ups_apply(rom, p) == bytes(pt.rom)
        print("written", ups_out, "(%d bytes, verified)" % len(p))


if __name__ == "__main__":
    main()
