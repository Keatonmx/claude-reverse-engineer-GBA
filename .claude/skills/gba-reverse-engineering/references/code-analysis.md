# Getting at the game's code: disassembly, decompilation, and the Ghidra + mGBA bridge

The data-side workflow (`debugging-workflow.md`) leads you to routines: the DMA copier, the level loader, the collision
check. This file is about reading and changing those routines, and about when to skip binary analysis entirely.

## Contents
1. There is nothing to "extract": the ROM is the code
2. Decide the route: decompilation project vs raw binary analysis
3. Ghidra setup for a GBA ROM
4. Live debugging bridge: mGBA GDB server ↔ gdb / Ghidra
5. The three things that make GBA disassembly look like garbage (and the fixes)
6. Reading compiler output: patterns you will see constantly
7. Modifying code: patch vs rebuild

## 1. The ROM is the code

A `.gba` file is a flat image mapped at `0x08000000`: ARM7TDMI machine code, literal pools, data tables and assets are
interleaved with no section headers, symbol table or relocation info. "Pulling the code out" therefore means
*disassembling* the image with the right memory map and mode annotations, or finding a project that has already turned
it back into buildable C. There is no decryption, packing, or file system on stock cartridges. Small pieces of code are
sometimes copied to IWRAM at runtime (fast ARM routines); you find those by watching writes to `0300_xxxx` from the
startup code, or simply by dumping IWRAM from the emulator once the game is running.

## 2. Pick the route

| Situation | Route |
|---|---|
| A matching decompilation exists (search GitHub for `<game> decomp`, `pret` org for Pokémon/Zelda/Fire Emblem-style projects, `Dream-Atelier/kl-eod-decomp` for Klonoa) | **Use it.** Edit C, rebuild with `agbcc`/devkitARM per its README, diff the output ROM. The compiler handles pointer fixups and layout; the symbol names alone save days. Still use the emulator to *find* what to change. |
| No decomp, goal is data (levels, sprites, text) | Debugger-first (`debugging-workflow.md`); open Ghidra only for the specific routines the watchpoints hand you. |
| No decomp, goal is behaviour (mechanics, bugs, new logic) | Ghidra + mGBA bridge below; patch with hooks (`patching.md`). Consider starting a partial decomp of just the functions you touch. |

IDA Pro is excellent for ARM but expensive; IDA Free lacks the processor support you need. Ghidra is free and its
decompiler is good enough for Thumb game code once the setup below is done.

## 3. Ghidra setup

1. **Import** the ROM: File → Import File, Format *Raw Binary*, Language `ARM:LE:32:v4t` (ARMv4T, little endian),
   Options → Base Address `0x08000000`. Do not run auto-analysis yet.
2. **Memory map** (Window → Memory Map): add blocks so pointers into RAM and I/O resolve as data instead of being
   ignored. Names/sizes: `BIOS 0x00000000 0x4000`, `EWRAM 0x02000000 0x40000`, `IWRAM 0x03000000 0x8000`,
   `IO 0x04000000 0x400`, `PAL 0x05000000 0x400`, `VRAM 0x06000000 0x18000`, `OAM 0x07000000 0x400`. Mark RAM blocks
   read/write, volatile for IO. Community scripts and the "GhidraGBA" loader do this automatically and label the
   I/O registers (`REG_DISPCNT`, `REG_DMA3SAD`, ...) so the decompiler prints names.
3. **Mode.** The entry at `0x08000000` is a single ARM `b`; nearly everything else is Thumb. Before auto-analysis, select
   the ROM block and set the `TMode` context register to 1 (right-click → *Set Register Values*, `TMode`/`T` = 1,
   0 for ARM). Then disassemble from the entry target and let analysis follow `bl` chains. Where you see nonsense
   (unaligned `bl` pairs split, huge immediates), you are in the wrong mode or inside data: clear and re-set.
4. **Analysis options.** Enable *ARM Constant Reference Analyzer* (resolves `ldr rN,[pc,#x]` literals to addresses), and
   turn off *Aggressive instruction finder* for the first pass; it will happily disassemble tile graphics.
5. **Label as you go.** Every RAM address a watchpoint gave you becomes a label; every routine the debugger stopped in
   gets a name. Ghidra's decompiler output improves dramatically once the struct of an object pool is defined
   (Data Type Manager → new struct with the fields from your findings table, then apply to the pointer).

## 4. Live debugging bridge

mGBA ships a GDB stub. Start it with `gdb` in the CLI debugger (`mgba -d rom.gba`) or Tools → *Start GDB server* in
`mgba-qt`; it listens on port 2345. Connect with an ARM-aware gdb:

```
gdb-multiarch            # or arm-none-eabi-gdb from devkitARM
(gdb) set architecture armv4t
(gdb) target remote localhost:2345
(gdb) break *0x08043B0C          # execution breakpoint (Thumb address, no bit 0)
(gdb) watch *(short*)0x03004DB0  # write watchpoint on a halfword
(gdb) info registers
(gdb) x/8i $pc                   # disassemble around pc
(gdb) x/16xh 0x03004DB0          # dump halfwords
(gdb) continue
```

To see the same addresses inside Ghidra, use the Debugger tool (Ghidra 10.2+): *Debugger → Connect* with the gdb
connector, pointing it at your local `gdb-multiarch`, then `target remote localhost:2345` in its interpreter. The
static listing and the live trace share addresses, so a breakpoint set in the decompiler view stops the emulator and
registers/memory show up next to the decompiled C. If the connector setup fights you, the fallback that costs nothing is
two windows: gdb (or mGBA's own CLI debugger) for the live side, CodeBrowser for the static side, and the address as
the bridge. Save states in mGBA let you replay the same frame against each new breakpoint.

Prefer a real BIOS dump over the HLE BIOS when tracing decompression or `CpuSet` calls: with HLE the `swi` returns in
one step and you cannot break inside it, though r0/r1 at the `swi` are still valid.

## 5. Why the disassembly looks like garbage

| Symptom | Cause | Fix |
|---|---|---|
| Plausible instructions that never make sense, `bl` halves split, odd immediates | Thumb bytes decoded as ARM, or vice versa. The CPU switches state at every `bx` and games mix both. | Set `TMode` per block/function (1 = Thumb). Check where you came from: `bx rN` with bit 0 set means the target is Thumb. In mGBA, `i` shows the T bit in CPSR at the breakpoint. |
| A function "ends" in a wall of constants, then code resumes | **Literal pool**: the compiler puts the 32-bit constants for `ldr rN,[pc,#x]` right after each function (or after an unconditional branch). | Do not disassemble them. Ghidra's constant-reference analyzer turns them into pointers; label the ones that are RAM addresses. In a trace logger (`trace N` in mGBA's CLI) only executed addresses appear, which separates code from pools reliably. |
| Loads/stores to `0x0300xxxx`/`0x0200xxxx`/`0x0400xxxx` with no names | Global variables, object pools and hardware registers are absolute addresses, not symbols. | Load the memory map with register labels (section 3), name RAM addresses from your findings table. The GBATEK I/O table gives the register names. |
| Jump tables you cannot follow statically | `switch` compiled to `add pc, ...` or a table of Thumb addresses. | Run it: a breakpoint on the `bx`/`mov pc` and a few inputs enumerate the cases; label the table entries. |
| Same code at two addresses | Routine copied from ROM to IWRAM at startup (or overlays swapped per mode). | Analyse the ROM copy; set the IWRAM block as an overlay or map it as a byte-mapped block pointing at the ROM source. |

## 6. Compiler patterns (mostly `agbcc`/GCC 2.9-era Thumb)

- Prologue `push {r4-r7,lr}` … epilogue `pop {r4-r7}` / `pop {r0}` / `bx r0`. Leaf functions may skip the push.
- Function arguments r0-r3, return r0; more than four arguments go on the stack (`ldr rN,[sp,#x]`).
- `ldr r0,[pc,#x]` followed by `ldr r0,[r0]` = read a global. `ldrh`/`strh` = 16-bit fields (coordinates, tile ids,
  counters); `ldrb` = flags/kinds.
- `lsl rX, rIndex, #N` then `add rX, rBase` = `base[index]` with a power-of-two struct size; a `mul` with a constant
  = odd struct size. The constant *is* the record size for your findings table (44 in Klonoa's object list).
- `asr #3` / `lsr #3` on a coordinate = tile coordinates (pixels ÷ 8); `#4` = 16-px metatiles.
- `swi 0xNN` = BIOS call (`gba-hardware.md` section 6); r0-r3 are its arguments.
- Fixed-point maths: `asr #8` after an add of a velocity means 8.8 fixed point positions.

## 7. Modifying code

- Small logic changes: patch instructions in place if the new sequence fits, otherwise hook into free space
  (`patching.md`). Hand-assemble with `scripts/thumb_patch.py` or assemble a `.s` with devkitARM and copy bytes.
- Larger changes: partial decompilation. Take the function's bytes, write equivalent C, compile with `agbcc`/`arm-none-eabi-gcc -mthumb -march=armv4t -O2`, place the output in free space and hook the original to it.
- Full changes: contribute to or start a matching decomp. It is the only route where "modify a line, rebuild, ship
  a patch" stays sane over months.

Whatever the route, keep the emulator loop tight: change → save state → run → watchpoint → confirm. The disassembler
explains; the debugger proves.
