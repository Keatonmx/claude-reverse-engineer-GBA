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
| flag bit 3 of the nibble (header byte `0x80`, e.g. `0xE0` = type 6 + filter) | post-filter at `0x0801EE00` applied to the output | **confirmed**: 16-bit running sum (Diff16 unfilter), implemented as `unfilter16` |

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
- **Level / district records** (confirmed layout, 75 records of 80 bytes at `0x73590 + k*0x50`, `urbz_level.py list`):

  | Offset | Content |
  |---|---|
  | `+0x08` | collision metatiles: type 6, 16 bytes each (values `03`, `43`, `12`, `13`, `10`, `11`, `00`: walkability/edge codes) |
  | `+0x0C` | collision map: type 6, `u16 metatile_count` (matches +0x08 exactly), 3 words, then u16 metatile ids (e.g. 38x20 for record `0x735E0`) |
  | `+0x18` | small raw descriptor |
  | `+0x1C` | **tile pixel bank**: type 0 (raw), 62 KB to 610 KB of 4bpp 8x8 tiles; ends exactly at the next pointer's target |
  | `+0x20` | 0xFFFF-filled table (*likely* the tile→VRAM cache map the engine fills at runtime) |
  | `+0x28/+0x2C`, `+0x38/+0x3C`, `+0x48/+0x4C` | three layers of (map, metatiles): map = type 6 + Diff16, `u16 w, u16 h` (32x30) then w*h u16 metatile ids; metatiles = type 6 (+Diff16), `u16 count`, `u16 0`, then count x 24 u16 tile refs |

  **Metatile geometry and tile refs (confirmed by edge-continuity scoring; true neighbours score 1.1-1.7 against 3.8 for
  random tile pairs, calibrated on 16x16 sprites).** The 24 refs are rows of 8, 8, 4 and 4 tiles of a 64x32 block. The map
  is drawn with a 16 px row pitch and each successive row shifted 32 px to the right, so the next row overdraws the
  block's bottom-right quarter, which is why rows 2-3 only store their left 4 tiles. A tile ref is `bits 0-9` = tile
  index into the first 1024 tiles of the bank (4bpp, 32 bytes each; all three layers use base 0), `bit 12` = vertical
  flip, `bit 13` = horizontal flip, `bits 10-11` most likely the palette bank. The metatile blob header is
  `{u16 count, u16 0, u16 x, u16 x}` with `x` = 0 (layer 1) or 6325 (layers 2-3); it is not a tile base. What the rest
  of the 610 KB bank is for (only 32 KB is addressed by a 10-bit index) is still open: candidates are per-map-region
  tile pages selected by the runtime cache table at +0x20, or graphics for other sub-areas of the district.
  `urbz_level.py render --layer 0` composites the three layers and produces recognisable streets (curbs, pavement
  stripes, road markings, street furniture) in greyscale.
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
6. **District/level editor** — *a week or two*. The level record, collision layer, three visual layers, metatile blocks,
   tile refs and the raw tile bank are all decoded and render as recognisable streets; what is missing is the palette
   (for colour) and the meaning of the bank's remaining pages. Collision edits are already possible today: the collision map is a plain u16 grid over 16-byte metatiles, so
   walls/walkable areas can be moved with `urbz_codec` + a type-6 encoder or by storing the edited map as BIOS LZ77 (type 1).
7. **Sound replacement** — *weeks*. Custom driver, raw 8-bit PCM at `0x1100000+`; sample swaps are feasible once the
   sample table is found, music sequencing would need the driver reversed.

## 6. Next debugger session (mGBA)

1. Break on `0x0801EC00` (loader) with a save state in a district; log r0 (source) for every call → maps directory records
   to on-screen objects and finds the font. Then `watch/w 0x06000000` (charblock 0) after a district load to catch the
   tile-bank → VRAM copy: it tells which bank pages beyond the first 1024 tiles are used and when (the +0x20 table is the
  cache map), and a `watch/w 0x05000000` gives the background palette source.
2. Watchpoint `watch/w 0x05000200` → caller of the palette remap → palette source table.
3. Watchpoint on `0x030031C0`/`0x03003520` writes confirms the IWRAM decoder install at boot.
4. Dump SRAM after a save, diff with the `0x9A2E4` template.

## Files

- `urbz_codec.py` — decoders for header types 0-4 and 6 (`decode(rom, offset)`), CLI: `urbz_codec.py rom.gba 0xA054D4 out.bin`
- `urbz_dump.py` — directory parser and exporter (`list`, `png`, `raw`)
- `urbz_level.py` — level record parser and layer renderer (`list`, `render --layer N --crop --origin --zoom`)
