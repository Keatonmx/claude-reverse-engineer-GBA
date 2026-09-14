#!/usr/bin/env python3
"""Render district/level layers from The Urbz (GBA) in colour. Format confirmed by tracing the game's tile-cache
routine (ROM 0x4FCD8 allocator, 0x4FDA2 copy) and the map walker at 0x4FA38 in a headless mGBA (see emu/ and FINDINGS.md).

    urbz_level.py list   rom.gba                       # level records (80-byte entries at 0x73590 + k*0x50)
    urbz_level.py render rom.gba <record_offset> out.png [--layer 0|1|2|3] [--crop CW CH] [--origin CX CY] [--zoom N]
       (--layer 0 = composite of all three, default; crop/origin in 32x32 metatiles)

Level record (file offsets, 80 bytes; records found by signature scan of 0x73000-0x7A000, e.g. 0x748C8):
  +0x00/+0x04, +0x10/+0x14, +0x20/+0x24: three layers of (map, metatiles), drawn on BG2, BG1, BG0 (BG3 = HUD)
  +0x30 collision metatiles (type 6, 16 bytes each)   +0x34 collision map (type 6: u16 count, 3 words, u16 cells)
  +0x40 object list (4 zero bytes then type-6 blobs of 6-byte records)
  +0x44 tile bank: type 0 (raw) header, then 4bpp 8x8 tiles; a metatile tile reference is the tile index into it
  +0x48 background palettes: raw, 16 banks x 16 BGR555 colours (512 bytes), copied to palette RAM as-is
  (verified in the emulator: the live district's RAM maps/metatiles, VRAM tile sources and palette RAM all come from
   one record laid out this way; an earlier version of this tool paired each record's layers with the next record's bank)
  Layer details:
     map       = type 6 + Diff16: u16 width, u16 height (in 32x32 metatiles), then width*height u16 metatile ids
     metatiles = type 6 (+Diff16): u16 count, u16 0, u16 x, u16 x, then count x 32 bytes: 16 u16 tile references
                 (4x4 block, row-major, 8x8 tiles), then count x 16 attribute bytes, one per tile:
                 bit 0 hflip, bit 1 vflip, bits 2-5 palette bank (the byte is shifted left 10 into the screen entry).
     The game caches tiles into VRAM on demand (cache table indexed by ref & 0x7FF, full ref compared), which is why
     VRAM never holds the whole bank.
"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".claude", "skills", "gba-reverse-engineering", "scripts"))
import urbz_codec as uc
import render_tiles as rt

ROM_BASE = 0x08000000
FIRST_RECORD = 0x73568
STRIDE = 0x50
F_LAYERS = (0x00, 0x10, 0x20)
F_COLL_META, F_COLL_MAP, F_OBJECTS, F_BANK, F_PALETTE = 0x30, 0x34, 0x40, 0x44, 0x48


def ptr(rom, off):
    w = struct.unpack_from("<I", rom, off)[0]
    return w - ROM_BASE if ROM_BASE <= w < ROM_BASE + len(rom) else None


def records(rom):
    """The district table: 80-byte records on a fixed 0x50 stride from 0x73568 (the game computes
    0x08073568 + index*0x50 at 0x08031BC0). Entries are valid while the layer-1 pointer and a raw tile bank exist."""
    out = []
    i = 0
    while True:
        off = FIRST_RECORD + i * STRIDE
        m, bank = ptr(rom, off), ptr(rom, off + F_BANK)
        if m is None or bank is None or (rom[bank] >> 4) != 0 or ptr(rom, off + 4) is None:
            break
        out.append(off)
        i += 1
    return out


def load_level(rom, rec):
    bank = ptr(rom, rec + F_BANK)
    size = struct.unpack_from("<I", rom, bank)[0] >> 8
    tiles = rt.decode_tiles(rom[bank + 4:bank + 4 + size], 4)
    pal_off = ptr(rom, rec + F_PALETTE)
    palette = rt.decode_palette(rom[pal_off:pal_off + 512]) if pal_off else [(0, 0, 0)] * 256
    layers = []
    for base in F_LAYERS:
        m, t = ptr(rom, rec + base), ptr(rom, rec + base + 4)
        if m is None or t is None:
            layers.append(None); continue
        tm, _ = uc.decode(rom, m); mt, _ = uc.decode(rom, t)
        w, h = struct.unpack_from("<HH", tm, 0)
        cells = struct.unpack_from("<%dH" % (w * h), tm, 4)
        n = struct.unpack_from("<H", mt, 0)[0]
        refs = [struct.unpack_from("<16H", mt, 4 + i * 32) for i in range(n)]
        attrs = [mt[4 + n * 32 + i * 16:4 + n * 32 + (i + 1) * 16] for i in range(n)]
        layers.append((w, h, cells, refs, attrs))
    return tiles, palette, layers


def draw_layer(canvas, tiles, palette, layer, ox, oy, cw, ch, opaque):
    w, h, cells, refs, attrs = layer
    for my in range(min(ch, h - oy)):
        for mx in range(min(cw, w - ox)):
            cid = cells[(my + oy) * w + mx + ox]
            if cid >= len(refs):
                continue
            for k in range(16):
                t = refs[cid][k]; a = attrs[cid][k]
                if (t == 0 and not opaque) or t >= len(tiles):
                    continue
                hf, vf, bank = bool(a & 1), bool(a & 2), (a >> 2) & 0xF
                x0, y0 = mx * 32 + (k % 4) * 8, my * 32 + (k // 4) * 8
                tile = tiles[t]
                for i, idx in enumerate(tile):
                    if idx == 0 and not opaque:
                        continue
                    x, y = i % 8, i // 8
                    if hf: x = 7 - x
                    if vf: y = 7 - y
                    canvas[y0 + y][(x0 + x) * 3:(x0 + x) * 3 + 3] = bytes(palette[bank * 16 + idx] if idx else palette[0])


def render(rom, rec, layer=0, crop=None, zoom=1, origin=(0, 0)):
    tiles, palette, layers = load_level(rom, rec)
    chosen = [(i, layers[i]) for i in ([layer - 1] if layer else (0, 1, 2)) if layers[i]]
    w, h = chosen[0][1][0], chosen[0][1][1]
    cw, ch = crop or (w, h)
    W, H = cw * 32, ch * 32
    canvas = rt.blank_canvas(W, H)
    for n, (i, lay) in enumerate(chosen):
        draw_layer(canvas, tiles, palette, lay, origin[0], origin[1], cw, ch, n == 0)
    if zoom > 1:
        big = rt.blank_canvas(W * zoom, H * zoom)
        for y in range(H):
            for x in range(W):
                px = canvas[y][x * 3:x * 3 + 3]
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
            bank = ptr(rom, rec + F_BANK); m = ptr(rom, rec + F_LAYERS[0])
            tm, _ = uc.decode(rom, m); w, h = struct.unpack_from("<HH", tm, 0)
            print("record 0x%06X: map %2dx%2d metatiles, bank 0x%X (%d bytes), palette 0x%X" % (
                rec, w, h, bank, struct.unpack_from("<I", rom, bank)[0] >> 8, ptr(rom, rec + F_PALETTE) or 0))
        return
    rec = int(sys.argv[3], 0); out = sys.argv[4]
    layer = int(sys.argv[sys.argv.index("--layer") + 1]) if "--layer" in sys.argv else 0
    crop = None
    if "--crop" in sys.argv:
        i = sys.argv.index("--crop"); crop = (int(sys.argv[i + 1]), int(sys.argv[i + 2]))
    origin = (0, 0)
    if "--origin" in sys.argv:
        i = sys.argv.index("--origin"); origin = (int(sys.argv[i + 1]), int(sys.argv[i + 2]))
    zoom = int(sys.argv[sys.argv.index("--zoom") + 1]) if "--zoom" in sys.argv else 1
    canvas, W, H = render(rom, rec, layer, crop, zoom, origin)
    rt.write_png(out, W, H, canvas)
    print("%dx%d -> %s" % (W, H, out))


if __name__ == "__main__":
    main()
