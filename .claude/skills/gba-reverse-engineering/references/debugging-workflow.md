# Debugging workflow: from a pixel on screen to a byte in the ROM

This is the method Bruno Macabeus used to turn "I want to stretch this bridge" into a level editor for
*Klonoa: Empire of Dreams*, generalised. Each step answers one question and hands the next step an address.
Dynamic analysis (emulator debugger) finds *where*; static analysis (disassembler) explains *why*.

## Contents
1. Tooling and setup
2. The hop chain: VRAM → DMA → WRAM → decompressor → ROM
3. Finding game logic (physics, collision, counters)
4. Finding object/entity tables
5. Finding dimensions and per-level tables
6. Emulator cheat sheets (mGBA, No$GBA)
7. Static analysis setup (Ghidra, IDA)
8. Keeping a findings log

## 1. Tooling and setup

- **Emulator with a debugger.** No$GBA (debug build, Windows) has the best integrated viewers: VRAM/tilemap viewer
  (F5), palette/OAM viewers, memory editor, breakpoints with read/write conditions. mGBA is cross-platform, open
  source, has a CLI debugger (`-d`), a GDB stub (`gdb` command, port 2345), memory search, and tile/map/OAM/IO viewers
  under Tools. Either works; the article used No$GBA.
- **Disassembler.** Ghidra (free; load as ARM v4T little-endian, see section 7) or IDA. Both need the memory map set up
  before the decompiler output is readable.
- **Reference docs.** GBATEK for hardware and BIOS, Tonc for how games are usually written, the ARM7TDMI reference for
  instruction encodings.
- **A ROM you dumped yourself** and its SHA-1 (`gba_rom.py header`). Every address you find is specific to that dump.
- **Save states** at the moment of interest so each experiment restarts from the same frame.

## 2. The hop chain

The thing you can see is the least interesting copy of the data. Follow it upstream:

| Hop | What you have | How to get the next address |
|---|---|---|
| Screen | A tile you can point at | VRAM/tilemap viewer: hover the tile, read its tile index and the map address in VRAM (`06xx_xxxx`). Note which BG layer it is on (in Klonoa the walkable layer was BG2). |
| VRAM | Map address | Edit the entry in the memory viewer. If it reverts next frame, something re-copies it every frame. Set a **write watchpoint** on that address. |
| Writer | PC at the break | If PC is in a loop poking `0400_00Bx`, it is DMA: read the channel's source register. Otherwise it is a CPU copy: read the source register of the `ldr/ldrh` feeding the store. Either way you get a WRAM address (`02xx`/`03xx`). |
| WRAM | Full level buffer | Editing here persists visually (the DMA copies your edit every frame). Watch writes to the buffer start and **reload the level** to catch whoever fills it. |
| Filler | PC in BIOS or a game routine | If inside a BIOS SWI (0x11/0x12/0x13/0x14): r0 = ROM source, r1 = destination. If a custom routine: find its source pointer the same way (usually `ldr r0,[pc,#..]` from a literal pool or a table lookup). |
| ROM | Compressed asset address | `gba_compress.py info/decompress`, then `gba_rom.py pointers` to find the table that references it. |

Two shortcuts that often skip hops: search RAM for a byte pattern you can read off the VRAM viewer; and set a watchpoint
on `DMAnSAD` writes to enumerate every DMA source the game uses in a level.

Expect the visual layer and the collision layer to be different data: in Klonoa the stretched bridge was drawn, but the
player fell through it because collision consulted a separate structure (Part 3). Fixing the picture is not fixing the level.

## 3. Finding game logic

Physics and rules are found from the *variables* they touch:

1. Find the variable. Use the emulator's memory search like a cheat-search: value known (lives = 3), value changed /
   unchanged between snapshots (Y position while falling vs standing), or "value increased" for counters. Player
   coordinates are usually 16-bit, sometimes fixed-point (8.8 or 12.4: the integer part matches the VRAM position).
2. Watch **writes** to it. Break, then walk up the call stack (or `lr`) to the function that decides the new value.
3. Read that function in the disassembler. Look for comparisons against table lookups: `collision(x >> 3, y >> 3)`
   patterns are `lsr #3` on the coordinates, a multiply/shift by the map width, and a load from a base pointer. That
   base pointer is the collision map, which is the address you actually need to make the "stretched bridge" solid.
4. Prove it by patching the RAM value in the emulator and watching behaviour change before touching the ROM.

Freezing a RAM address (write it every frame) is how the "infinite hearts/lives" hacks in klo-gba.js work.

## 4. Finding object/entity tables

Sprites on screen come from hardware OAM (`0700_0000`), which is refreshed from a shadow buffer in WRAM. Watch writes to
OAM → find the copy routine → its source is the runtime object list (one struct per live object with x, y, kind, state).
Watch the *kind* or *x* field of a live object and reload the phase to catch the spawner, which reads the **ROM object
list**. In Klonoa (Part 6) that turned out to be a table of 44-byte records per level: five (x, y) pairs (one per stage
of the level, 8 bytes each), then a sprite byte and a kind byte, delimited by known start/end addresses. The runtime
list only holds the objects of the current phase, which is why watching the ROM table directly is more useful.

To decode a record layout: dump the table with `gba_rom.py dump --stride N`, line up columns, change one field in the
emulator's memory editor (or with a hex editor in the ROM) and observe what moves. Pointers (`08xxxxxx`) inside records
usually lead to sprite/animation data.

## 5. Finding dimensions and per-level tables

A decompressed tilemap is a flat vector; you need its width to fold it into rows (Part 5). Options, cheapest first:

- The game must know the width too. Look for a multiplication by, or a table load near, the tilemap base in the
  routine found in section 3 (the collision lookup) or the DMA row-copy routine (it advances the source by `width`).
- Guess from factors of the size and from visual sanity: render with `render_tiles.py tilemap --width W` for candidate
  widths and pick the one where structures line up. Wrong widths shear the image diagonally.
- Per-level tables usually sit next to each other and share an index. Once you find one table indexed by
  `(world, level)`, dump siblings with the same stride (`gba_rom.py table --stride`) and look for pointers into the
  ranges you already identified (tilemap, tileset, palette, objects). Klonoa keeps width/height at `0x51C80`,
  palette pointers at `0x188F60`, tileset pointers at `0x189048`, all indexed by `(world-1)*9 + (level-1)`.

Levels are often subdivided (Klonoa's "stages"/phases within a vision) yet stored as one big map; check whether the
per-phase data is a separate table of offsets/portals rather than separate maps.

## 6. Emulator cheat sheets

**mGBA CLI debugger** (`mgba -d rom.gba`, or Tools → Open debugger in mgba-qt):

```
break 0x08043B0C          execution breakpoint
watch/w 0x03004DB0        break on write   (watch/r read, watch/c change)
watch 0x0600F000 ...      any access
r/2 0x03004DB0            read halfword   (r/1, r/4); w/2 addr value writes
i                         registers      (r0..r15, cpsr)
dis                       disassemble at pc
n / c                     step / continue
trace 100                 trace 100 instructions
reset
gdb                       start GDB stub on :2345 (connect Ghidra/gdb: target remote :2345)
```

**No$GBA** (debug version): F5 opens the VRAM viewer; Ctrl+B sets breakpoints. Breakpoint syntax:
`08043B0C` (execute), `[03004DB0]!` (write), `[03004DB0]?` (read); ranges like `[03004DB0..03004DBF]!`. The memory viewer
is editable; the "DMA" and "I/O map" windows show current DMA sources. Klonoa's IWRAM buffer for the visible map was
at `0300_4DB0` (bridge tile at `0300_45FD`), found exactly this way.

## 7. Static analysis setup

**Ghidra**: File → Import ROM as raw binary, language `ARM:LE:32:v4t`, base address `0x08000000`. In the Memory Map
add blocks for BIOS (`0x00000000`, 16K), EWRAM (`0x02000000`, 256K), IWRAM (`0x03000000`, 32K), IO (`0x04000000`, 1K),
PAL (`0x05000000`, 1K), VRAM (`0x06000000`, 96K), OAM (`0x07000000`, 1K) so the decompiler resolves pointers as data.
The entry at `0x08000000` is ARM (`b` to start); mark the rest Thumb by setting the `TMode` context register to 1
before disassembling, or disassemble from a known Thumb address. Community loaders (e.g. "GhidraGBA") automate this.
**IDA**: processor ARM little-endian, then set the `T` segment register to 1 for Thumb regions. IDA's hex view
highlights how far a data item extends, which is how the article delimited the compressed tilemap.

Name things as you go: the RAM addresses from your watchpoints become labels, and the decompiler becomes readable
once `sub_8043A00(0x081B27FC, 0x02001000)` reads as `decompress_tilemap(rom_ptr, wram_ptr)`.

If a decompilation project exists for the game (search GitHub for `<game>-decomp`; Klonoa has `Dream-Atelier/kl-eod-decomp`),
its symbol names are a shortcut for everything above.

## 8. Keeping a findings log

Record every address the moment you confirm it, with the evidence, in a table (see the deliverable format in SKILL.md).
Reverse engineering sessions are long and the same address gets rediscovered otherwise. Note which ROM (SHA-1) and
region each address belongs to; the same game's EU/JP builds move everything.
