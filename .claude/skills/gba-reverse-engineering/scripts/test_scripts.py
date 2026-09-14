#!/usr/bin/env python3
"""Self-test for the bundled scripts. Run: python3 test_scripts.py"""
import os
import random
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gba_compress as gc  # noqa: E402
import thumb_patch as tp  # noqa: E402
import gba_rom as gr  # noqa: E402
import render_tiles as rt  # noqa: E402


class TestLZ77(unittest.TestCase):
    def test_known_vector(self):
        # "AAAAAAAA": literal A, then a 7-byte copy from displacement 1
        self.assertEqual(gc.lz77_decompress(bytes.fromhex("10080000 40 41 40 00".replace(" ", ""))), b"A" * 8)

    def test_roundtrip(self):
        rnd = random.Random(1)
        samples = [b"", b"x", b"A" * 1000, bytes(rnd.randrange(256) for _ in range(3000)),
                   (b"tilemap" * 50 + bytes(range(256))) * 3]
        for s in samples:
            for vram in (False, True):
                enc = gc.lz77_compress(s, vram_safe=vram)
                self.assertEqual(enc[0], 0x10)
                self.assertEqual(gc.lz77_decompress(enc), s)
                self.assertEqual(len(enc) % 4, 0)

    def test_vram_safe_has_no_disp1(self):
        enc = gc.lz77_compress(b"A" * 64, vram_safe=True)
        pos = 4
        size = 0
        while size < 64:
            flags = enc[pos]; pos += 1
            for bit in range(7, -1, -1):
                if size >= 64:
                    break
                if flags & (1 << bit):
                    disp = (((enc[pos] & 0xF) << 8) | enc[pos + 1]) + 1
                    self.assertGreaterEqual(disp, 2)
                    size += (enc[pos] >> 4) + 3; pos += 2
                else:
                    size += 1; pos += 1

    def test_rejects_garbage(self):
        with self.assertRaises(gc.DecompressError):
            gc.lz77_decompress(b"\x10\xff\xff\x00" + b"\xff" * 4)


class TestHuffman(unittest.TestCase):
    def test_known_vector(self):
        # 8-bit symbols A,B; root has two data children; bits 0,0,1 => "AAB"
        blob = bytes.fromhex("28030000") + bytes([1, 0xC0, 0x41, 0x42]) + struct.pack("<I", 0b001 << 29)
        self.assertEqual(gc.huffman_decompress(blob), b"AAB")

    def test_roundtrip_8bit(self):
        rnd = random.Random(2)
        s = bytes(rnd.choice(b"abcdefgh  \n") for _ in range(5000))
        enc = gc.huffman_compress(s, bits=8)
        self.assertEqual(enc[0], 0x28)
        self.assertEqual(gc.huffman_decompress(enc), s)

    def test_roundtrip_4bit_and_fallback(self):
        rnd = random.Random(3)
        uniform = bytes(rnd.randrange(256) for _ in range(4096))  # 256 symbols -> 8-bit layout overflows, falls back
        enc = gc.huffman_compress(uniform, bits=8)
        self.assertIn(enc[0], (0x24, 0x28))
        self.assertEqual(gc.huffman_decompress(enc), uniform)
        enc4 = gc.huffman_compress(b"hello hello hello", bits=4)
        self.assertEqual(enc4[0], 0x24)
        self.assertEqual(gc.huffman_decompress(enc4), b"hello hello hello")

    def test_single_symbol(self):
        enc = gc.huffman_compress(b"\x07" * 100)
        self.assertEqual(gc.huffman_decompress(enc), b"\x07" * 100)


class TestRLE(unittest.TestCase):
    def test_known_vector(self):
        self.assertEqual(gc.rle_decompress(bytes.fromhex("30050000 81 41 00 42".replace(" ", ""))), b"AAAAB")

    def test_roundtrip(self):
        rnd = random.Random(4)
        for s in (b"", b"A" * 500, bytes(rnd.randrange(3) for _ in range(2000)), b"ab" * 300 + b"\0" * 129):
            self.assertEqual(gc.rle_decompress(gc.rle_compress(s)), s)


class TestChain(unittest.TestCase):
    def test_lz77_then_huffman(self):
        s = (b"level data " * 200)
        enc = gc.huffman_compress(gc.lz77_compress(s))
        out, layers = gc.decompress(enc, 0, chain=True)
        self.assertEqual(out, s)
        self.assertEqual(layers, ["huffman", "lz77"])

    def test_scan_finds_block(self):
        s = bytes(range(256)) * 4
        blob = b"\xAA" * 100 + gc.lz77_compress(s) + b"\x00" * 50
        hits = list(gc.scan(blob, min_size=64, start=0))
        self.assertTrue(any(off == 100 and kind == "lz77" and size == 1024 for off, kind, size, comp in hits))
        off, kind, size, comp = [h for h in hits if h[0] == 100][0]
        self.assertEqual(comp, len(gc.lz77_compress(s)))

    def test_scan_rejects_zero_filled_region(self):
        # A zeroed header region that happens to parse as RLE must not be reported (it would "inflate")
        blob = bytes([0x30, 0x31, 0x96, 0x00]) + b"\x00" * 80000
        self.assertEqual(list(gc.scan(blob, min_size=64, start=0)), [])

    def test_decompress_ex_end_offset(self):
        enc = gc.lz77_compress(b"abc" * 100)
        out, end = gc.lz77_decompress_ex(b"\xEE" * 8 + enc + b"\xEE" * 8, 8)
        self.assertEqual(out, b"abc" * 100)
        self.assertLessEqual(8 + end - 8, 8 + len(enc))
        self.assertGreaterEqual(end, 8 + len(enc) - 3)  # padding may be skipped


class TestThumbPatch(unittest.TestCase):
    """Vectors taken from the klo-gba.js custom-level loader patch (Klonoa: Empire of Dreams, USA)."""

    def test_bl(self):
        self.assertEqual(tp.thumb_bl(0x08043B0C, 0x08367610), bytes.fromhex("23F380FD"))
        self.assertEqual(tp.thumb_decode_bl(0x08043B0C, bytes.fromhex("23F380FD")), 0x08367610)

    def test_stub(self):
        self.assertEqual(tp.thumb_to_arm_stub(0x08367610, 0x08367650), bytes.fromhex("78463C300047"))

    def test_arm_ldr_pc(self):
        self.assertEqual(tp.arm_ldr_pc(4, 0x08367654, 0x08367620), bytes.fromhex("3C401FE5"))          # ldr r4,[pc,#-0x3C]
        self.assertEqual(tp.arm_ldr_pc(0, 0x0836765C, 0x08367624, cond=0), bytes.fromhex("40001F05"))  # ldreq r0,[pc,#-0x40]
        self.assertEqual(tp.arm_ldr_pc(1, 0x08000100, 0x08000200), bytes.fromhex("F8109FE5"))        # forward: +0xF8

    def test_misc(self):
        self.assertEqual(tp.arm_bx_lr(), bytes.fromhex("1EFF2FE1"))
        self.assertEqual(tp.arm_b(0x08000000, 0x080000C0), bytes.fromhex("2E0000EA"))
        self.assertEqual(tp.thumb_b(0x08001000, 0x08001040), bytes.fromhex("1EE0"))
        self.assertEqual(tp.thumb_ldr_pc(0, 0x08001000, 0x08001010), bytes.fromhex("0348"))
        with self.assertRaises(ValueError):
            tp.thumb_bl(0x08000000, 0x08800000)


class TestRom(unittest.TestCase):
    def setUp(self):
        rom = bytearray(0x1000)
        rom[0:4] = tp.arm_b(0x08000000, 0x080000C0)
        rom[0xA0:0xA0 + 6] = b"KLONOA"
        rom[0xAC:0xB0] = b"AKEE"; rom[0xB0:0xB2] = b"01"; rom[0xB2] = 0x96
        rom[0xBD] = (-(sum(rom[0xA0:0xBD]) + 0x19)) & 0xFF
        struct.pack_into("<I", rom, 0x200, 0x08000800)
        struct.pack_into("<I", rom, 0x204, 0x08000804)
        rom[0x900:0x1000] = b"\xFF" * 0x700
        self.rom = bytes(rom)

    def test_header(self):
        h = gr.header(self.rom)
        self.assertEqual(h["title"], "KLONOA"); self.assertEqual(h["game_code"], "AKEE")
        self.assertIn("ok", h["header_checksum"]); self.assertEqual(h["entry_target"], "0x080000C0")

    def test_pointers_and_free(self):
        self.assertEqual([o for o, v in gr.find_pointers(self.rom, 0x800)], [0x200])
        self.assertEqual([o for o, v in gr.find_pointers(self.rom, 0x800, near=8)], [0x200, 0x204])
        runs = list(gr.free_space(self.rom, 0x100))
        self.assertIn((0x900, 0x700, 0xFF), runs)


class TestRender(unittest.TestCase):
    def test_png(self):
        d = tempfile.mkdtemp()
        tiles = bytes(range(64)) * 4
        pal = b"".join(struct.pack("<H", (i & 31) | ((i & 31) << 5) | ((i & 31) << 10)) for i in range(256))
        open(os.path.join(d, "t.bin"), "wb").write(tiles); open(os.path.join(d, "p.bin"), "wb").write(pal)
        out = os.path.join(d, "o.png")
        r = subprocess.run([sys.executable, os.path.join(HERE, "render_tiles.py"), "tileset", os.path.join(d, "t.bin"), "--palette", os.path.join(d, "p.bin"), "--bpp", "8", "--columns", "2", "-o", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        png = open(out, "rb").read()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", png[16:24])
        self.assertEqual((w, h), (16, 16))
        self.assertEqual(rt.decode_palette(pal[:2]), [(0, 0, 0)])


if __name__ == "__main__":
    unittest.main(verbosity=1)
