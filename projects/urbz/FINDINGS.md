# The Urbz: Sims in the City (GBA) — static survey and hacking roadmap

**Dump:** `THE URBZ AGB`, game code `BOCE` (USA/Europe, six languages), maker `69` (EA), version 0, 32 MB,
SHA-1 `8efd27375d1f92b43fe0d1c93a63c08b7a259acc`. Header checksum OK, entry `b 0x080000C0`.
Every offset below is a ROM file offset unless written as a bus address (`0x08xxxxxx` / `0x03xxxxxx`).
This was done statically (no emulator in this environment) with the `gba-reverse-engineering` skill scripts plus
capstone for disassembly; items marked *confirmed* were verified by decoding data or by matching disassembly, items marked
*likely* are inferences that need a debugger session to pin down.

## 1. ROM layout (confirmed by pointer density, `bl` density and entropy per block)

| Range | Content |
|---|---|
| `0x000000-0x06FFFF` | Thumb code (5-6 % of halfwords are `bl`), literal pools, BIOS wrapper stubs at `0x6B368-0x6B3A0` |
| `0x070000-0x0BFFFF` | Tables: 40-50 % of words are ROM pointers. Resource directory (`0x7A000-0x7F000`, `0x9A000-0xB5000`), behaviour tables with 1755 Thumb function pointers (`0x75000`, `0x78000`, `0x90000-0x92000`), item/object table at `0x75000` (20-byte records, pointers into `0x149xxxx`) |
| `0x0C0000-0x0FFFFFF` | Compressed graphics blobs (entropy 7.3-7.9), referenced from the directory |
| `0x1000000-0x10FFFFF` | More assets, plus per-district data referenced from the save/profile block |
| `0x1100000-0x13FFFFF` | 8-bit PCM sample data (smooth waveforms, entropy 6.5, almost no pointers); no GAX/Sappy signature, so a custom sound driver |
| `0x1400000-0x14FFFFF` | Sprite/animation descriptors referenced from `0x75000` and `0x90000` tables |
| `0x1FF823D-0x1FFFFFF` | 32,195 bytes of `0x00` padding = free space |

## 2. Resource loader and compression (confirmed by disassembly of `0x1EC00` and `0x1ED44`)

Every resource starts with a 4-byte header: `byte0 = type<<4 | flags`, bytes 1-3 = decompressed size (24-bit LE).
The WRAM loader at `0x0801EC00` (and a VRAM twin at `0x0801ED44`) masks bit 3 of the type (`type & ~8`) and jumps
through the table at `0x1EC2C`:

| Type | Handler | Notes |
|---|---|---|
| 0 | BIOS CpuSet (`swi 0x0B`) | raw copy of `size` bytes after the header |
| 1 | BIOS LZ77UnCompWram / Vram (`swi 0x11` / `0x12`) | standard GBA LZ77 |
| 2 | BIOS HuffUnComp (`swi 0x13`) | |
| 3 | BIOS RLUnComp (`swi 0x14` / `0x15`) | |
| 4 | **custom decoder A** via function pointer at IWRAM `0x030031C0` | code copied from ROM `0xD274` (528 bytes) to IWRAM `0x03003530` by the init at `0x1ECD0`; source pointer includes the header |
| 6 | **custom decoder B** via function pointer at IWRAM `0x03003520` | code copied from ROM `0x2EC` (848 bytes) to IWRAM `0x030031D0`; source pointer skips the header |
| 5, 7, 8 | no-op | |
| flag bit 3 (`0x8`) | post-filter at `0x0801EE00` applied to the output | *likely* a delta/diff filter, not yet translated |

BIOS wrapper stubs (each `swi N; bx lr`): CpuFastSet `0x6B36C`, CpuSet `0x6B370`, Div `0x6B374`, Huff `0x6B380`,
LZ77Vram `0x6B384`, LZ77Wram `0x6B388`, ObjAffineSet `0x6B38C`, RLVram `0x6B390`, RLWram `0x6B394`, Sqrt `0x6B398`,
VBlankIntrWait `0x6B39C`. Their callers are how the loaders were found.

**Decoder B (type 6) is fully translated** in `urbz_codec.py` and decodes all 7,431 type-6 resources in the directory to
exactly their declared size. Format: a 4-byte parameter word (dictionary length, escape value, extra distance bits,
literal split bits) + a dictionary of up to 31 bytes + an MSB-first bitstream of 32-bit words with a sentinel bit.
Tokens: literal (`n_c` high bits compared to an adaptive escape value, then `n_a` low bits); escape then an
Elias-gamma code: code ≥ 2 → LZ match of length code+1 with a gamma-coded distance (0xFF = end of stream); code 1 then
bit 0 → 2-byte match with an 8-bit distance; bits 1,0 → literal equal to the escape (and the escape changes); bits 1,1 →
fill run with a gamma-coded count and a dictionary or 11-bit fill byte. The output is written as halfwords, so it is
VRAM-safe. **Decoder A (type 4)** (control byte: `0x80` back-reference, `0x40` fill, else literal run, `0x00` end) is also
translated but only 107 directory blobs use it and they are not yet validated.

## 3. Resource directory (confirmed)

7,682 records of 16 bytes in `0x7A000-0x7F000` and `0x9A000-0xB5000`: `{count(1-3), ptr0, ptr1, ptr2|0}` (some
entries are rotated by one word). Slot 0 is the graphics blob (type 6 in 7,431 cases, sizes 128 B to 2,176 B: 4bpp
tiles, i.e. one 16x16 sprite = 128 B, a 32x32 object = 512 B). Slot 1 is **not** a compressed blob even though byte 0 is
`0x10`/`0x20`: it is a frame descriptor with a u16 offset table (e.g. `10 18 00 00 | 00 00 18 00 | C0 00 00 00 | 0030 0042 0054 ...`,
entries 18 bytes apart), *likely* OAM/frame metadata. 3,353 distinct graphics blobs are shared between records.
Greyscale renders of decoded slot-0 blobs show clean isometric objects and character sprites (see `urbz_dump.py png`).

Palettes are not in the directory. The OBJ palette RAM literal `0x05000200` is used at `0x15F10`, `0x31804`, `0x67648`;
the routine at `0x08015E58` remaps every colour of a palette through a 32-entry lookup table (brightness/tint), so palettes
go through a lighting stage before reaching hardware. Finding the palette source table is the first debugger task.

## 4. Other confirmed structures

- **Save signature** `URBZ0011` at `0x9A2CC`, referenced from the save/load module at `0x4C900-0x4DB00` (pointers at
  `0x4D6EC`, `0x4DA88`; the 16-byte block before it at `0x9A2BC` is referenced six times). The block after it
  (`0x9A2E4`) is a record table with pointers into `0x104C88C`/`0x104CB54` (*likely* per-district data).
- **Behaviour tables**: 599 distinct Thumb functions referenced 1,755 times from `0x75000`, `0x78000`, `0x90000-0x92000`.
  `0x92000` shows repeated rows of five function pointers + a flags word (`01 01 00 05`) = object/state dispatch tables.
- **Item table** at `0x75000`: 20-byte records `{ptr → 0x149xxxx descriptor, 5 bytes of parameters (10/21/21/21/21, 50/6/6/6/6…), pad, u32 id}`
  with sequential ids `0x3D6, 0x3D7, …`.
- **Text**: no ASCII anywhere (`strings` finds nothing but the header and the save signature), so the six-language script uses
  a font-index encoding and is *likely* stored as large blobs referenced from code rather than from the directory
  (search still open; candidates are the 122 large type-4/6 blobs referenced from code).

## 5. What we could do with it (ranked by effort)

1. **Sprite/object viewer and editor** — *days*. Everything needed is already decoded: `urbz_dump.py png` exports every
   graphics resource; add the palette (one debugger session on `0x15E58`'s caller) and the viewer is in colour. Writing back
   requires a type-6 *encoder* (or cheaper: re-encode as BIOS LZ77 and change the header type nibble from 6 to 1, which the
   loader accepts, and place the blob in the 32 KB tail free space or grow the ROM to 64 MB is not possible, so use
   in-place when smaller). Reskins of Sims, furniture and NPCs become straightforward.
2. **Text translation / dialogue editing** — *days once the font table is found*. Locate the font glyphs (a type-6 blob with
   many 8x8/8x16 tiles), then the encoding is the glyph index; strings for the six languages follow the same table.
   A new language or rewritten dialogue is then a table rebuild.
3. **Save editor** — *days*. The `URBZ0011` block and the save module are located; dumping an SRAM save from an emulator and
   diffing against the template at `0x9A2E4` gives the field layout (money, skills, relationships, inventory).
4. **Gameplay tuning** — *hours per change*. The item table at `0x75000` carries per-object parameters in plain bytes; the
   dispatch tables at `0x90000-0x92000` map object kinds to behaviour functions, so swapping an object's behaviour is a
   pointer change. Money/skill gain constants live in the functions those tables reference.
5. **Lighting and palette hacks** — *hours*. The remap LUT used by `0x08015E58` controls tint; patching it yields
   night/sepia/colour-blind modes without touching any asset.
6. **New rooms/levels** — *weeks*. Map data has not been located yet (it is not in the sprite directory); the route is a
   VRAM watchpoint on a background layer in mGBA → DMA source → loader → directory entry, exactly the Klonoa path.
7. **Sound replacement** — *weeks*. Custom driver, raw 8-bit PCM at `0x1100000+`; sample swaps are feasible once the
   sample table is found, music sequencing would need the driver reversed.

## 6. Next debugger session (mGBA)

1. Break on `0x0801EC00` (loader) with a save state in a district; log r0 (source) for every call → maps directory records
   to on-screen objects and finds the map/tilemap loads and the font.
2. Watchpoint `watch/w 0x05000200` → caller of the palette remap → palette source table.
3. Watchpoint on `0x030031C0`/`0x03003520` writes confirms the IWRAM decoder install at boot.
4. Dump SRAM after a save, diff with the `0x9A2E4` template.

## Files

- `urbz_codec.py` — decoders for header types 0-4 and 6 (`decode(rom, offset)`), CLI: `urbz_codec.py rom.gba 0xA054D4 out.bin`
- `urbz_dump.py` — directory parser and exporter (`list`, `png`, `raw`)
