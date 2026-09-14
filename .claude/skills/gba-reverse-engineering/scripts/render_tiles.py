#!/usr/bin/env python3
"""Render GBA tile graphics to PNG so you can *see* what you extracted. Pure Python (zlib PNG writer).

Tiles are 8x8. 4bpp tiles are 32 bytes (two pixels per byte, low nibble first); 8bpp tiles are 64 bytes.
Palettes are BGR555: 16-bit little-endian, red in bits 0-4, green 5-9, blue 10-14.

Usage examples
    # tileset sheet: 8bpp tiles, 16 tiles per row
    render_tiles.py tileset tiles.bin --palette pal.bin --bpp 8 --columns 16 -o tileset.png

    # a Klonoa-style tilemap: one byte per cell indexing into the tileset (width in tiles)
    render_tiles.py tilemap map.bin --tiles tiles.bin --palette pal.bin --bpp 8 --width 256 --index-size 1 -o level.png

    # a hardware screen-entry tilemap (u16 per cell: tile index bits 0-9, hflip 10, vflip 11, palette bank 12-15)
    render_tiles.py tilemap sbb.bin --tiles charblock.bin --palette bgpal.bin --bpp 4 --width 32 --index-size 2 -o screen.png

    # raw palette swatch
    render_tiles.py palette pal.bin -o pal.png

Colour index 0 is treated as transparent (rendered as magenta) unless --opaque is given.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib


def write_png(path: str, width: int, height: int, rgb_rows):
    raw = bytearray()
    for row in rgb_rows:
        raw.append(0)
        raw += row

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


def decode_palette(data: bytes):
    cols = []
    for i in range(0, len(data) - 1, 2):
        v = data[i] | (data[i + 1] << 8)
        cols.append(((v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3))
    return cols


def decode_tiles(data: bytes, bpp: int):
    size = 32 if bpp == 4 else 64
    tiles = []
    for t in range(0, len(data) - size + 1, size):
        blob = data[t:t + size]
        if bpp == 4:
            px = []
            for b in blob:
                px.append(b & 0xF)
                px.append(b >> 4)
        else:
            px = list(blob)
        tiles.append(px)
    return tiles


def paint(canvas, x0, y0, tile, palette, pal_bank=0, hflip=False, vflip=False, opaque=False, bpp=8):
    for i, idx in enumerate(tile):
        x, y = i % 8, i // 8
        if hflip:
            x = 7 - x
        if vflip:
            y = 7 - y
        if idx == 0 and not opaque:
            rgb = (255, 0, 255)
        else:
            ci = (pal_bank * 16 + idx) if bpp == 4 else idx
            rgb = palette[ci] if ci < len(palette) else (0, 0, 0)
        row = canvas[y0 + y]
        p = (x0 + x) * 3
        row[p:p + 3] = bytes(rgb)


def blank_canvas(w, h):
    return [bytearray(b"\x20" * (w * 3)) for _ in range(h)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tileset"); p.add_argument("tiles"); p.add_argument("--palette", required=True); p.add_argument("--bpp", type=int, default=8)
    p.add_argument("--columns", type=int, default=16); p.add_argument("--opaque", action="store_true"); p.add_argument("-o", "--output", default="tileset.png")
    p.add_argument("--pal-bank", type=int, default=0)

    p = sub.add_parser("tilemap"); p.add_argument("map"); p.add_argument("--tiles", required=True); p.add_argument("--palette", required=True)
    p.add_argument("--bpp", type=int, default=8); p.add_argument("--width", type=int, required=True, help="map width in tiles")
    p.add_argument("--index-size", type=int, default=1, choices=(1, 2), help="1 = byte per cell, 2 = GBA screen entry (u16)")
    p.add_argument("--height", type=int, default=0); p.add_argument("--skip", default="0", help="bytes to skip before the map (e.g. a 4-byte header)")
    p.add_argument("--opaque", action="store_true"); p.add_argument("-o", "--output", default="tilemap.png")

    p = sub.add_parser("palette"); p.add_argument("palette"); p.add_argument("-o", "--output", default="palette.png")

    a = ap.parse_args(argv)

    if a.cmd == "palette":
        pal = decode_palette(open(a.palette, "rb").read())
        cols = 16
        rows = (len(pal) + cols - 1) // cols
        canvas = blank_canvas(cols * 8, rows * 8)
        for i, rgb in enumerate(pal):
            x0, y0 = (i % cols) * 8, (i // cols) * 8
            for y in range(8):
                for x in range(8):
                    canvas[y0 + y][(x0 + x) * 3:(x0 + x) * 3 + 3] = bytes(rgb)
        write_png(a.output, cols * 8, rows * 8, canvas)
        print("%d colours -> %s" % (len(pal), a.output))
        return 0

    palette = decode_palette(open(a.palette, "rb").read())
    tiles = decode_tiles(open(a.tiles, "rb").read(), a.bpp)

    if a.cmd == "tileset":
        rows = (len(tiles) + a.columns - 1) // a.columns
        canvas = blank_canvas(a.columns * 8, rows * 8)
        for i, t in enumerate(tiles):
            paint(canvas, (i % a.columns) * 8, (i // a.columns) * 8, t, palette, a.pal_bank, opaque=a.opaque, bpp=a.bpp)
        write_png(a.output, a.columns * 8, rows * 8, canvas)
        print("%d tiles -> %s" % (len(tiles), a.output))
        return 0

    data = open(a.map, "rb").read()[int(a.skip, 0):]
    cells = len(data) // a.index_size
    height = a.height or (cells + a.width - 1) // a.width
    canvas = blank_canvas(a.width * 8, height * 8)
    missing = 0
    for i in range(min(cells, a.width * height)):
        if a.index_size == 1:
            idx, hf, vf, bank = data[i], False, False, 0
        else:
            e = data[2 * i] | (data[2 * i + 1] << 8)
            idx, hf, vf, bank = e & 0x3FF, bool(e & 0x400), bool(e & 0x800), e >> 12
        if idx >= len(tiles):
            missing += 1
            continue
        paint(canvas, (i % a.width) * 8, (i // a.width) * 8, tiles[idx], palette, bank, hf, vf, a.opaque, a.bpp)
    write_png(a.output, a.width * 8, height * 8, canvas)
    print("%dx%d tiles -> %s%s" % (a.width, height, a.output, "  (%d cells referenced missing tiles)" % missing if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
