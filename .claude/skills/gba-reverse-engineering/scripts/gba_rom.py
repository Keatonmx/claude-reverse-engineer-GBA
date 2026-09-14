#!/usr/bin/env python3
"""ROM-level helpers for GBA reverse engineering: header, pointer hunting, pointer tables, free space, hex dumps.

Usage examples
    gba_rom.py header    rom.gba                       # title, game code, maker, version, checksum, size, sha1
    gba_rom.py pointers  rom.gba 0x1B27FC               # every 32-bit word equal to 0x081B27FC (who references this data?)
    gba_rom.py pointers  rom.gba 0x1B27FC --near 16     # also words within +-16 bytes of the target
    gba_rom.py table     rom.gba 0x188F60 --count 9 --stride 4        # dump a pointer table (u32 entries)
    gba_rom.py table     rom.gba 0x51C80 --count 9 --stride 6 --fields u16:width          # struct table
    gba_rom.py freespace rom.gba --min 0x1000            # runs of 0x00 / 0xFF padding (candidate patch space)
    gba_rom.py dump      rom.gba 0xE2B90 --length 0x2C --stride 44    # hexdump in fixed-size records
    gba_rom.py u16       rom.gba 0x51C80 --count 8       # read little-endian words
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys

ROM_BASE = 0x08000000


def parse_address(text: str) -> int:
    v = int(text, 0)
    if 0x08000000 <= v < 0x0E000000:
        return v - ROM_BASE
    return v


def header(rom: bytes) -> dict:
    """Parse the 192-byte cartridge header (GBATEK 'GBA Cartridge Header')."""
    entry = struct.unpack_from("<I", rom, 0)[0]
    title = rom[0xA0:0xAC].rstrip(b"\0").decode("ascii", "replace")
    code = rom[0xAC:0xB0].decode("ascii", "replace")
    maker = rom[0xB0:0xB2].decode("ascii", "replace")
    fixed = rom[0xB2]
    version = rom[0xBC]
    chk = rom[0xBD]
    calc = (-(sum(rom[0xA0:0xBD]) + 0x19)) & 0xFF
    logo_ok = rom[0x04:0xA0] == NINTENDO_LOGO if len(NINTENDO_LOGO) == 156 else None
    # entry point: ARM b instruction -> decode target
    if (entry >> 24) & 0x0F == 0x0A:
        off = entry & 0xFFFFFF
        if off & 0x800000:
            off -= 0x1000000
        entry_target = ROM_BASE + 8 + off * 4
    else:
        entry_target = None
    return {
        "title": title, "game_code": code, "maker_code": maker, "fixed_0x96": "0x%02X" % fixed,
        "version": version, "header_checksum": "0x%02X (calc 0x%02X, %s)" % (chk, calc, "ok" if chk == calc else "MISMATCH"),
        "entry_instruction": "0x%08X" % entry, "entry_target": ("0x%08X" % entry_target) if entry_target else "not a branch",
        "size": "%d bytes (%.2f MB)" % (len(rom), len(rom) / 1048576), "sha1": hashlib.sha1(rom).hexdigest(),
        "logo_present": logo_ok,
    }


NINTENDO_LOGO = bytes.fromhex(
    "24ffae51699aa2213d84820a84e409ad11248b98c0817f21a352be199309ce2010464a4af82731ec58c7e83382e3cebf85f4df94ce4b09c194568ac01372a7fc9f844d73a3ca9a615897a327fc039876231dc7610304ae56bf38840040a70efdff52fe036f9530f197fbc08560d68025a963be03014e38e2f9a234ffbb3e0344780090cb88113a9465c07c6387f03cafd625e48b380aac7221d4f807"
)


def find_pointers(rom: bytes, target_off: int, near: int = 0, align: int = 4):
    """Yield (offset, value) for words pointing at ROM_BASE+target (or within +-near bytes)."""
    lo = ROM_BASE + target_off - near
    hi = ROM_BASE + target_off + near
    for off in range(0, len(rom) - 3, align):
        v = struct.unpack_from("<I", rom, off)[0]
        if lo <= v <= hi:
            yield off, v


def free_space(rom: bytes, min_len: int = 0x100, fills=(0x00, 0xFF)):
    """Yield (offset, length, fill_byte) for runs of a single padding byte."""
    n = len(rom)
    i = 0
    while i < n:
        b = rom[i]
        if b in fills:
            j = i + 1
            while j < n and rom[j] == b:
                j += 1
            if j - i >= min_len:
                yield i, j - i, b
            i = j
        else:
            i += 1


FIELD_FMT = {"u8": "<B", "s8": "<b", "u16": "<H", "s16": "<h", "u32": "<I", "s32": "<i", "ptr": "<I"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("header"); p.add_argument("rom")

    p = sub.add_parser("pointers", help="find words referencing an offset")
    p.add_argument("rom"); p.add_argument("target"); p.add_argument("--near", type=int, default=0); p.add_argument("--align", type=int, default=4)

    p = sub.add_parser("table", help="dump a table of fixed-size records")
    p.add_argument("rom"); p.add_argument("offset"); p.add_argument("--count", type=int, default=16); p.add_argument("--stride", type=int, required=True)
    p.add_argument("--fields", default="ptr:ptr", help="comma list of type:name at consecutive offsets (u8,s8,u16,s16,u32,s32,ptr). Use type@off:name to set an explicit offset")

    p = sub.add_parser("freespace"); p.add_argument("rom"); p.add_argument("--min", default="0x100")

    p = sub.add_parser("dump"); p.add_argument("rom"); p.add_argument("offset"); p.add_argument("--length", default="0x100"); p.add_argument("--stride", type=int, default=16)

    for w in ("u8", "u16", "u32"):
        p = sub.add_parser(w); p.add_argument("rom"); p.add_argument("offset"); p.add_argument("--count", type=int, default=1)

    a = ap.parse_args(argv)
    rom = open(a.rom, "rb").read()

    if a.cmd == "header":
        for k, v in header(rom).items():
            print("%-18s %s" % (k, v))
    elif a.cmd == "pointers":
        t = parse_address(a.target)
        hits = list(find_pointers(rom, t, a.near, a.align))
        for off, v in hits:
            print("file 0x%06X (bus 0x%08X) -> 0x%08X%s" % (off, off + ROM_BASE, v, "" if v == ROM_BASE + t else "  (%+d)" % (v - ROM_BASE - t)))
        print("%d reference(s)" % len(hits), file=sys.stderr)
    elif a.cmd == "table":
        base = parse_address(a.offset)
        fields = []
        auto = 0
        for spec in a.fields.split(","):
            typ, _, name = spec.partition(":")
            if "@" in typ:
                typ, off = typ.split("@"); off = int(off, 0)
            else:
                off = auto
            size = struct.calcsize(FIELD_FMT[typ])
            fields.append((typ, off, name or typ))
            auto = off + size
        print("index  offset    " + "  ".join("%-12s" % f[2] for f in fields))
        for i in range(a.count):
            rec = base + i * a.stride
            vals = []
            for typ, off, name in fields:
                v = struct.unpack_from(FIELD_FMT[typ], rom, rec + off)[0]
                if typ == "ptr":
                    vals.append("0x%08X" % v + (" (f 0x%06X)" % (v - ROM_BASE) if ROM_BASE <= v < ROM_BASE + len(rom) else ""))
                else:
                    vals.append("%d (0x%X)" % (v, v & 0xFFFFFFFF))
            print("%5d  0x%06X  %s" % (i, rec, "  ".join("%-12s" % v for v in vals)))
    elif a.cmd == "freespace":
        for off, length, fill in free_space(rom, int(a.min, 0)):
            print("file 0x%06X (bus 0x%08X)  %8d bytes  fill 0x%02X%s" % (off, off + ROM_BASE, length, fill, "  <- end of ROM" if off + length == len(rom) else ""))
    elif a.cmd == "dump":
        off = parse_address(a.offset); length = int(a.length, 0)
        for i in range(off, off + length, a.stride):
            chunk = rom[i:i + a.stride]
            print("0x%06X  %s  %s" % (i, " ".join("%02X" % b for b in chunk), "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)))
    else:
        off = parse_address(a.offset)
        fmt = FIELD_FMT[a.cmd]; size = struct.calcsize(fmt)
        for i in range(a.count):
            v = struct.unpack_from(fmt, rom, off + i * size)[0]
            print("0x%06X: %d (0x%X)" % (off + i * size, v, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
