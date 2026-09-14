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

**Decoder B (type 6) is fully translated** in `urbz_codec.py`; it decodes all 7,431 type-6 resources in the directory to
their declared size and is **byte-identical to the game's own loader** on every blob tested through the emulator oracle
(sprites and Diff16-filtered map/metatile blobs up to 16,900 bytes). Format: a 4-byte parameter word (dictionary length, escape value, extra distance bits,
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
- **District records (verified in the emulator, see section 6)**: a table of **71** 80-byte records on a fixed 0x50
  stride from `0x73568` (the game computes `0x08073568 + index*0x50` at `0x08031BC0`; the district index lives at
  IWRAM `0x030048F8`, 62 = the first playable district, 72 = the Create-a-Bod screen). The earlier signature scan found
  only 33 of them because the other records store some layers as BIOS LZ77 (`0x10`), LZ77 + Diff16 (`0x90`, which the
  game ships and the BIOS accepts) or RLE (`0x30`) instead of type 6. Each record:

  | Offset | Content |
  |---|---|
  | `+0x00/+0x04`, `+0x10/+0x14`, `+0x20/+0x24` | three visual layers (map, metatiles) for BG2, BG1, BG0 (BG3 is the HUD) |
  | `+0x30` / `+0x34` | collision metatiles (type 6, 16 bytes each) / collision map (`u16 count`, 3 words, u16 cells) |
  | `+0x40` | pointer to `u32 size_class` (0/1/2) followed by a type-6 blob: a **spatial hash map** keyed by position, not an object list (see below) |
  | `+0x44` | **tile bank**: raw (type 0) header, then 4bpp 8x8 tiles; a metatile tile reference is an index into it |
  | `+0x48` | **background palettes**: 512 raw bytes = 16 BGR555 banks, copied to palette RAM (bank 0 is overwritten by the HUD) |

  Map blob = type 6 + Diff16: `u16 width, u16 height` in 32x32-pixel metatiles, then `width*height` u16 metatile ids.
  Metatile blob = type 6 (+Diff16): `u16 count, u16 0, u16 x, u16 x`, then `count` x 32 bytes of **16 u16 tile references**
  (a 4x4 block of 8x8 tiles, row-major), then `count` x 16 **attribute bytes** (bit 0 hflip, bit 1 vflip, bits 2-5 palette
  bank; the byte is shifted left 10 into the hardware screen entry). The game keeps a VRAM tile cache (allocator at
  `0x4FCD8`: cache table indexed by `ref & 0x7FF`, full reference compared; copy at `0x4FDA2`: source = bank + ref*32) and
  draws the map at `0x4FA38` with the cell at `map[(y>>5)*width + (x>>5)]` and the tile at entry `((y>>3)&3)*4 + ((x>>3)&3)`.
  There is no isometric geometry in the format; the isometric look is in the art.

  **The `+0x40` hash map.** The loader is called on the blob after the size word (`0x0803147C`); the decoded buffer is
  `2^k` buckets of 6 bytes (`k` = 8 + size class: 256/512/1024 buckets) followed by an overflow area (125/256/512
  entries), total 2286/4608/9216 bytes. Entry = `{u16 value, u16 next, u8 x, u8 y}`; bucket = `(251*x + 23*y) & (2^k-1)`
  (lookup routine `0x0805320C`, insert `0x0805332C`, `value |= bits` at `0x080534CE`); `next` chains into the overflow
  area (0 = end). Positions are in 16x16-pixel cells. The ROM ships static entries with value 2 (114 in district 62);
  at runtime the game inserts entries with value `0x20` and `0x100` for actors. Emptying the static entries changed
  nothing in the first frame or in the walk test, so their meaning is still open; NPC and object *placement* is not
  in the district record at all (see section 8).

  **Exact verification**: with the game paused in the first district, every visible cell of BG2, BG1 and BG0 (651 of 651
  each) matches the ROM record `0x748C8` in tile index and attribute at map tile origin (11,25) with scroll (90,205), the
  RAM copies of maps and metatiles are byte-identical to the decoded ROM blobs, and palette banks 1-15 equal the record's
  palette block. `urbz_level.py render rom.gba 0x748C8 out.png --crop 8 6 --origin 2 6` reproduces the screenshot in colour.
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
6. **District/level editor** — **done** (`editor/`, write-back in section 7): all 71 districts, three layers, collision,
   piece (metatile) redefinition from the tile bank, type-6 re-encoding, slot reclaim, UPS export. Art import replaces a piece's tiles from a PNG with a new palette bank. Remaining: sprite and HUD art (sprite
   directory + object palettes), palette editing by hand, and object placement.
7. **Sound replacement** — *weeks*. Custom driver, raw 8-bit PCM at `0x1100000+`; sample swaps are feasible once the
   sample table is found, music sequencing would need the driver reversed.

## 6. Debugger session (done, headless mGBA)

`emu/` holds a scripted-input harness, a watchpoint/breakpoint tracer and a loader oracle built on libmgba (no display
needed). The route from power-on to the first district is scripted (`probe10.txt`), memory was dumped in the district,
VRAM tiles were traced back to the bank copy routine, the map walker and cache allocator were disassembled with the live
register values, and the game's own loader was called on ROM blobs to prove the Python decoder byte-exact. One trap worth
recording: two adjacent records share most tile graphics at different indices, so matching VRAM tiles against the wrong
record's bank produced a convincing but false "remap"; the map/metatile RAM comparison settled which record was loaded.

## 7. Write-back (done, verified in the emulator)

The loader's type-1 branch passes the resource header itself to `swi 0x11`, and a resource header with type nibble 1
and no flags (`0x10 | size<<8`) *is* the BIOS LZ77 header. So any custom type-6 blob can be replaced by a plain BIOS LZ77
blob without a type-6 encoder: decode with `urbz_codec`, edit, `lz77_compress`, drop the blob in the zero tail
(`0x1FF8240+`), and rewrite the record pointer. The Diff16 flag must stay clear (`0x90` is not a BIOS header), so the
LZ77 payload is the unfiltered data. `urbz_patch.py` does this and emits a UPS patch (IPS offsets stop at 16 MB).

**Encoder (done).** `urbz_codec.encode_type6` is an optimal-parse (dynamic programming) encoder for the game's own
format, with the Diff16 pre-filter, so edited blobs can go back as type 6 (`0xE0`) at 101-106 % of EA's size instead of
LZ77 at 130-270 %. Three things had to be learned from the game itself, through the emulator oracle: (1) the IWRAM
decoder fetches the bitstream with **word loads**, so the dictionary must be padded to a multiple of 4 and blobs placed
4-byte aligned, otherwise the decoder reads rotated words and the game jumps into garbage (Python, reading bytes, was
happy with the misaligned stream); (2) the "literal fill byte" form (`f >= 0x20` plus 3 bits) is never used by EA's
data and the real decoder rejects it, so only dictionary fills are emitted; (3) EA's parameter words use escape widths of
0-2 bits (`n_c`) and 4-28-byte dictionaries, which is where most of the size difference came from. Eleven blobs in
every configuration now come back byte-identical from the game's decoder (`0x0801EC00` via `emu/oracle`).
`urbz_patch.encode_blob` / `UrbzCore.encodeBest` pick the smallest of type 6 (filtered or not), LZ77 + Diff16 and LZ77,
after decoding each candidate back.

**Free space (done).** Every district blob is referenced exactly once, so a redirected pointer frees its old bytes.
`urbz_patch.Patcher` and `UrbzCore.applyEdits` reclaim them (extent from the decoder's end position) and allocate
best-fit, largest first; the zero tail (32 KB) is only the fallback. A collision-map edit lands in its own old slot;
metatile blobs (about 7.3 KB at EA's size) overflow their slot by a few percent and go to the tail.

Proof of concept `urbz_patch.py demo`: a 2x5-metatile block at map cells (8..9, 7..11) of the first district, visual
metatile 0 on the ground layer (`map0`, 956 B → 824 B LZ77) and collision metatile 0 (`collmap`, 954 B → 284 B).
Verified with `emu/probe26_walltest.txt` on the original and the patched ROM:

- The RAM copies of the map (`0x0201CD20`) and collision map (`0x02026260`) equal the edited data in the patched run and
  the original data in the control run.
- Frame at the district's first visible frame: 4,564 pixels differ, all inside the block; `urbz_level.py render` of the
  patched ROM predicts the same picture.
- Behaviour: after the opening dialogue the Sim stands at world metatile (10, 8), immediately right of the block. Holding
  Left for 290 frames scrolls the original from `BG2HOFS=204` to `0` (the Sim crosses the map) and leaves the patched
  run at `204` for the whole time (blocked; the walking animation plays in place). Holding Down afterwards moves the Sim in
  both, so nothing is frozen. Collision byte `0x03` therefore blocks movement; the Sim walks on `0x40`/`0x43` cells.
- The UPS patch is 365 bytes with the type-6 encoder (1,416 with LZ77) and re-applies to the original dump byte-exactly.
- A metatile edit (all 16 tiles of piece 156 replaced through `UrbzCore.applyEdits`) shows up in the emulator frame at
  exactly the expected 32x32 screen rectangle, and the RAM copy of the metatile blob equals the edited data.
- **Art import (done).** EA's pipeline left nothing spare: in every district checked, every tile of the bank is used by a
  placed piece, every piece is placed, and the ROM has no blank region larger than the 32 KB tail, while tile banks are
  10-758 KB and are read straight from ROM by the tile cache (so they cannot be compressed or grown). New art therefore
  replaces an existing piece's tiles in place; `bestTargets` ranks pieces by how private their tiles are (district 62 has
  pieces with 16 exclusive tiles). A synthetic 8x-upscaled PNG imported through the editor (scale detection, magenta
  key, median-cut palette into unused bank 1, per-tile quantization, raw bank and palette written back into their own
  slots) renders in the emulator with 0 of 590 visible pixels off against the source. Palette banks 1 and 2 are unused
  in district 62; most districts use all 16.

Collision map layout, corrected: `u16 count` (number of collision metatiles, 74 here), `u16 0`, then `width*height` u16
metatile ids using the visual map's width and height (25x19); each collision metatile is 16 bytes = 4x4 cells of 8x8 px.

Scripted route past the opening dialogue (needed for any behavioural test): A at frame 3120 and 3300, then Down every 30
frames from 3500 to 4400 (long boxes scroll line by line with the D-pad and only close on A once fully shown), then A
every 60 frames from 4500; the district is under player control by frame 4600 (`BG2HOFS` leaves 0).

## 8. Remaining leads

0. **Objects and NPCs.** The district record has no placement list; the `+0x40` hash map only carries static value-2
   markers. Candidates: the per-district table at `0x08075124` (stride 16, three pointers, indexed by district; entries
   1-4 point into `0x080B85A4+`), the `0x9A2E4` table referenced from the save module, and the `0x90000-0x92000`
   dispatch tables. Trace: read watchpoint on the OAM shadow builder's source, or break on the sprite directory reader
   (`0x7A000` records) after a district load and walk back to the table that supplied the index.
1. Break on `0x0801EC00` (loader) with a save state in a district; log r0 (source) for every call → maps directory records
   to on-screen objects and finds the font. Then `watch/w 0x06000000` (charblock 0) after a district load to catch the
   tile-bank → VRAM copy: it tells which bank pages beyond the first 1024 tiles are used and when (the +0x20 table is the
  cache map), and a `watch/w 0x05000000` gives the background palette source.
2. Watchpoint `watch/w 0x05000200` → caller of the palette remap → palette source table.
3. Watchpoint on `0x030031C0`/`0x03003520` writes confirms the IWRAM decoder install at boot.
4. Dump SRAM after a save, diff with the `0x9A2E4` template.

## Files

- `urbz_codec.py` — decoders for header types 0-4 and 6 (`decode(rom, offset)`) and the type-6 encoder (`encode_type6(raw, filtered)`, `filter16`), CLI: `urbz_codec.py rom.gba 0xA054D4 out.bin`
- `urbz_dump.py` — directory parser and exporter (`list`, `png`, `raw`)
- `urbz_level.py` — level record parser and layer renderer (`list`, `render --layer N --crop --origin --zoom`)
- `editor/` — browser district editor (`index.html` + `urbz-core.js`): renders any of the 71 districts, paints pieces and collision, redefines pieces tile by tile, re-encodes with the type-6 encoder, reclaims replaced blobs, exports UPS; `test/core_test.js` and `test/ui_test.js` check it against the Python tools and in headless Chromium
- `urbz_patch.py` — write-back: `demo` (the wall proof of concept), `replace rom out record field raw.bin`, `--ups out.ups`, `ups-apply`
- `emu/probe26_walltest.txt` — harness script: power-on state → district → through the dialogue → walk Left and Down with register dumps
