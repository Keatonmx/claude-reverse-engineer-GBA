#!/usr/bin/env python3
"""Create and apply IPS and UPS patch files, so a hack ships as a diff and never as a ROM.

    python3 gba_patchfile.py make original.gba modified.gba out.ups     # or out.ips
    python3 gba_patchfile.py apply original.gba patch.ups out.gba       # or patch.ips

IPS is the classic format but its offsets are 24-bit: it cannot address anything past 16 MB, so a 32 MB ROM patched in
its tail free space needs UPS (or BPS). UPS carries CRC32s of input, output and patch, and `apply` checks them.
Importable: ips_make, ips_apply, ups_make, ups_apply (all bytes -> bytes).
"""
import struct
import sys
import zlib


# ---------------------------------------------------------------- IPS
def ips_make(orig: bytes, mod: bytes) -> bytes:
    if len(mod) > 0xFFFFFF + 1 or len(orig) > len(mod):
        raise ValueError("IPS: output larger than 16 MB or smaller than the input; use UPS")
    out = bytearray(b"PATCH")
    i, n = 0, len(mod)
    while i < n:
        if i < len(orig) and orig[i] == mod[i]:
            i += 1
            continue
        start = i
        while i < n and (i >= len(orig) or orig[i] != mod[i]) and i - start < 0xFFFF:
            i += 1
        if start == 0x454F46:  # "EOF" as an offset would terminate the patch early; split one byte earlier
            start -= 1
        chunk = mod[start:i]
        out += struct.pack(">I", start)[1:] + struct.pack(">H", len(chunk)) + chunk
    return bytes(out + b"EOF")


def ips_apply(orig: bytes, patch: bytes) -> bytes:
    if patch[:5] != b"PATCH":
        raise ValueError("not an IPS file")
    out = bytearray(orig)
    pos = 5
    while patch[pos:pos + 3] != b"EOF":
        off = int.from_bytes(patch[pos:pos + 3], "big")
        size = struct.unpack_from(">H", patch, pos + 3)[0]
        pos += 5
        if size == 0:  # RLE record
            rle, byte = struct.unpack_from(">HB", patch, pos)
            pos += 3
            data = bytes([byte]) * rle
        else:
            data = patch[pos:pos + size]
            pos += size
        if off + len(data) > len(out):
            out += bytes(off + len(data) - len(out))
        out[off:off + len(data)] = data
    return bytes(out)


# ---------------------------------------------------------------- UPS
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(x | 0x80)
            return bytes(out)
        out.append(x)
        n -= 1


def _read_varint(buf: bytes, pos: int):
    n, shift = 0, 1
    while True:
        x = buf[pos]
        pos += 1
        n += (x & 0x7F) * shift
        if x & 0x80:
            return n, pos
        shift <<= 7
        n += shift


def ups_make(orig: bytes, mod: bytes) -> bytes:
    p = bytearray(b"UPS1") + _varint(len(orig)) + _varint(len(mod))
    n = max(len(orig), len(mod))
    a = orig + bytes(n - len(orig))
    b = mod + bytes(n - len(mod))
    i, last = 0, 0
    while i < n:
        if a[i] == b[i]:
            i += 1
            continue
        p += _varint(i - last)
        while i < n and a[i] != b[i]:
            p.append(a[i] ^ b[i])
            i += 1
        p.append(0)
        i += 1
        last = i
    p += struct.pack("<II", zlib.crc32(orig), zlib.crc32(mod))
    p += struct.pack("<I", zlib.crc32(p))
    return bytes(p)


def ups_apply(orig: bytes, patch: bytes, check: bool = True) -> bytes:
    if patch[:4] != b"UPS1":
        raise ValueError("not a UPS file")
    in_crc, out_crc, p_crc = struct.unpack_from("<III", patch, len(patch) - 12)
    if check and zlib.crc32(patch[:-4]) != p_crc:
        raise ValueError("UPS file is corrupt")
    if check and zlib.crc32(orig) != in_crc:
        raise ValueError("input does not match the patch (CRC32 %08X, patch expects %08X)" % (zlib.crc32(orig), in_crc))
    pos = 4
    in_size, pos = _read_varint(patch, pos)
    out_size, pos = _read_varint(patch, pos)
    out = bytearray(orig) + bytes(max(0, out_size - len(orig)))
    i = 0
    end = len(patch) - 12
    while pos < end:
        skip, pos = _read_varint(patch, pos)
        i += skip
        while patch[pos]:
            if i < len(out):
                out[i] ^= patch[pos]
            i += 1
            pos += 1
        pos += 1
        i += 1
    out = bytes(out[:out_size])
    if check and zlib.crc32(out) != out_crc:
        raise ValueError("patched output failed its CRC check")
    return out


def main():
    a = sys.argv[1:]
    if len(a) != 4 or a[0] not in ("make", "apply"):
        print(__doc__)
        return
    if a[0] == "make":
        orig, mod = open(a[1], "rb").read(), open(a[2], "rb").read()
        p = ups_make(orig, mod) if a[3].lower().endswith(".ups") else ips_make(orig, mod)
        open(a[3], "wb").write(p)
        print("%s: %d bytes" % (a[3], len(p)))
    else:
        orig, patch = open(a[1], "rb").read(), open(a[2], "rb").read()
        out = ups_apply(orig, patch) if patch[:4] == b"UPS1" else ips_apply(orig, patch)
        open(a[3], "wb").write(out)
        print("%s: %d bytes" % (a[3], len(out)))


if __name__ == "__main__":
    main()
