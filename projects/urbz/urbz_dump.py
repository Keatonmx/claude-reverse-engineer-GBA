#!/usr/bin/env python3
"""Enumerate and export resources from The Urbz (GBA, USA/Europe multi-language dump).

    urbz_dump.py list  rom.gba                     # print every resource record (table offset, count, blob pointers, formats, sizes)
    urbz_dump.py png   rom.gba out_dir [--limit N] # decode slot-0 graphics (type 6) and write greyscale 4bpp sheets
    urbz_dump.py raw   rom.gba out_dir             # write every decoded blob as .bin

Resource records live in two table regions (0x7A000-0x7F000 and 0x9A000-0xB5000) as 16-byte entries
{count, ptr0, ptr1, ptr2/0} (some entries are rotated by one word). Slot 0 is normally the graphics blob
(type 6, 4bpp tiles); slot 1 is a frame/OAM descriptor (not compressed even though byte 0 is 0x10/0x20).
"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".claude", "skills", "gba-reverse-engineering", "scripts"))
import urbz_codec as uc
import render_tiles as rt

ROM_BASE = 0x08000000
TABLE_REGIONS = ((0x7A000, 0x7F000), (0x9A000, 0xB5000))


def records(rom: bytes):
    n = len(rom)
    out = []
    for lo, hi in TABLE_REGIONS:
        off = lo
        while off + 16 <= hi:
            w = struct.unpack_from("<4I", rom, off)
            if w[0] in (1, 2, 3) and ROM_BASE <= w[1] < ROM_BASE + n:
                out.append((off, w[0], [x - ROM_BASE for x in w[1:1 + w[0]] if x]))
                off += 16
            elif ROM_BASE <= w[0] < ROM_BASE + n and w[3] in (1, 2, 3):
                out.append((off, w[3], [x - ROM_BASE for x in w[0:w[3]] if x]))
                off += 16
            else:
                off += 4
    return out


def header(rom, p):
    w = struct.unpack_from("<I", rom, p)[0]
    return rom[p] >> 4, rom[p] & 0xF, w >> 8


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    rom = open(path, "rb").read()
    recs = records(rom)
    if cmd == "list":
        for off, cnt, ptrs in recs:
            desc = ["0x%X:%s" % (p, "t%X/%X/%d" % header(rom, p)) for p in ptrs]
            print("0x%06X count=%d %s" % (off, cnt, " ".join(desc)))
        print(len(recs), "records", file=sys.stderr)
        return
    out_dir = sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    gray = [(0, 0, 0)] + [(i * 16, i * 16, i * 16) for i in range(1, 16)]
    seen = set(); done = 0
    for off, cnt, ptrs in recs:
        for i, p in enumerate(ptrs):
            if p in seen:
                continue
            seen.add(p)
            t, fl, size = header(rom, p)
            if cmd == "png":
                if i != 0 or t != 6:
                    continue
                tiles = rt.decode_tiles(uc.decode_type6(rom, p), 4)
                ncol = 8; rows = (len(tiles) + ncol - 1) // ncol
                canvas = rt.blank_canvas(ncol * 8, rows * 8)
                for k, tile in enumerate(tiles):
                    rt.paint(canvas, (k % ncol) * 8, (k // ncol) * 8, tile, gray, 0, opaque=True, bpp=4)
                rt.write_png(os.path.join(out_dir, "gfx_%07X_%d.png" % (p, size)), ncol * 8, rows * 8, canvas)
            else:
                try:
                    data, kind = uc.decode(rom, p)
                except Exception:
                    continue
                open(os.path.join(out_dir, "res_%07X_slot%d_%s.bin" % (p, i, kind)), "wb").write(data)
            done += 1
            if limit and done >= limit:
                print(done, "written"); return
    print(done, "written")


if __name__ == "__main__":
    main()
