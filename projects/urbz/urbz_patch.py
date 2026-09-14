#!/usr/bin/env python3
"""Write edited district data back into an Urbz ROM.

The loader at 0x0801EC00 dispatches on the header type nibble, and type 1 is BIOS LZ77 with the resource header itself
passed to swi 0x11 (byte 0 = 0x10, bytes 1-3 = size: the BIOS header). So any blob the game stores as its custom type 6
can be replaced by a plain LZ77 blob without writing a type-6 encoder: decode, edit, `lz77_compress`, drop the result in
free space, point the record at it. The Diff16 filter flag (bit 7) must be clear on a type-1 blob (0x90 is not a valid
BIOS header), so the LZ77 payload is the *unfiltered* data.

    python3 urbz_patch.py demo rom.gba out.gba [record] [--ups out.ups]
        The proof of concept: a 2x5-metatile wall to the right of the start position in the first district
        (visual ground layer + collision), see FINDINGS.md section 8.
    python3 urbz_patch.py replace rom.gba out.gba <record> <field> <raw.bin> [--ups out.ups]
        Replace one blob of a district record with raw (decoded) data from a file. Fields: map0 meta0 map1 meta1 map2
        meta2 collmeta collmap objects.
    python3 urbz_patch.py ups-apply rom.gba patch.ups out.gba

Free space is the zero-filled tail of the ROM (0x1FF823D-0x1FFFFFF on the BOCE dump); blobs are packed there 4-byte
aligned. Output ROMs are for your own testing; ship the .ups.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "gba-reverse-engineering", "scripts"))
import urbz_codec as uc  # noqa: E402
from gba_compress import lz77_compress, lz77_decompress  # noqa: E402
from gba_patchfile import ups_make, ups_apply  # noqa: E402

FIELDS = {"map0": 0x00, "meta0": 0x04, "map1": 0x10, "meta1": 0x14, "map2": 0x20, "meta2": 0x24,
          "collmeta": 0x30, "collmap": 0x34, "objects": 0x40}
DEFAULT_RECORD = 0x748C8  # first district (verified pixel-exact in the emulator)


def decoded(rom, off):
    d = uc.decode(rom, off)
    return bytes(d[0] if isinstance(d, tuple) else d)


def encode_blob(raw):
    """Resource blob the loader accepts as type 1: BIOS LZ77 header + stream. Round-trip checked."""
    blob = lz77_compress(bytes(raw), vram_safe=True)
    assert blob[0] == 0x10 and lz77_decompress(blob) == bytes(raw)
    return blob


class Patcher:
    def __init__(self, rom):
        self.orig = bytes(rom)
        self.rom = bytearray(rom)
        end = len(rom)
        last = end - 1
        while last > 0 and rom[last] == 0:
            last -= 1
        self.cursor = (last + 1 + 0x10 + 3) & ~3  # leave a small gap after the last used byte
        self.log = []

    def place(self, blob):
        addr = self.cursor
        if addr + len(blob) > len(self.rom):
            raise SystemExit("out of free space: need %d bytes at 0x%X" % (len(blob), addr))
        self.rom[addr:addr + len(blob)] = blob
        self.cursor = (addr + len(blob) + 3) & ~3
        return addr

    def set_ptr(self, off, file_addr):
        struct.pack_into("<I", self.rom, off, 0x08000000 + file_addr)

    def replace(self, rec, field, raw):
        off = rec + FIELDS[field]
        old = struct.unpack_from("<I", self.rom, off)[0]
        blob = encode_blob(raw)
        addr = self.place(blob)
        self.set_ptr(off, addr)
        self.log.append((rec, field, old, addr, len(raw), len(blob)))
        return addr

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
    for rec, field, old, addr, n, nb in pt.log:
        print("record 0x%X %-8s 0x%08X -> 0x%08X (%d bytes raw, %d bytes LZ77)" % (rec, field, old, 0x08000000 + addr, n, nb))
    print("written", a[2])
    if ups_out:
        p = pt.ups()
        open(ups_out, "wb").write(p)
        assert ups_apply(rom, p) == bytes(pt.rom)
        print("written", ups_out, "(%d bytes, verified)" % len(p))


if __name__ == "__main__":
    main()
