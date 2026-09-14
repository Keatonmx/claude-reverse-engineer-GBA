# Case study: The Urbz: Sims in the City (USA/Europe) — a game with no BIOS compression and no community research

Klonoa (`klonoa-case-study.md`) is the textbook case: BIOS LZ77/Huffman, a level table with regular stride, a level
buffer in WRAM. This second case study records what changed when the same loop was applied to a game that uses its own
compressor, stores nothing in plain text, and had no prior research to lean on. Every address below belongs to the dump
`THE URBZ AGB`, game code `BOCE`, SHA-1 `8efd27375d1f92b43fe0d1c93a63c08b7a259acc`, 32 MB, and is a ROM file offset
unless written as a bus address. The full record, tools and hack roadmap are in `projects/urbz/` of the repository this
skill ships with.

## Contents

1. How the survey was ordered (static first, then one emulator session)
2. The resource loader: a dispatcher on a header nibble, with decoders living in IWRAM
3. Translating a custom decoder and proving it byte-exact with the game's own code
4. The district format: structure-of-arrays metatiles, a tile cache, a raw palette
5. Verification method (pixel-exact against a running frame)
6. Traps that produced convincing wrong answers
7. Write-back without an encoder for the custom format
8. Findings table

## 1. Order of work

The environment had no display, so the loop in `SKILL.md` was run in two passes:

1. **Static pass (hours).** `gba_rom.py header`; map the ROM by block statistics (share of words that are ROM pointers,
   share of halfwords that decode as Thumb `bl`, byte entropy) → code / tables / compressed assets / PCM / padding.
   Locate BIOS wrapper stubs (`swi N; bx lr`) and walk their callers to the resource loader. Disassemble the loader,
   translate the custom decoder in Python, scan pointer-dense regions for regular records.
2. **Emulator pass (one session, headless mGBA, `headless-emulation.md`).** Script inputs from power-on to the first
   district, save a state, dump RAM/VRAM/palette/IO, put write watchpoints on VRAM and palette RAM, read the copy
   routine's registers, call the game's loader as an oracle, and compare the ROM decode against the live frame.

The static pass produced every hypothesis; the emulator pass confirmed most of them and overturned one (section 6).
Do not skip the second pass because the first "looks right".

## 2. The loader

`gba_compress.py scan` found only a handful of valid BIOS-format blocks in a 32 MB ROM full of high-entropy data; that is
the signal for a custom compressor. Rather than guess the format, find the loader:

- BIOS wrapper stubs sit together at `0x6B368-0x6B3A0` (`CpuSet`, `LZ77UnCompWram/Vram`, `HuffUnComp`, `RLUnComp...`).
  `gba_rom.py pointers` on each stub gives its callers; the routine that calls *several* of them is the loader.
- Loader at `0x0801EC00` (WRAM destination) with a twin at `0x0801ED44` (VRAM destination, uses the `Vram` SWIs).
  Every resource starts with a 4-byte header: `byte0 = type<<4 | flags`, bytes 1-3 = decompressed size, 24-bit LE.
  The loader masks the type nibble and jumps through a table at `0x1EC2C`:

| Type | Handler |
|---|---|
| 0 | `swi 0x0B` CpuSet, raw copy |
| 1 / 2 / 3 | BIOS LZ77 / Huffman / RLE |
| 4 | custom decoder A, called through a function pointer at IWRAM `0x030031C0`; its 528 bytes are copied from ROM `0xD274` to `0x03003530` at boot |
| 6 | custom decoder B, function pointer at `0x03003520`; 848 bytes copied from ROM `0x2EC` to `0x030031D0` |
| header byte bit 7 (`0xE0` = type 6 + filter) | post-filter at `0x0801EE00`: 16-bit running sum (Diff16 unfilter) over the output |

Two lessons generalise. First, a decoder that runs from IWRAM is invisible to a static pointer search of the ROM: the
call is `ldr r3,[pool]; bx r3` through a RAM address, so find the *install* (a copy loop from ROM to `0x03xxxxxx` at
boot) to know which ROM bytes the code came from. Second, the same header nibble scheme means a hacker can re-encode any
asset with a BIOS format the loader already accepts (change the nibble from 6 to 1 and store LZ77), which removes the
need to write an encoder for the custom format before shipping a first patch.

## 3. Translating and proving the decoder

Decoder B (type 6) is a bit-packed LZ variant: a 4-byte parameter word (dictionary length, escape value, extra distance
bits, literal split), a dictionary of up to 31 bytes, then an MSB-first bitstream read from 32-bit little-endian words
with a sentinel bit marking the end of each word. Tokens are literals split into "high bits compared against an
adaptive escape" and "low bits"; an escape followed by an Elias-gamma code selects long match / short match with 8-bit
distance / literal equal to the escape (which then changes) / fill run. Output is written as halfwords, so it is VRAM-safe.

Translation habits that paid off:

- Translate the disassembly line by line into Python first, preserving register names as variables; only refactor once
  it decodes something to its declared size.
- Two bugs survived the "decodes to declared size" test: a conditional branch read backwards (`blo` is *carry clear*, so
  the selector bits were inverted) and the filter flag taken from the wrong bit (`0x08` instead of `0x80`). Neither
  shows up as a size mismatch. Only comparison against the real decoder catches them.
- **The oracle.** With a save state loaded, hijack the CPU: set `r0` = ROM blob, `r1` = a scratch WRAM address, `lr` = a
  return address in ROM, `pc` = the loader, run until `pc` reaches the return address, dump the destination, compare
  with the Python output. `scripts/emu/oracle.c` does this generically. After that, the decoder was byte-identical on every
  blob tried, including 16.9 KB filtered map blobs, and "decodes to declared size on all 7,431 type-6 resources in the
  directory" became a statement about the format rather than about the parser.

## 4. The district format

There is no per-level table with a fixed stride. The district records were found by **signature scan** over the
table region (`0x73000-0x7A000`, every 4 bytes): a candidate is an 80-byte record whose pointer fields each land on a
resource header of the right type (layer 1 present; layers 2 and 3 may be null; the tile bank must be raw type 0).
Thirty-three records match. Layout, verified in the emulator:

| Offset | Content |
|---|---|
| `+0x00/+0x04`, `+0x10/+0x14`, `+0x20/+0x24` | (map, metatiles) for BG2, BG1, BG0. BG3 is the HUD |
| `+0x30` / `+0x34` | collision metatiles (type 6, 16 bytes each) / collision map |
| `+0x40` | object list (4 zero bytes, then type-6 blobs of 6-byte records) |
| `+0x44` | tile bank: raw header, then 4bpp 8x8 tiles |
| `+0x48` | 512 raw bytes = 16 BGR555 palette banks (bank 0 later overwritten by the HUD) |

- **Map** blob (type 6 + Diff16): `u16 width, u16 height` in 32x32-pixel metatiles, then `width*height` u16 metatile ids.
- **Metatile** blob: `u16 count, u16 0, u16 x, u16 x`, then `count` x 32 bytes of 16 u16 tile references (a 4x4 block of
  8x8 tiles, row-major), **then** `count` x 16 attribute bytes (bit 0 hflip, bit 1 vflip, bits 2-5 palette bank). This is
  structure-of-arrays: all references first, all attributes after. An array-of-structs reading (32+16 bytes per metatile)
  parses without error and renders plausible garbage.
- Tile references index the bank directly. The game does not upload the bank to VRAM; it keeps a **tile cache**:
  allocator at `0x4FCD8` (cache table indexed by `ref & 0x7FF`, full reference compared on hit), copy at `0x4FDA2`
  (source = bank + 4 + ref*32), map walker at `0x4FA38` (cell = `map[(y>>5)*width + (x>>5)]`, tile =
  `entry[((y>>3)&3)*4 + ((x>>3)&3)]`, screen entry = cached VRAM tile | `attr << 10`). So the VRAM tile numbers seen in
  the tilemap viewer are *cache slots*, not asset indices; the map ↔ VRAM correspondence only exists through the cache.
- Runtime copies (IWRAM): `0x03006180 + layer*4` map pointer, `+0xC + layer*4` metatile pointer, `0x030061A0 + layer*4`
  attribute-table offset, `0x03005FDC` width, `0x03005FC0 + layer*4` the BGxCNT value written for that layer.
- The picture looks isometric; the data is an orthogonal grid of 32x32 metatiles. The diagonal look is painted into the
  tiles. Do not model geometry the data does not have.

## 5. Verification method

"It renders something that looks like the screenshot" is not verification; self-similar pavement tiles make many wrong
layouts look right. The check that was accepted:

1. Pause in the district (save state). Dump VRAM, palette RAM, IWRAM, and the true I/O registers (`gba->memory.io`,
   not the memory-block API) for BGxCNT / BGxHOFS / BGxVOFS.
2. From BGxCNT take screenblock and charblock; from HOFS/VOFS take the scroll; compute for every visible screen entry the
   world tile coordinate, the map cell, the metatile entry and the expected attribute.
3. Resolve the expected *asset* tile through the cache table (or compare pixel data of the VRAM tile with the bank tile).
4. Count matches: 651 of 651 visible cells per layer for BG2, BG1 and BG0, plus the RAM copies of maps and metatiles
   byte-identical to the decoded ROM blobs, plus palette banks 1-15 equal to the record's palette block.

Only then was the record layout written down as confirmed and the renderer's output trusted for records that were never
loaded in the emulator.

## 6. Traps that produced convincing wrong answers

- **Shared graphics between adjacent records.** The VRAM tiles matched the tile bank of record `0x748F0` at different
  indices, which looked like an index remap table. The loaded record was actually `0x748C8`; the two share most tiles.
  Comparing the RAM copies of the map and metatiles against each candidate's decoded blobs settled it in one step.
  Lesson: identify the loaded record by its *unique* data (maps), not by its most-shared data (tiles).
- **Layout off by one field group.** A first record layout put the layers 0x28 bytes away from their bank because the
  guess came from a fixed-grid assumption. The signature scan replaced the grid.
- **Static plausibility metrics.** Edge-continuity and transparency scores were used to choose between candidate
  geometries. They favoured a wrong layout because pavement tiles continue into almost anything. Emulator evidence
  replaced them.
- **Precedence in Python.** `x >> 4 & 7 == 6` parses as `x >> (4 & (7 == 6))`. Parenthesise field extraction.
- **The right decoder for the wrong bit.** See section 3; the size check does not catch inverted selectors.
- **Hijacking the CPU in the wrong state.** `ARMWritePC` from Thumb code, or forcing a mode/IRQ flag and stepping
  immediately after `loadState`, crashed the emulator. Use `ThumbWritePC`, re-prime the pipeline, and step until the
  PC is in ROM in System mode before writing registers (`headless-emulation.md`).

## 7. Write-back without an encoder for the custom format

The type-6 format has no encoder, and writing one is days of work. The dispatcher made it unnecessary: the type-1 branch
passes the resource header straight to `swi 0x11`, and `0x10 | size<<8` is exactly the BIOS LZ77 header. So an edited
asset is stored as BIOS LZ77 in the zero tail of the ROM (32 KB at `0x1FF823D+`), the record pointer is redirected, and
the game decodes it with the BIOS. Two details: the Diff16 flag (bit 7) must stay clear, so the payload is the unfiltered
data; and IPS cannot express offsets past 16 MB, so the patch ships as UPS (`scripts/gba_patchfile.py`).

The proof was a 2x5-metatile block next to the start position, edited in the ground map and the collision map of the
first district. Three independent confirmations, in increasing strength:

1. The RAM copies of both blobs equal the edited data after the district loads (and the original data in a control run).
2. The first district frame differs from the control in 4,564 pixels, all inside the block, and the ROM renderer
   predicts the same picture from the patched ROM.
3. Behaviour: with the same scripted input, holding Left for 290 frames moves the control run's camera from `BG2HOFS=204`
   to `0` while the patched run stays at `204` (the Sim walks in place against the block); holding Down afterwards moves
   both. That also settles the collision semantics: byte `0x03` blocks, the Sim walks on `0x40`/`0x43`.

**Writing the encoder afterwards.** Once edits grew to metatile blobs (17 KB decoded), LZ77 no longer fit and a real
type-6 encoder was written: an optimal parse over the token costs (dynamic programming), a dictionary of run bytes,
and a search over the escape-width parameter. Three facts came only from the game's own decoder, through the oracle:
the IWRAM decoder loads the bitstream as 32-bit words, so the dictionary must be padded to a multiple of 4 and the blob
placed word-aligned (a byte-oriented Python decoder happily reads the misaligned stream, so the round-trip test passed
while the game crashed); one branch of the translated decoder (the non-dictionary fill byte) is never exercised by
shipped data and is wrong in the translation, so the encoder avoids it; and EA's parameter choices (0-2 escape bits,
small dictionaries) account for most of the size gap. Token-path statistics over the shipped blobs (which branches ever
run, maximum values) told which parts of the translation were verified by data and which were not. The final encoder is
within 1-6 % of EA's output and every variant decodes byte-identically in the game.

**"Object list" that is a hash map.** The record field that looked like an object list decodes to 6-byte entries
`{u16 value, u16 next, u8 x, u8 y}`. Read watchpoints on the decoded buffer led to a lookup routine computing
`(251*x + 23*y) & (buckets-1)` and following `next` into an overflow area: a spatial hash keyed by position, sized by
the header word (256/512/1024 buckets). The ROM entries are static markers; the game inserts actors at runtime. The
tell-tale signs were a fixed set of three total sizes independent of map size, mostly-zero rows, and a `next` field of
small integers.

The behavioural test cost more than the patch: the district opens with a scripted dialogue whose long boxes scroll line
by line with the D-pad and only close on A once fully shown. Every input cadence that ignored this looked like "the game
hung". Record the exact route through such dialogues once and keep the script with the project.

## 8. Findings table

| Item | Value | Evidence |
|---|---|---|
| Loader (WRAM / VRAM) | `0x0801EC00` / `0x0801ED44` | callers of the BIOS stubs at `0x6B368-0x6B3A0`; dispatch table `0x1EC2C` |
| Custom decoders | type 4: ROM `0xD274` → IWRAM `0x03003530`, ptr `0x030031C0`; type 6: ROM `0x2EC` → `0x030031D0`, ptr `0x03003520` | init copy at `0x1ECD0`; `bx r3` thunk at `0x6BD20` |
| Post-filter | `0x0801EE00`, header bit 7, Diff16 running sum | disassembly; byte-exact oracle on filtered blobs |
| Resource directory | 7,682 x 16-byte `{count, ptr0, ptr1, ptr2}` at `0x7A000-0x7F000`, `0x9A000-0xB5000` | pointer-density scan; all slot-0 blobs decode to declared size |
| District records | 33 x 80 bytes in `0x73000-0x7A000`, layout in section 4 | signature scan; 651/651 cells per BG in the emulator |
| Tile cache | allocator `0x4FCD8`, copy `0x4FDA2`, map walker `0x4FA38` | VRAM write watchpoint → register dump → disassembly |
| Runtime level struct | `0x03006180` (maps), `+0xC` (metatiles), `0x030061A0` (attr offsets), `0x03005FDC` (width), `0x03005FC0` (BGxCNT) | IWRAM dump vs decoded blobs |
| Palettes | record `+0x48`, 512 raw bytes; OBJ palettes pass through a remap LUT routine at `0x08015E58` | palette RAM dump; literal `0x05000200` callers |
| Save signature | `URBZ0011` at `0x9A2CC`; save module `0x4C900-0x4DB00` | string + pointer search |
| Free space | `0x1FF823D-0x1FFFFFF` (32,195 bytes) | `gba_rom.py freespace` |
| Write-back | type-6 blob → BIOS LZ77 blob (type nibble 1, no filter flag) in the tail, pointer redirected, UPS patch | RAM copies, frame diff and blocked movement in the emulator |
| Collision | map `u16 count, u16 0, w*h u16 ids`; metatile 16 bytes = 4x4 cells of 8 px; `0x03` blocks, `0x40`/`0x43` walkable | wall test |
| District table | 71 records, `0x73568 + index*0x50`; index at IWRAM `0x030048F8` | code at `0x08031BC0`; all 71 decode |
| `+0x40` field | size word + spatial hash map `{value, next, x, y}`, bucket `(251x+23y) & mask` | read watchpoints → `0x0805320C` |
| Type-6 encoder | optimal parse, Diff16, word-aligned dictionary, dictionary fills only; 101-106 % of EA's size | 11 variants byte-identical through the game's decoder |
| Open | text/font encoding (no ASCII in the ROM), object placement list semantics, save layout; a type-6 encoder only if the tail free space runs out | — |

## What generalises

- Block statistics (pointer share, `bl` share, entropy) map an unknown ROM in minutes and tell you where to scan.
- BIOS wrapper stubs are the handle on any loader, even one that mostly uses custom decoders.
- A header nibble that selects a decoder is common in licensed titles of this era; the BIOS branches of that table are
  a free write-back path.
- Custom decoders are proven by calling the game's own routine on the same input, never by eyeballing output size.
- Level formats built on metatiles + a VRAM tile cache break the naive "VRAM tile index = asset tile index" assumption;
  resolve through the cache or compare pixels.
- Signature scans beat stride guesses when records are not in a table.
- Verify against the running game before trusting any renderer, and identify what is loaded by its least-shared data.
- A type-dispatching loader is a free write-back path: store edits in the BIOS format it already accepts.
- An encoder for a custom format is proven only by the game's decoder: a byte-oriented re-implementation hides
  alignment rules and never-exercised branches. Collect token-path statistics over shipped data to see which branches
  the translation has actually verified.
- A record field that decodes to sparse fixed-size rows with a small integer link field is a hash map, not a list.
- Prove a patch three ways (RAM copy, frame, behaviour) with the control ROM run through the identical input script.
- An editor is a port of the verified decoders and the write-back into one browser page (klo-gba.js pattern): keep the
  logic in a module that also loads in Node, and test it against the Python tools (same patch bytes, same pixels) and
  the UI in headless Chromium before shipping. The Urbz editor took a few hours once the format was proven.
