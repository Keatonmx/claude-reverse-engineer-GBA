# Headless emulation: run the game where you have no display, and let it answer questions for you

Static analysis stalls on questions only the running game can settle: which record is loaded, what a runtime table
contains, whether your decoder is byte-exact. A headless libmgba build turns those into scripts. Everything below was
used to finish the Urbz district format in one session (`references/urbz-case-study.md`).

## Contents
1. Build (5 minutes, no GUI libraries)
2. The three tools in `scripts/emu/`
3. Driving the game to the state you need
4. Reading memory and I/O properly
5. Watchpoints and breakpoints through the debugger API
6. Calling the game's own code: the oracle pattern
7. Gotchas that cost hours

## 1. Build

`scripts/emu/build.sh` clones mGBA, configures a static, GUI-less library (`BUILD_QT/SDL/GL=OFF`, `BUILD_STATIC=ON`,
`BUILD_HEADLESS=ON`), builds it, then compiles the tools with the same defines and include paths as the `mgba-headless`
target (read them from `CMakeFiles/mgba-headless.dir/flags.make`; do not guess them). Needs cmake, gcc, zlib and libpng.
The HLE BIOS is fine for driving and dumping; use a real BIOS dump only if you must single-step inside SWI routines.

## 2. The tools

| Tool | Purpose |
|---|---|
| `harness rom script outprefix` | Runs a script of `<frame> keys <mask>` / `shot name` / `dump name` (EWRAM, IWRAM, palette, VRAM, OAM) / `regs name` (true I/O state) / `save name` / `load name`. Key mask bits: A=1 B=2 Select=4 Start=8 Right=16 Left=32 Up=64 Down=128 R=256 L=512. `topng.py` converts `.rgba` screenshots. |
| `trace rom state frames keymask log [lo:hi ...] [rlo:hi ...] [b<hex> ...]` | Loads a state, sets write (`lo:hi`) or read (`rlo:hi`) watchpoints on address ranges and/or execution breakpoints, runs with a key mask held and logs frame, address, value, access source (CPU or DMA), PC, r0-r7, lr, sp and `[sp]`, `[sp+4]` on every hit. |
| `oracle rom state outdir loader ret dst blob...` | Hijacks the paused CPU to call a game routine (r0 = source, r1 = destination) and writes back r0 bytes from `dst`. Used to run the game's decompressor on arbitrary ROM blobs. |

A full run of a few thousand frames takes well under a second, so iterate freely.

## 3. Driving the game

Work in probes: run, screenshot every few hundred frames, look, extend the script. Mashing A every 60-120 frames gets
through most menus; screens that need a specific choice (name entry, Yes/No dialogs) show up as a screenshot that stops
changing. Save a state at each milestone (`save`) and start the next probe from it (`load`), so a 3,000-frame route is
never replayed from power-on. Keep the route file in the repo: a deterministic route means every later dump is reproducible
and RAM addresses are stable between runs.

## 4. Reading memory and I/O

`core->getMemoryBlock(core, region, &size)` gives direct pointers for EWRAM (2), IWRAM (3), palette (5), VRAM (6) and
OAM (7), but not for I/O (4). Scroll registers are write-only on the bus, so `busRead16` returns garbage: read
`((struct GBA*) core->board)->memory.io` instead (the `regs` command). From the I/O dump take `DISPCNT`, `BGxCNT`
(charblock, screenblock, size, bpp) and `BGxHOFS/VOFS`; from the RAM dumps take the game's own pointers (a level struct
in IWRAM will hold pointers into EWRAM for maps, metatile arrays, cache tables). Those pointers are the fastest way to
name what you are looking at.

## 5. Watchpoints and breakpoints

Attach a debugger with a custom module: `mDebuggerInit`, `mDebuggerAttach(&dbg, core)`, a `struct mDebuggerModule` whose
`entered` callback logs `info->address`, `info->type.wp.newValue`, `info->type.wp.accessSource` and the CPU registers
(`((struct ARMCore*) core->cpu)->gprs`), then sets `module->isPaused = false` and `debugger->state = DEBUGGER_RUNNING`
to resume without stopping. Set points with `dbg.platform->setWatchpoint(platform, module, &wp)` (`segment = -1`,
`minAddress`, `maxAddress`, `WATCHPOINT_WRITE`) and `setBreakpoint` (`BREAKPOINT_HARDWARE`). Watchpoints see DMA writes
too (`accessSource == mACCESS_DMA`), unlike the GUI's CPU-only watchpoints. Run with `mDebuggerRunTimeout(&dbg, 50)` in a
loop; note that while any breakpoint exists the debugger single-steps, so budget iterations accordingly.

What to watch: the VRAM charblock during a scroll gives the tile-cache copy routine and, via `lr`, the map walker; a
freshly decoded RAM array during the load tells you whether the game modifies data after decompression (in the Urbz case
it did not, the only extra writer was a `memset` on memory later reused); a breakpoint just before a copy loop logs the
exact (reference, base, destination) triples.

## 6. The oracle pattern

To prove a re-implemented decoder, or to decode formats you have not re-implemented, call the game's routine on your data:
load a state, set `gprs[0]` = blob address, `gprs[1]` = scratch RAM, `gprs[14]` = address of a `bx lr` stub with bit 0 set
(the CPU spins there after return, so completion is `pc` reaching the stub), `gprs[15]` = routine entry, switch to the
routine's state and re-prime the pipeline, then `core->step` until the PC hits the stub and read `gprs[0]` (size) and the
output. Compare with your decoder byte for byte. The Urbz type-6 + Diff16 decoder passed on every blob this way, which
is what turned a suspected decoder bug into the real answer (the wrong record was being compared).

## 7. Gotchas

- **`ARMWritePC` is ARM-only.** After switching to Thumb use `ThumbWritePC`; the ARM variant loads 32-bit prefetches and
  advances PC by 4, and the next `step` segfaults inside `ARMRun`. This one is invisible until the call actually runs.
- **States saved mid-interrupt.** A state taken in the main loop is usually inside the BIOS `VBlankIntrWait`; the pending
  IRQ fires on the first step and its handler returns into the game, not into your hijacked call. Re-prime the pipeline at
  the current instruction (`gprs[15] -= 2*width`, then the matching WritePC), step until `pc` is in ROM and
  `privilegeMode == MODE_SYSTEM`, then hijack. Do not set `cpsr.i` by hand; it crashed the core.
- **Never `step` right after `loadState`** without re-priming; the active memory region is stale.
- **Two records can share graphics.** Matching VRAM tiles against every candidate bank by content picked a neighbouring
  district whose bank held the same tiles at different indices, and produced a convincing but false "remap" of
  references. Decide which record is loaded by comparing decoded maps/metatiles with the RAM copies, never by tile pixels.
- **Static continuity metrics are fooled by self-similar art.** Pavement tiles join with anything; a whole bank region of
  them "matches" any hypothesis. Use them to rank, never to conclude. The emulator settles it.
- **Structure-of-arrays.** A 48-byte-per-record blob turned out to be 32-byte records followed by a separate 16-byte array;
  interleaved slicing produced plausible-looking nonsense (near-sequential numbers). When decoded data looks like
  identity sequences, suspect the layout, not the codec.
- **Isometric look, orthogonal data.** Diamond-shaped tiles tempt you into diamond geometry; the Urbz format was a plain
  4x4-tile grid all along. Let the map-walker code define the geometry.
