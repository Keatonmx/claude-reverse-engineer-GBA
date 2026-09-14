#!/usr/bin/env python3
"""Render district/level layers from The Urbz (GBA) using the record format documented in FINDINGS.md.

    urbz_level.py list   rom.gba                       # level records (80-byte entries at 0x73598 + k*0x50)
    urbz_level.py render rom.gba <record_offset> out.png [--layer 1|2|3] [--crop CW CH] [--origin CX CY] [--zoom N] [--grid]

Level record (file offsets, 80 bytes):
  +0x08 collision metatiles (type 6, 16 bytes each)   +0x0C collision map (type 6: u16 count, 3 words, u16 cells)
  +0x1C raw tile pixel bank (type 0, 4bpp 8x8 tiles)   +0x20 tile cache table (0xFFFF-filled)
  +0x28/+0x2C, +0x38/+0x3C, +0x48/+0x4C: three layers of (map, metatiles):
     map       = type 6 + Diff16: u16 width, u16 height, then width*height u16 metatile ids
     metatiles = type 6 (+Diff16): u16 count, u16 0, then count * 24 u16 tile refs (bits 0-13 tile index into the bank,
                 bit 14/15 flips - assumed). The 24 refs form a 64x32 isometric diamond: 4 top, 8, 8, 4 bottom.
Palettes are not yet located, so output is greyscale by colour index.
"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".claude", "skills", "gba-reverse-engineering", "scripts"))
import urbz_codec as uc
import render_tiles as rt

ROM_BASE = 0x08000000
FIRST_RECORD = 0x73598 - 8
STRIDE = 0x50
GRAY = [(i * 16, i * 16, i * 16) for i in range(16)]
POS = {}
for k in range(4):
    POS[k] = (k, 1); POS[16 + k] = (4 + k, 1); POS[4 + k] = (k, 2); POS[20 + k] = (4 + k, 2)
    POS[8 + k] = (2 + k, 0); POS[12 + k] = (2 + k, 3)


def ptr(rom, off):
    w = struct.unpack_from("<I", rom, off)[0]
    return w - ROM_BASE if ROM_BASE <= w < ROM_BASE + len(rom) else None


def records(rom):
    out = []
    off = FIRST_RECORD
    while off + STRIDE <= 0x7A000:
        a, b = ptr(rom, off + 8), ptr(rom, off + 0xC)
        if a is not None and b is not None and rom[a] >> 4 == 6 and rom[b] >> 4 == 6:
            out.append(off)
        off += STRIDE
    return out


def load_level(rom, rec):
    bank_off = ptr(rom, rec + 0x1C)
    bank_size = struct.unpack_from("<I", rom, bank_off)[0] >> 8
    tiles = rt.decode_tiles(rom[bank_off + 4:bank_off + 4 + bank_size], 4)
    layers = []
    for base in (0x28, 0x38, 0x48):
        m, t = ptr(rom, rec + base), ptr(rom, rec + base + 4)
        if m is None or t is None:
            layers.append(None); continue
        tm, _ = uc.decode(rom, m); mt, _ = uc.decode(rom, t)
        w, h = struct.unpack_from("<HH", tm, 0)
        cells = struct.unpack_from("<%dH" % (w * h), tm, 4)
        n = struct.unpack_from("<H", mt, 0)[0]
        metas = [struct.unpack_from("<24H", mt, 4 + i * 48) for i in range(n)]
        layers.append((w, h, cells, metas))
    return tiles, layers


def render(rom, rec, layer=1, crop=None, zoom=1, grid=False, origin=(0, 0)):
    tiles, layers = load_level(rom, rec)
    w, h, cells, metas = layers[layer - 1]
    cw, ch = crop or (w, h)
    if grid:
        W, H = cw * 64, ch * 32
    else:
        W, H = cw * 64 + 32, ch * 16 + 32
    canvas = rt.blank_canvas(W, H)
    ox, oy = origin
    for cy in range(min(ch, h - oy)):
        for cx in range(min(cw, w - ox)):
            m = metas[cells[(cy + oy) * w + cx + ox]]
            px, py = (cx * 64, cy * 32) if grid else (cx * 64 + (32 if cy & 1 else 0), cy * 16)
            for k, e in enumerate(m):
                t = e & 0x3FFF
                if t == 0 or t >= len(tiles):
                    continue
                tx, ty = POS[k]
                rt.paint(canvas, px + tx * 8, py + ty * 8, tiles[t], GRAY, 0, bool(e & 0x4000), bool(e & 0x8000), False, 4)
    if zoom > 1:
        big = rt.blank_canvas(W * zoom, H * zoom)
        for y in range(H):
            row = canvas[y]
            for x in range(W):
                px = row[x * 3:x * 3 + 3]
                for dy in range(zoom):
                    r = big[y * zoom + dy]
                    for dx in range(zoom):
                        r[(x * zoom + dx) * 3:(x * zoom + dx) * 3 + 3] = px
        canvas, W, H = big, W * zoom, H * zoom
    return canvas, W, H


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    rom = open(path, "rb").read()
    if cmd == "list":
        for rec in records(rom):
            m, t = ptr(rom, rec + 0x28), ptr(rom, rec + 0x2C)
            bank = ptr(rom, rec + 0x1C)
            print("record 0x%06X: collision 0x%X/0x%X, bank 0x%X (%d bytes), layer1 map 0x%X metatiles 0x%X" % (
                rec, ptr(rom, rec + 8), ptr(rom, rec + 0xC), bank, struct.unpack_from("<I", rom, bank)[0] >> 8, m, t))
        return
    rec = int(sys.argv[3], 0); out = sys.argv[4]
    layer = int(sys.argv[sys.argv.index("--layer") + 1]) if "--layer" in sys.argv else 1
    crop = None
    if "--crop" in sys.argv:
        i = sys.argv.index("--crop"); crop = (int(sys.argv[i + 1]), int(sys.argv[i + 2]))
    zoom = int(sys.argv[sys.argv.index("--zoom") + 1]) if "--zoom" in sys.argv else 1
    origin = (0, 0)
    if "--origin" in sys.argv:
        i = sys.argv.index("--origin"); origin = (int(sys.argv[i + 1]), int(sys.argv[i + 2]))
    canvas, W, H = render(rom, rec, layer, crop, zoom, "--grid" in sys.argv, origin)
    rt.write_png(out, W, H, canvas)
    print("%dx%d -> %s" % (W, H, out))


if __name__ == "__main__":
    main()
