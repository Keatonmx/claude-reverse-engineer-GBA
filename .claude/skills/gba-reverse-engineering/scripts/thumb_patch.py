#!/usr/bin/env python3
"""Hand-assemble the handful of ARM7TDMI instructions you need for ROM patches, and generate hook stubs.

No toolchain required. Every output is printed as little-endian bytes ready to write into the ROM,
plus the file offset (bus address minus 0x08000000).

Usage examples
    thumb_patch.py bl 0x08043B0C 0x08367610          # Thumb BL from -> to        => 23 F3 80 FD
    thumb_patch.py b  0x08001000 0x08001040          # Thumb unconditional branch
    thumb_patch.py arm-b 0x08000000 0x080000C0       # ARM branch (e.g. the ROM entry point)
    thumb_patch.py decode-bl 0x08043B0C 23F380FD     # where does this BL go?
    thumb_patch.py arm-ldr-pc r4 0x08367654 0x08367620   # ARM ldr r4,[pc,#imm] to a literal at that address
    thumb_patch.py thumb-ldr-pc r0 0x08001000 0x08001010 # Thumb ldr r0,[pc,#imm]
    thumb_patch.py thumb-to-arm-stub 0x08367610 0x08367650  # stub at first address that jumps into ARM code at second
    thumb_patch.py hook 0x08043B0C 0x08367610 --arm-body 0x08367650  # full "bl + stub" recipe

Encodings follow the ARM7TDMI reference (ARM DDI 0029E). Thumb BL is the two-halfword v4T form.
"""
from __future__ import annotations

import argparse
import sys

ROM_BASE = 0x08000000
REGS = {("r%d" % i): i for i in range(16)}
REGS.update({"sp": 13, "lr": 14, "pc": 15})


def hexbytes(b: bytes) -> str:
    return " ".join("%02X" % x for x in b)


def reg(name: str) -> int:
    name = name.lower()
    if name not in REGS:
        raise SystemExit("unknown register %r" % name)
    return REGS[name]


def to_offset(addr: int) -> int:
    return addr - ROM_BASE if addr >= ROM_BASE else addr


# ----------------------------------------------------------------------------- Thumb

def thumb_bl(src: int, dst: int) -> bytes:
    """Thumb BL: two 16-bit halfwords. PC-relative from src+4, range +-4MB, target must be halfword aligned."""
    if dst & 1:
        raise ValueError("BL target must be halfword aligned")
    off = dst - (src + 4)
    if not -(1 << 22) <= off < (1 << 22):
        raise ValueError("BL target out of range (+-4MB)")
    off &= (1 << 23) - 1
    hi = 0xF000 | ((off >> 12) & 0x7FF)
    lo = 0xF800 | ((off >> 1) & 0x7FF)
    return hi.to_bytes(2, "little") + lo.to_bytes(2, "little")


def thumb_decode_bl(src: int, halfwords: bytes) -> int:
    hi = int.from_bytes(halfwords[0:2], "little")
    lo = int.from_bytes(halfwords[2:4], "little")
    if (hi & 0xF800) != 0xF000 or (lo & 0xF800) != 0xF800:
        raise ValueError("not a Thumb BL pair")
    off = ((hi & 0x7FF) << 12) | ((lo & 0x7FF) << 1)
    if off & (1 << 22):
        off -= 1 << 23
    return src + 4 + off


def thumb_b(src: int, dst: int) -> bytes:
    """Thumb unconditional B: 11-bit signed halfword offset from src+4 (+-2KB)."""
    off = (dst - (src + 4)) >> 1
    if not -1024 <= off < 1024:
        raise ValueError("Thumb B out of range (+-2KB); use BL or a literal + BX")
    return (0xE000 | (off & 0x7FF)).to_bytes(2, "little")


def thumb_ldr_pc(rd: int, insn: int, literal: int) -> bytes:
    """Thumb LDR Rd,[PC,#imm8*4]. PC is (insn+4) & ~3. Literal must be word aligned and ahead of the instruction."""
    base = (insn + 4) & ~3
    off = literal - base
    if literal & 3 or off < 0 or off > 1020:
        raise ValueError("literal must be word aligned, within 0..1020 bytes after (insn+4)&~3")
    return (0x4800 | (rd << 8) | (off >> 2)).to_bytes(2, "little")


def thumb_mov_r0_pc() -> bytes:
    return (0x4678).to_bytes(2, "little")  # mov r0, pc  (hi-register MOV)


def thumb_add_imm8(rd: int, imm: int) -> bytes:
    return (0x3000 | (rd << 8) | (imm & 0xFF)).to_bytes(2, "little")


def thumb_bx(rm: int) -> bytes:
    return (0x4700 | (rm << 3)).to_bytes(2, "little")


def thumb_nop() -> bytes:
    return (0x46C0).to_bytes(2, "little")  # mov r8, r8


# ----------------------------------------------------------------------------- ARM

def arm_b(src: int, dst: int, link: bool = False, cond: int = 0xE) -> bytes:
    off = (dst - (src + 8)) >> 2
    if not -(1 << 23) <= off < (1 << 23):
        raise ValueError("ARM branch out of range (+-32MB)")
    word = (cond << 28) | (0xA << 24) | ((1 if link else 0) << 24) | (off & 0xFFFFFF)
    return word.to_bytes(4, "little")


def arm_ldr_pc(rd: int, insn: int, literal: int, cond: int = 0xE) -> bytes:
    """ARM LDR Rd,[PC,#+-imm12]. PC reads as insn+8."""
    off = literal - (insn + 8)
    up = 1 if off >= 0 else 0
    off = abs(off)
    if off > 0xFFF:
        raise ValueError("literal too far (+-4095 bytes from insn+8)")
    word = (cond << 28) | (0x5 << 24) | (1 << 24) | (up << 23) | (1 << 20) | (15 << 16) | (rd << 12) | off
    return word.to_bytes(4, "little")


def arm_bx_lr(cond: int = 0xE) -> bytes:
    return ((cond << 28) | 0x012FFF1E).to_bytes(4, "little")


# ----------------------------------------------------------------------------- stubs

def thumb_to_arm_stub(stub_addr: int, arm_body: int) -> bytes:
    """Thumb stub that switches to ARM state at arm_body:  mov r0,pc / add r0,#N / bx r0.

    In Thumb, `mov r0, pc` reads stub_addr+4. arm_body must be word aligned and within 255 bytes of that.
    r0 is clobbered; if the hooked code needs r0 alive, pick another scheme (push/pop or a literal load).
    """
    if arm_body & 3:
        raise ValueError("ARM body must be word aligned")
    delta = arm_body - (stub_addr + 4)
    if not 0 <= delta <= 0xFF:
        raise ValueError("ARM body must be 0..255 bytes after stub+4 for the add-imm8 form")
    return thumb_mov_r0_pc() + thumb_add_imm8(0, delta) + thumb_bx(0)


def print_patch(label: str, addr: int, b: bytes):
    print("%-28s @ bus 0x%08X (file 0x%06X): %s" % (label, addr, to_offset(addr), hexbytes(b)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, help_ in (("bl", "Thumb BL from src to dst"), ("b", "Thumb B from src to dst"), ("arm-b", "ARM B from src to dst"), ("arm-bl", "ARM BL from src to dst")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("src")
        p.add_argument("dst")

    p = sub.add_parser("decode-bl", help="decode a Thumb BL pair (hex bytes as stored in the ROM)")
    p.add_argument("src")
    p.add_argument("hexbytes")

    for name in ("arm-ldr-pc", "thumb-ldr-pc"):
        p = sub.add_parser(name, help="%s LDR Rd,[PC,#imm] to a literal" % name.split("-")[0].upper())
        p.add_argument("rd")
        p.add_argument("insn")
        p.add_argument("literal")

    p = sub.add_parser("thumb-to-arm-stub", help="Thumb stub that BXes into an ARM body")
    p.add_argument("stub")
    p.add_argument("arm_body")

    p = sub.add_parser("hook", help="print a complete recipe: BL at hook site into a Thumb stub that enters an ARM body")
    p.add_argument("hook_site")
    p.add_argument("stub")
    p.add_argument("--arm-body", required=True)

    a = ap.parse_args(argv)
    i = lambda s: int(s, 0)  # noqa: E731

    if a.cmd == "bl":
        print_patch("thumb bl", i(a.src), thumb_bl(i(a.src), i(a.dst)))
    elif a.cmd == "b":
        print_patch("thumb b", i(a.src), thumb_b(i(a.src), i(a.dst)))
    elif a.cmd == "arm-b":
        print_patch("arm b", i(a.src), arm_b(i(a.src), i(a.dst)))
    elif a.cmd == "arm-bl":
        print_patch("arm bl", i(a.src), arm_b(i(a.src), i(a.dst), link=True))
    elif a.cmd == "decode-bl":
        dst = thumb_decode_bl(i(a.src), bytes.fromhex(a.hexbytes.replace(" ", "")))
        print("bl target: 0x%08X (file 0x%06X)" % (dst, to_offset(dst)))
    elif a.cmd == "arm-ldr-pc":
        print_patch("arm ldr %s,[pc,#..]" % a.rd, i(a.insn), arm_ldr_pc(reg(a.rd), i(a.insn), i(a.literal)))
    elif a.cmd == "thumb-ldr-pc":
        print_patch("thumb ldr %s,[pc,#..]" % a.rd, i(a.insn), thumb_ldr_pc(reg(a.rd), i(a.insn), i(a.literal)))
    elif a.cmd == "thumb-to-arm-stub":
        print_patch("mov r0,pc/add r0,#n/bx r0", i(a.stub), thumb_to_arm_stub(i(a.stub), i(a.arm_body)))
    elif a.cmd == "hook":
        site, stub, body = i(a.hook_site), i(a.stub), i(a.arm_body)
        print("1. Replace the 4 bytes at the hook site with a BL into free space")
        print_patch("   thumb bl", site, thumb_bl(site, stub))
        print("   (the two halfwords you overwrite must be re-executed at the end of your ARM body, and the")
        print("    BL clobbers LR: if the original function needed LR, push/pop it in the stub)")
        print("2. Thumb stub in free space that switches to ARM state")
        print_patch("   stub", stub, thumb_to_arm_stub(stub, body))
        print("3. ARM body at 0x%08X ends with the re-executed instruction(s) then `bx lr` = %s" % (body, hexbytes(arm_bx_lr())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
