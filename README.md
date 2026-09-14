# claude-reverse-engineer-GBA

A Claude Code skill for reverse engineering Game Boy Advance games, distilled from Bruno Macabeus's
["Reverse engineering a GameBoy Advance game — Complete Guide"](https://macabeus.medium.com/reverse-engineering-a-gameboy-advance-game-introduction-ec185bd8e02)
(the Klonoa: Empire of Dreams level-editor series) and its open-source result, [klo-gba.js](https://github.com/macabeus/klo-gba.js).

The skill lives in [`.claude/skills/gba-reverse-engineering/`](.claude/skills/gba-reverse-engineering/SKILL.md) and is picked up
automatically by Claude Code when this repository is open. To use it elsewhere, copy that directory into your project's
`.claude/skills/` or your user-level `~/.claude/skills/`.

## What is inside

| Path | Purpose |
|---|---|
| `SKILL.md` | The method: a ten-step loop from a pixel on screen to a ROM patch, the deliverable format, and the pitfalls |
| `references/gba-hardware.md` | Memory map, video/tiles/OAM/DMA, BIOS calls, CPU conventions, cartridge header |
| `references/debugging-workflow.md` | The VRAM → DMA → WRAM → decompressor → ROM hop chain, finding physics and object tables, mGBA/No$GBA cheat sheets, Ghidra/IDA setup |
| `references/compression.md` | GBA LZ77 / Huffman / RLE bit layouts, chained formats, locating compressed assets, re-compression strategy |
| `references/code-analysis.md` | Reading and changing code: Ghidra setup, mGBA GDB bridge, literal pools and Thumb/ARM mode pitfalls, decompilation projects |
| `references/patching.md` | Free space, pointer redirects, `bl` hooks with Thumb→ARM stubs, hand-assembly encodings, verification, distribution |
| `references/headless-emulation.md` | Running a game with no display: building libmgba, scripted input, memory/IO dumps, watchpoints via the debugger API, calling the game's own decoder as an oracle |
| `references/klonoa-case-study.md` | The complete worked example: every table, structure, address and the loader patch from klo-gba.js |
| `references/urbz-case-study.md` | Second worked example: a licensed title with custom IWRAM decompressors, a header-nibble dispatcher, structure-of-arrays metatiles and a VRAM tile cache, verified pixel-exact |
| `scripts/gba_compress.py` | Decode/encode/scan GBA BIOS compression formats |
| `scripts/gba_rom.py` | ROM header, pointer hunting, table dumps, free-space finder, hex dumps |
| `scripts/thumb_patch.py` | Hand-assemble Thumb/ARM branches and literal loads; generate hook stubs |
| `scripts/gba_patchfile.py` | Make and apply IPS/UPS patch files (UPS for ROMs over 16 MB) |
| `scripts/render_tiles.py` | Render tilesets and tilemaps to PNG |
| `scripts/test_scripts.py` | Self-test (`python3 scripts/test_scripts.py`), vectors include the real Klonoa patch bytes |
| `scripts/emu/` | Headless mGBA tools (C): `harness` (scripted input + memory dumps), `trace` (watchpoints/breakpoints with registers), `oracle` (call a ROM routine on chosen inputs), `topng.py`, `build.sh` |
| `evals/evals.json` | Prompts used to evaluate the skill |

The Python scripts have no third-party dependencies. The `scripts/emu/` tools need a C compiler and cmake; `build.sh` clones and builds mGBA headless.

## Projects

| Path | What |
|---|---|
| `projects/urbz/` | First application of the skill: static survey of *The Urbz: Sims in the City* (GBA). `FINDINGS.md` documents the ROM layout, the resource loader, the two custom IWRAM decompressors (type 6 fully translated in `urbz_codec.py`), the 7,682-record resource directory, and a ranked list of hack ideas. `urbz_dump.py` exports every graphics resource; `urbz_level.py` renders any of the 33 district records in colour, verified pixel-exact against the running game; `urbz_patch.py` writes edited maps and collision back (as BIOS LZ77 through the loader's own type dispatch, no custom encoder needed) and emits a UPS patch, proven in the emulator by a wall that stops the Sim; `editor/index.html` is a browser-based district editor for all 71 districts (paint pieces and collision, redefine pieces from the tile bank, import PNG art onto pieces with automatic palette quantization (see `editor/ART-BRIEF.md` for the image-model prompt), re-encode in the game's own compression with a verified encoder, reclaim replaced blobs, export a UPS) whose rendering and output match the Python tools exactly; `emu/` has the headless mGBA harness, tracer, loader oracle and input scripts used for those checks. ROMs are git-ignored; bring your own dump. |
