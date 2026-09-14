---
name: gba-reverse-engineering
description: Reverse engineer Game Boy Advance games and build ROM hacks, level editors, or asset extractors from them. Use this whenever the user mentions a GBA ROM, .gba file, mGBA/No$GBA/VBA debugging, Ghidra or IDA on ARM7TDMI/Thumb code, pulling or decompiling the code out of a GBA game, GBA decomp projects, GBA tilemaps/tilesets/palettes/OAM/VRAM/DMA, BIOS decompression (LZ77 swi 0x11, Huffman, RLE), finding where a level or sprite lives in a ROM, patching or hooking GBA assembly, or wants a "level editor" / "ROM hack" / "extract the maps" for any GBA title, even if they never say "reverse engineering". Also use it when a game uses a custom compressor, when the game must be run headless (libmgba, no display) to dump memory or trace VRAM writes, and for general questions about how GBA games store and load data.
---

# GBA reverse engineering

This skill packages the method Bruno Macabeus documented while building klo-gba.js, a level editor for
*Klonoa: Empire of Dreams* ("Reverse engineering a GameBoy Advance game — Complete Guide"), generalised to any GBA
title, plus scripts for the recurring chores (decompression, pointer hunting, hand-assembling patches, rendering tiles),
and a second worked example (The Urbz) where the game uses its own compressor and a VRAM tile cache.

Nothing about a specific game is documented, but the *platform* is, exhaustively. Every step below turns a hardware
fact (how VRAM, DMA, the BIOS and the cartridge bus work) into a question a debugger can answer. That is why an
obscure game with zero community research is still tractable.

## The loop

Work from what you can see toward what is stored, one hop at a time, and write down every address as you confirm it.

1. **Pick one visible, testable objective.** "Stretch this bridge", "move this enemy", "change this palette". It gives
   you a tile to point at and a pass/fail check at every step. Vague goals ("understand the level format") stall.
2. **Identify the ROM.** `scripts/gba_rom.py header rom.gba` → title, game code, region, SHA-1. Every address you
   find belongs to this exact dump. Save states at the frame of interest before experimenting.
3. **Find the on-screen copy (VRAM).** Emulator tile/map viewer: which BG layer, tile index, map address. Edit it in the
   memory viewer; it usually reverts next frame, which is your first clue that a copy routine exists.
4. **Follow the writer (DMA or CPU copy) to WRAM.** Write watchpoint on the VRAM entry. If the PC is in a loop that
   writes the DMA registers (`0400_00Bx`), read the DMA source register; otherwise read the load feeding the store.
   If the watchpoint never fires (mGBA only traps CPU accesses), watch the DMA control register instead.
   Edit the WRAM copy: if the change now persists you have the working level buffer.
5. **Check the gameplay data, not only the picture.** Collision, physics, and spawns may use another structure.
   Find the variable (memory search for the player's Y while falling), watch writes to it, read the routine in the
   disassembler, and locate the table it consults. Patch RAM to prove the finding before touching the ROM.
6. **Catch the load: WRAM ← decompressor ← ROM.** Watch writes to the WRAM buffer while reloading the level. You land in a
   BIOS SWI (0x11/0x12 LZ77, 0x13 Huffman, 0x14/0x15 RLE) or a custom routine; r0 is the ROM source, r1 the destination.
   `scripts/gba_compress.py info/decompress --chain` confirms the format and extracts the data.
7. **Find the tables.** `scripts/gba_rom.py pointers rom.gba <asset>` finds who references it; a hit in a regular
   stride is the per-level table. Dump siblings with `gba_rom.py table`, pull width/height, tileset, palette, objects
   the same way. Fold the flat map with `scripts/render_tiles.py` to sanity-check widths visually.
8. **Decode structures by perturbation.** Change one field (in RAM or ROM), reload, observe. Record layouts in a table
   with offsets, types and the evidence.
9. **Write back without breaking neighbours.** Recompressed data grows. Put it in free space
   (`gba_rom.py freespace`), redirect the pointer, or hook the loader (`scripts/thumb_patch.py hook`). If the game's
   loader dispatches on a type byte, re-encode in a BIOS format it already accepts instead of writing a custom encoder.
   Re-execute displaced instructions, return with `bx lr`, stamp the ROM so tools can recognise it, test the levels before
   and after, and prove the edit in the emulator (RAM copy equals the edit, the frame changes, the behaviour changes).
10. **Ship a tool or a patch, not a ROM.** Editors take the user's own dump and verify its hash; distribute
    IPS/UPS/BPS diffs (`scripts/gba_patchfile.py`; UPS or BPS when the ROM is over 16 MB).

When a step is ambiguous, the cheaper experiment wins: a memory search or a RAM edit costs seconds, a full static
analysis costs hours. Static analysis (Ghidra/IDA) is for explaining a routine the debugger already led you to. Before
any of it, check whether a matching decompilation project already exists for the game; if it does, work in its C source
and use the emulator only to locate what to change (`references/code-analysis.md`).

## Reading the references

Load the one that matches the current step; each is self-contained.

| When you need | Read |
|---|---|
| To interpret an address, register, tile, OAM entry, DMA channel, or SWI number | `references/gba-hardware.md` |
| The step-by-step debugger method, emulator breakpoint syntax, Ghidra/IDA setup | `references/debugging-workflow.md` |
| Compressed data: headers, LZ77/Huffman/RLE bit layouts, chained formats, scanning a ROM | `references/compression.md` |
| To read or change the *code*: Ghidra setup, mGBA GDB bridge, literal pools, Thumb/ARM mode errors, decomp projects | `references/code-analysis.md` |
| To modify the ROM: free space, pointer redirects, `bl` hooks, Thumb→ARM stubs, encodings | `references/patching.md` |
| To run the game with no display: build libmgba, script inputs, dump memory/IO, trace VRAM writes, call the game's own decoder as an oracle | `references/headless-emulation.md` |
| A complete worked example with every address, table, struct and the loader patch (Klonoa) | `references/klonoa-case-study.md` |
| A second worked example: custom IWRAM decoders, a header-nibble dispatcher, structure-of-arrays metatiles, a tile cache, pixel-exact verification (The Urbz) | `references/urbz-case-study.md` |

## Scripts (Python 3, no dependencies)

Run `python3 scripts/test_scripts.py` once if in doubt; the vectors include the real Klonoa patch bytes.

| Script | Does |
|---|---|
| `scripts/gba_compress.py` | `info`, `decompress [--chain]`, `compress --type lz77,huffman [--vram]`, `scan` a ROM for compressed blocks. Importable: `lz77_decompress`, `huffman_compress`, ... |
| `scripts/gba_rom.py` | `header`, `pointers` (who references an offset), `table` (dump fixed-stride records with typed fields), `freespace`, `dump`, `u16/u32` |
| `scripts/thumb_patch.py` | Encode Thumb `bl`/`b`, ARM `b`, pc-relative `ldr` for both states, the Thumb→ARM stub, and a full `hook` recipe with file offsets |
| `scripts/gba_patchfile.py` | `make`/`apply` IPS and UPS patch files with CRC checks; UPS for ROMs over 16 MB |
| `scripts/render_tiles.py` | Render 4bpp/8bpp tiles + BGR555 palette to PNG: tileset sheets, byte-indexed or screen-entry tilemaps, palette swatches |
| `scripts/emu/` | Headless mGBA tools (C, built by `build.sh` against libmgba): `harness` runs scripted input and dumps RAM/VRAM/palette/OAM/IO, `trace` logs watchpoint/breakpoint hits with registers, `oracle` calls a ROM routine on chosen inputs, `topng.py` renders a dumped frame |

All scripts accept bus addresses (`0x081B27FC`) or file offsets (`0x1B27FC`).

## Deliverable: a findings document

Reverse engineering that is not written down gets redone. Keep `FINDINGS.md` (or the user's chosen file) current
with this shape, and fill it as you go rather than at the end:

```
# <Game> (<region>) — SHA-1 <hash>
## Runtime addresses        | address | meaning | how found (watchpoint/search) |
## ROM tables               | table | offset | stride | index formula | entry layout |
## Assets per level         | level | tilemap | tileset | palette | objects | size |
## Structures               | struct name | offset | type | field | evidence |
## Compression              | asset | layers (e.g. huffman > lz77) | prefix bytes |
## Patches                  | offset | original bytes | new bytes | purpose |
## Open questions
```

Cite evidence for every row (the breakpoint that fired, the value that changed). Distinguish *confirmed* from *guessed*.

## Pitfalls that cost the original author days

- Editing VRAM changes one frame; the source of truth is upstream. Editing the visual map does not change collision.
- Bus address `0x08xxxxxx` vs file offset: subtract `0x08000000` before touching the file. Pointer tables store bus addresses.
- Compressed blocks may sit a few bytes after the pointer target (Klonoa: +4), and the decoded data may carry its own
  small header (Klonoa: 4 bytes) before the payload.
- Chained compression: decode Huffman then LZ77; encode LZ77 then Huffman. The bundled `--chain` peels layers automatically.
- Saving a bigger asset in place corrupts the next asset; the failure shows up in a *different* level.
- IPS offsets are 24-bit: an edit past 16 MB silently cannot be expressed. Use UPS/BPS for 32 MB ROMs.
- Scripted behavioural tests fail on the first blocking dialogue, not on your patch. Record the exact input route through
  menus and dialogues (long boxes may scroll with the D-pad before A closes them) and run the control ROM through the
  same script.
- Thumb `bl` clobbers `lr`; pc reads +4 in Thumb and +8 in ARM; ARM code must be word aligned; `bx` needs bit 0 set for Thumb targets.
- Disassembly that reads as nonsense is almost always the wrong mode (set Thumb) or a literal pool (data after the function), not encryption.
- Region/revision moves every address. Pin the dump by SHA-1 and say so in the deliverable.
- A custom decoder that runs from IWRAM is invisible to ROM pointer searches; find the boot-time copy loop to learn which
  ROM bytes it came from, and prove your translation by calling the game's routine on the same input (the oracle pattern).
  "Decodes to the declared size" does not catch an inverted branch condition or a wrong flag bit.
- Games with a VRAM tile cache break "VRAM tile index = asset tile index"; resolve through the cache table or compare pixels.
- Metatile tables may be structure-of-arrays (all tile refs, then all attributes); an array-of-structs reading parses fine
  and renders plausible garbage. Verify against the running frame, cell by cell, before trusting a renderer.
- Adjacent records often share graphics. Identify the loaded record by its least-shared data (the map), not by its tiles,
  or you will invent a remap table that does not exist.
- Static plausibility scores (edge continuity, transparency) are fooled by self-similar tiles. Emulator evidence wins.
- An isometric look does not imply isometric data; check the map walker before modelling geometry.
- When hijacking the CPU in libmgba use `ThumbWritePC` for Thumb targets, re-prime the pipeline, and step to ROM code in
  System mode first; `ARMWritePC` from Thumb or writing registers mid-BIOS crashes the core.
- Legal and practical: work on the user's own dump, never distribute ROMs, ship patches or an editor. Buying the game
  keeps the franchise alive, which is why editors like klo-gba.js ask for it.

## Attribution

Method and case study from Bruno Macabeus's series (Medium: "Reverse engineering a GameBoy Advance game — Complete Guide",
Parts 1-8, Introduction and Final Part) and the MIT-licensed `macabeus/klo-gba.js`; compression tools in the original
project were CUE's LZSS/Huffman encoders, reimplemented here in Python. Hardware facts follow GBATEK and Tonc. The Urbz
case study and the headless tools come from applying this skill in the repository's `projects/urbz/`; the emulator is mGBA (MPL 2.0).
