# GBA hardware cheat sheet for reverse engineers

Everything here is what you need to *interpret* what a debugger shows you. For the authoritative detail, GBATEK
(Martin Korth) is the reference every GBA hacker keeps open; Tonc (Jasper Vijn) explains the same material as a tutorial.

## Contents
1. Memory map (where an address lives tells you what the data is)
2. Bus address vs ROM file offset
3. Video: backgrounds, tiles, tilemaps, palettes
4. Objects (sprites) and OAM
5. DMA
6. BIOS calls (SWI) worth recognising
7. CPU: ARM vs Thumb, calling convention, what registers mean in a trace
8. Cartridge header

## 1. Memory map

| Range | Size | Name | What usually lives there |
|---|---|---|---|
| `0000_0000-0000_3FFF` | 16 KB | BIOS | Boot + SWI routines (decompression, memcpy, div). Read-protected outside BIOS. |
| `0200_0000-0203_FFFF` | 256 KB | EWRAM (External / "slow" WRAM) | Large game state: decompressed level data, object pools, heaps. 16-bit bus, slower. |
| `0300_0000-0300_7FFF` | 32 KB | IWRAM (Internal / "fast" WRAM) | Hot data + hot code: player state, per-frame copies staged for DMA, stack (top at `0300_7Fxx`). |
| `0400_0000-0400_03FE` | 1 KB | I/O registers | Display control, BG control/scroll, DMA, timers, keys, interrupts. |
| `0500_0000-0500_03FF` | 1 KB | Palette RAM | 256 BG colours at `0500_0000`, 256 OBJ colours at `0500_0200`. BGR555. |
| `0600_0000-0601_7FFF` | 96 KB | VRAM | Tiles (charblocks) + tilemaps (screenblocks) for BGs at `0600_0000-0600_FFFF`; OBJ tiles at `0601_0000-0601_7FFF`. |
| `0700_0000-0700_03FF` | 1 KB | OAM | 128 sprite attribute entries, 8 bytes each. |
| `0800_0000-09FF_FFFF` | up to 32 MB | Game Pak ROM | The cartridge. Mirrored at `0A00_0000` and `0C00_0000` with different wait states. |
| `0E00_0000-0E00_FFFF` | 64 KB | SRAM / Flash | Save data (8-bit bus). |

Reading a trace: an address starting with `03` is fast RAM (a variable or a staging buffer), `02` is a big buffer,
`06` is graphics memory, `08` is a ROM constant/table/asset. A pointer value of `08xxxxxx` stored in RAM or in a
ROM table is a pointer into the ROM.

## 2. Bus address vs ROM file offset

`file_offset = bus_address - 0x08000000`. Debuggers and disassemblers show bus addresses; your hex editor and your
scripts use file offsets. Pointer tables in the ROM store bus addresses, so when you find `FC 27 1B 08` in a table it is
the little-endian word `0x081B27FC`, i.e. file offset `0x1B27FC`. All bundled scripts accept either form.

## 3. Video

Display control `DISPCNT` (`0400_0000`) picks a BG mode. Modes 0-2 are tiled (almost every 2D game); 3-5 are bitmap.

- **Tiles** are 8x8. 4bpp tiles are 32 bytes (two pixels per byte, low nibble = left pixel), 8bpp tiles are 64 bytes.
  A **charblock** is 16 KB of tiles (512 4bpp tiles). BG charblocks 0-3 at `0600_0000 + n*0x4000`.
- **Tilemaps** (regular BGs) are arrays of 16-bit **screen entries** in **screenblocks** (2 KB = 32x32 entries) at
  `0600_0000 + n*0x800`. Screen entry: bits 0-9 tile index, bit 10 horizontal flip, bit 11 vertical flip,
  bits 12-15 palette bank (4bpp only). Affine BGs (mode 1/2) use 8-bit entries with no flip/palette bits.
- **BGxCNT** (`0400_0008 + 2*x`): priority (bits 0-1), charblock (2-3), mosaic (6), colour depth (7: 0=4bpp 1=8bpp),
  screenblock (8-12), wrap (13), size (14-15: 256x256 / 512x256 / 256x512 / 512x512 px for regular BGs).
- **BGxHOFS/VOFS** (`0400_0010 + 4*x`): scroll. Watching these is a quick way to find the camera variables.
- **Palettes**: 16-bit BGR555 little endian. `r = (v & 31) << 3`, `g = ((v >> 5) & 31) << 3`, `b = ((v >> 10) & 31) << 3`.
  Colour 0 of each 16-colour bank (or index 0 in 8bpp) is transparent.

A game does **not** keep whole levels in VRAM. VRAM holds only the visible window (typically a 512x256 or 256x256 px
wrapping tilemap). As the camera scrolls, the game streams the newly exposed row/column of tiles from a full level map
in WRAM into the screenblock, usually via DMA. So editing a tile in the VRAM viewer changes one frame; the source of
truth is one hop (WRAM) or two hops (compressed ROM asset) upstream.

## 4. Objects (sprites) and OAM

128 OAM entries, 8 bytes each (the 4th halfword is affine data):

- attr0: bits 0-7 Y, 8-9 object mode (affine/hide), 10-11 blend mode, 12 mosaic, 13 colour depth, 14-15 shape
- attr1: bits 0-8 X, 9-13 affine index, 12 hflip, 13 vflip (non-affine), 14-15 size
- attr2: bits 0-9 tile index (OBJ charblock at `0601_0000`), 10-11 priority, 12-15 palette bank

Hardware OAM is rewritten every frame from a shadow copy in WRAM ("OAM buffer"). Watch writes to `0700_0000` to find
the OAM copy routine, then watch its source buffer to find the per-object structs. Object *definitions* in the ROM
(kind, spawn x/y, sprite id) are a separate, game-specific table, like the 44-byte records in Klonoa.

## 5. DMA

Four channels, registers at `0400_00B0 + 12*n`: `DMAnSAD` (source, 32-bit), `DMAnDAD` (dest, 32-bit),
`DMAnCNT_L` (count), `DMAnCNT_H` (control: bit 15 enable, 12-13 timing: immediately / VBlank / HBlank / special,
10 32-bit transfer, 9 repeat, 5-6 source adjust, 7-8 dest adjust).

Why you care: when you break on a write to VRAM and the PC is inside a tight loop writing `0400_00Bx`, the "writer" is a
DMA. The interesting information is the value written into `DMAnSAD`, that is where the data really comes from.
Both mGBA and No$GBA will show the DMA registers in their I/O viewers.

## 6. BIOS calls (SWI) worth recognising

In Thumb code, `swi 0xNN`; in ARM code `swi 0xNN0000`. The ones you will see constantly:

| SWI | Name | Meaning |
|---|---|---|
| 0x00 | SoftReset | |
| 0x05 | VBlankIntrWait | Idle loop of the main game loop |
| 0x06 / 0x07 | Div / DivArm | Signed division (no hardware divider) |
| 0x08 | Sqrt | |
| 0x0A | ArcTan2 | |
| 0x0B | CpuSet | memcpy/memset, r0 src, r1 dst, r2 count+flags |
| 0x0C | CpuFastSet | 32-byte-block memcpy |
| 0x10 | BitUnPack | Expand 1/2/4bpp to wider |
| 0x11 | LZ77UnCompWram | LZ77 decode, 8-bit writes, r0 src, r1 dst |
| 0x12 | LZ77UnCompVram | LZ77 decode, 16-bit writes (VRAM cannot take byte writes) |
| 0x13 | HuffUnComp | Huffman decode |
| 0x14 / 0x15 | RLUnCompWram / Vram | RLE decode |
| 0x16-0x18 | Diff8bitUnFilter / Diff16bit | Delta filters |

For every decompression SWI, **r0 is the source pointer** (in ROM, usually) and **r1 is the destination**. A breakpoint
on the SWI, or a write watchpoint on the destination, hands you the ROM address of the compressed asset. Compressed
formats are in `compression.md`. Games may also ship their own decompressors (custom LZ variants); the SWI table
then does not help, but the same watchpoint technique finds the routine.

## 7. CPU

ARM7TDMI, 16.78 MHz. Two instruction sets: **ARM** (32-bit instructions) and **Thumb** (16-bit). The ROM entry point
at `0800_0000` is an ARM branch; nearly all game code is Thumb because the 16-bit cartridge bus makes Thumb faster.
Hot routines are sometimes copied into IWRAM and run as ARM. `bx rN` switches state: bit 0 of the target = 1 means Thumb.

Calling convention (AAPCS): arguments in r0-r3, return in r0, r4-r11 callee-saved, r12 scratch, r13 sp, r14 lr, r15 pc.
Thumb `bl` clobbers lr, which is why patch stubs push/pop it. Thumb PC reads as instruction address + 4; ARM PC reads
as instruction address + 8 (this matters for pc-relative literal loads, see `patching.md`).

Reading a Thumb function quickly: `push {r4-r7,lr}` prologue, `ldr rN, [pc, #x]` loads a 32-bit constant from the literal
pool just after the function (often a RAM address or a ROM table), `ldrh/strh` are 16-bit accesses (coordinates, tile
indices), `lsl #n`/`lsr #n` chains around a table load usually mean "index * struct size".

## 8. Cartridge header (first 192 bytes)

| Offset | Content |
|---|---|
| 0x00 | ARM `b` to the entry point |
| 0x04-0x9F | Nintendo logo (fixed 156 bytes; BIOS refuses to boot without it) |
| 0xA0-0xAB | Title, 12 ASCII bytes |
| 0xAC-0xAF | Game code (e.g. `AKEE`: `A`=GBA game, `KE`=id, `E`=USA region) |
| 0xB0-0xB1 | Maker code |
| 0xB2 | Fixed `0x96` |
| 0xBC | Version |
| 0xBD | Header checksum: `-(sum(0xA0..0xBC) + 0x19) & 0xFF` |

`gba_rom.py header rom.gba` prints all of this and the SHA-1, which is how you pin the exact dump you reverse engineered
(the addresses you find are only valid for that dump; other regions/revisions shift everything).
