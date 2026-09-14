# Case study: Klonoa: Empire of Dreams (USA) — what a finished reverse-engineering spec looks like

Source: Bruno Macabeus, "Reverse engineering a GameBoy Advance game — Complete Guide" (Medium, 2019-2024, Parts 1-8
plus introduction and final part) and the resulting open-source editor `macabeus/klo-gba.js` (`scissors/` extracts data,
`brush/` is the React/PixiJS UI with an embedded `react-gbajs` emulator). All offsets are ROM file offsets for the USA
dump, SHA-1 `a0a298d9dba1ba15d04a42fc2eb35893d1a9569b` (EU `e4a81713…903ef` and JP `f46410ec…983b05` are not supported).
Use this as a template for the level of detail a "findings" document should reach, and as a worked example of the
method in `debugging-workflow.md`.

## The story in one paragraph per part

- **Introduction.** Goal: a level editor for a game with no existing research or tooling. Tools: No$GBA for dynamic
  analysis, IDA for static. The trick that makes an obscure game tractable: the *platform* is documented even when the
  game is not, so every step leans on GBA manuals and debuggers rather than game-specific knowledge.
- **Part 1, "Let's stretch the bridge".** A concrete, visible objective. In the VRAM viewer (F5) the level's walkable
  tiles live on BG2; hovering shows each tile's ID (the bridge's top-left corner is tile `0x9B`) and its map address.
  Editing VRAM stretches the bridge on screen, for one frame at a time, and Klonoa falls through it.
- **Part 2, "the mischievous DMA".** A write breakpoint on the VRAM entry lands in a DMA transfer that copies the
  visible window every frame from IWRAM (`0300_4DB0`; the bridge tile at `0300_45FD`). Lesson: IWRAM is where
  per-frame data is staged; the full level lives in a larger buffer and only the needed rows are DMA'd into VRAM, which
  is why scrolling never lags. Editing the IWRAM copy makes the visual change persist.
- **Part 3, "understanding the game physics".** The stretched bridge is drawn but not solid. Find Klonoa's Y position
  (watch it change while falling), break on writes, walk into the routine that stops the fall, and find the collision
  lookup it performs against the level structure. The collision data and the visual tilemap are read from the same
  level buffer, so the real target is the full tilemap in WRAM, not VRAM.
- **Part 4, "where is the tilemap in the ROM?".** Write breakpoint on the WRAM tilemap during level load leads into the
  BIOS: `swi 0x11` (LZ77UnCompWram), r0 = `0x081B27FC`. The block there is Huffman-wrapped LZ77. IDA's hex view shows
  how far the data item extends. CUE's LZSS/Huffman C tools are compiled to WebAssembly so the browser can decode and
  re-encode.
- **Part 5, "let there be tilemap".** The decoded vector (25203 bytes for vision 1-1, first 4 bytes are metadata) has
  to be folded into a matrix; the width/height come from a per-vision table. Each vision has several stages but one
  shared tilemap. The tileset (8bpp 8x8 tiles) and palette (BGR555) come from their own per-vision pointer tables,
  also Huffman+LZ77 compressed with a 4-byte prefix, and render the map in the browser.
- **Part 6, "extracting the objects".** Objects (enemies, dream stones, keys, doors) come from a per-vision "ROM OAM"
  table of 44-byte records; the runtime ("global") OAM only holds the objects of the current stage. Portals are 8-byte
  records. Sprites and palettes for objects are found through further pointer tables.
- **Part 7, "let's paint our website".** Building the editor: one WebGL (PixiJS) component instead of one React
  component per tile, an embedded emulator (react-gbajs) with save states that jump straight into a vision, freeze-address
  hacks (infinite hearts/lives). Saving the recompressed level in place worked for level N and crashed level N+1
  because the new blob was larger.
- **Part 8, "saving the custom level".** Store custom tilemaps in the empty space at the end of the ROM and patch the
  loader with a `bl` hook into a Thumb→ARM stub that swaps the tilemap address when a custom one exists; generate the
  patch bytes from JS so it scales to every level.
- **Final part.** Keep a clear objective ("stretch the bridge") to measure progress; ship small increments to the
  community and use their feedback; talks and write-ups force you to understand what you did.

## Findings table (the deliverable format)

### ROM identification
| Item | Value |
|---|---|
| Game code / title | `AKEE` / KLONOA (USA) |
| SHA-1 | `a0a298d9dba1ba15d04a42fc2eb35893d1a9569b` |
| Custom-ROM marker | bytes `42 30` (`add r0, #0x42`) at `0x367606` |

### Per-vision asset tables (index = `(world-1)*9 + (vision-1)`, 9 visions per world)
| Table | Offset | Stride | Entry |
|---|---|---|---|
| Vision size | `0x051C80` | 6 | `u16 width` at +0; `u16 height` at +0x142 from the width word (separate parallel array) |
| Palette pointer | `0x188F60` | 4 | bus pointer; data = pointer+4, Huffman>LZ77, skip 4 decoded bytes, then BGR555 colours |
| Tileset pointer | `0x189048` | 12 | bus pointer; data = pointer+4, Huffman>LZ77, skip 4, then 64-byte 8bpp tiles |
| Object sprite table pointer | `0x189A28` | 4 | pointer to records `{u32 animations_ptr, u32 ?, u16 tileset_len, u16 ?}`; animations → first frame → tileset pointer |
| Object OAM pointer table | `0x18B8E4` | 4 | pointer to `{u32 ?, u32 oam_ptr}` records; OAM record `{u16 vram_tile, u8 palette, s8 rel_x, s8 rel_y}` |
| Global object palettes | `0x077E28` | 32 | 141 palettes of 16 BGR555 colours; per-vision palette index lists are hard-coded in `getVisionObjectPalettes.js` |
| Global sprite blobs | `0x05F508` box (512 B), `0x05F708` red key (128), `0x05F788` blue key (128), `0x05F808` door (512); heart/dream stone/large dream stone/star/goomi via pointers at `0x18B9D8`, `0x18B9B8`, `0x18B9C8`, `0x18B9E8`, `0x054B04` |

### Vision data (world 1, Ghazzaland)
| Vision | Tilemap (compressed) | Custom slot | Objects [start, end) | Portals [start, end) | Stages |
|---|---|---|---|---|---|
| 1-1 | `0x1B27FC` | `0x367700` | `0xE2B90-0xE2F59` | `0xD48C8-0xD48EF` | 3 |
| 1-2 | `0x1B3E5C` | `0x3686B0` | `0xE3CC0-0xE3FAC` | `0xD4970-0xD49A0` | 3 |
| 1-3 | `0x1B50AC` | `0x369380` | `0xE4DF0-0xE5109` | `0xD4A18-0xD4A5F` | 5 |
| 1-5 | `0x1B8A28` | `0x36A250` | `0xE7050-0xE7579` | `0xD4B68-0xD4BAF` | 4 |

Tilemap encoding: `Huffman(LZ77(4 metadata bytes + width*height tile indices, one byte per cell))`. Cell value 0 = empty.

### Object record (44 bytes, little-endian)
| Offset | Field |
|---|---|
| 0, 2 | x, y in stage 1 (u16 each) |
| 4-7 | unknown |
| 8, 10 / 16, 18 / 24, 26 / 32, 34 | x, y in stages 2-5, each followed by 4 unknown bytes |
| 40 | sprite id (u8) |
| 41 | kind (u8) |
| 42-43 | unknown |

Kinds: `01` Red Key, `02` Blue Key, `03` Star, `05` Door, `07` Heart, `2C` Dream Stone, `2D` Large Dream Stone, `2E` One Up,
`2F` Goomi, `31`/`32` Mobile Goomi (vertical / diagonal), `6F` Box, `76` Moo, `77`/`78` Flying Moo (horizontal / vertical).
Portal record (8 bytes): `u16 x, u16 y, 4 unknown`.

### Runtime addresses (IWRAM)
| Address | Meaning |
|---|---|
| `0x03004DB0` | staging buffer DMA'd to VRAM each frame (visible window of BG2) |
| `0x030045FD` | the bridge tile of vision 1-1 inside that buffer |
| gbajs freeze `21024` → 3, `21100` → 99 | infinite hearts / lives hacks (emulator-side RAM freezes) |

### Loader hook (Part 8)
| Offset | Bytes | Meaning |
|---|---|---|
| `0x043B0C` | `23 F3 80 FD` | `bl 0x08367610` replacing `mov r4, r1` + next halfword in the tilemap loader |
| `0x367610` | `78 46 3C 30 00 47` | `mov r0,pc; add r0,#0x3C; bx r0` (enter ARM at `0x08367650`) |
| `0x367620` | 8 bytes per vision | `{u32 original_tilemap_bus_addr, u32 custom_tilemap_bus_addr}` |
| `0x367650` | `04 00 85 E2` | `add r0, r5, #4` |
| `0x367654 + 12*i` | `xx 40 1F E5 / 04 00 50 E1 / yy 00 1F 05` | `ldr r4,[pc,#-xx]; cmp r0,r4; ldreq r0,[pc,#-yy]` per custom vision |
| after the blocks | `01 40 A0 E1 / 1E FF 2F E1` | `mov r4, r1; bx lr` |

The editor's save path: `LZ77-encode → Huffman-encode → write at custom slot → apply object field diffs in place
(objects never change size) → write the loader patch → write the marker`.

## What generalises

- One visible objective, then the hop chain (VRAM → DMA → WRAM → SWI → ROM), then tables, then objects, then a patch.
- Assets are found through pointer tables indexed by level; find one table and the siblings fall out.
- Visual data and collision/gameplay data can be the same buffer or different ones; verify with the physics code.
- In-place saves are a trap; relocate and hook.
- Keep the editor honest about what it needs: a user-supplied dump of a specific region, verified by hash.
