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
| `references/patching.md` | Free space, pointer redirects, `bl` hooks with Thumb→ARM stubs, hand-assembly encodings, verification, distribution |
| `references/klonoa-case-study.md` | The complete worked example: every table, structure, address and the loader patch from klo-gba.js |
| `scripts/gba_compress.py` | Decode/encode/scan GBA BIOS compression formats |
| `scripts/gba_rom.py` | ROM header, pointer hunting, table dumps, free-space finder, hex dumps |
| `scripts/thumb_patch.py` | Hand-assemble Thumb/ARM branches and literal loads; generate hook stubs |
| `scripts/render_tiles.py` | Render tilesets and tilemaps to PNG |
| `scripts/test_scripts.py` | Self-test (`python3 scripts/test_scripts.py`), vectors include the real Klonoa patch bytes |
| `evals/evals.json` | Prompts used to evaluate the skill |

The scripts are plain Python 3 with no third-party dependencies.
