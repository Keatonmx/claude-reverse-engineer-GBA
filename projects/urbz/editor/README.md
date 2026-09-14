# Urbz District Editor

A browser page that edits the districts of *The Urbz: Sims in the City* (GBA) and exports the result as a UPS patch.
Open `index.html` in any modern browser (no server, no build step, nothing leaves your machine) and load your own dump.

- **Districts.** All 71 entries of the game's district table (index 62 is the first playable rooftop).
- **Layers.** Each district has up to three visual layers (ground = BG2, middle = BG1, top = BG0) made of 32x32-pixel
  pieces (metatiles), plus a collision layer of 4x4 cells per piece. Pick a layer or Collision in *Edit*, click a piece
  in the *Pieces* panel, then click or drag on the map. Right-click a map cell to pick up whatever is there. Pieces
  marked with an orange dot are not used anywhere in the district and are safe to redefine.
- **Piece editor.** The second tab shows the selected piece as a 4x4 grid of 8x8 tiles. Click a slot, then click a tile
  in the district's tile bank (or type its number), with palette bank and H/V flip. Every cell using that piece
  updates at once. The piece count is fixed (the game allocates RAM for it), so redefine unused pieces to add new art.
- **Import art.** The third tab loads a PNG (any size that is an integer upscale of 32x32; magenta or alpha =
  transparent), converts it to the hardware's rules and paints it onto the selected piece: grid detection, colour
  quantization to an existing palette bank or a new 15-colour palette in a bank you choose, 4bpp tiles written into the
  district's tile bank. The report lists scale, palette, colour error, shared tiles and duplicate slots. *Suggest a
  private piece* picks a piece whose 16 tiles no other piece uses. See `ART-BRIEF.md` for the prompt to give an image
  model. Tile banks cannot grow (they are fully used and read straight from ROM), so art always replaces a piece.
- **Collision colours.** Red blocks the Sim (byte `0x03`), green is pavement the Sim walks on (`0x40`, `0x43`), blue marks
  special edge/step values, unpainted is open (`0x00`). These meanings were confirmed by a scripted walk test in mGBA.
- **Undo/redo** with the buttons or Ctrl+Z / Ctrl+Y. Strokes across several districts are one history.
- **Export .ups** writes a patch with every edited district. Apply it to the same dump with any UPS patcher or
  `python3 ../../../.claude/skills/gba-reverse-engineering/scripts/gba_patchfile.py apply rom.gba patch.ups out.gba`.
  **Patched .gba** downloads the patched ROM for your own testing. Ship the patch, never the ROM.
- **Continue an edit.** Load the ROM, then load a previously exported `.ups` in *Patch*; new edits are exported against the
  original dump again, so the new patch replaces the old one.

## How it writes back

Edited blobs are re-encoded in the game's own compression (a type-6 encoder that the game's decoder was shown to accept
byte-exactly, with the Diff16 pre-filter the game uses for maps and pieces) or, when smaller, as BIOS LZ77 with or
without the filter flag; each candidate is decoded back before it may win. Because every district blob is referenced
exactly once, the bytes of a replaced blob are reclaimed and edits are placed best-fit, largest first, into those slots;
the ROM's 32 KB zero tail is the fallback. The footer shows how many blobs landed in reclaimed slots and how much of the
tail is used. Which pieces go where, the collision grid, the piece definitions, the tile pixels (through Import art) and the
district palettes (a new bank per import) can all change; sprites and HUD graphics are still read-only.

## Files

- `urbz-core.js` — decoding (type 6 + Diff16, LZ77 incl. the `0x90` filtered header, RLE, raw), the district table,
  level model, piece painter, type-6 and LZ77 encoders, blob extents, the reclaiming allocator, UPS make/apply. Also
  loads in Node.
- `index.html` — the UI. `window.__editorApi` is a small hook used by the headless browser test.
- `test/core_test.js rom.gba` — round-trips all 480 type-6 district blobs through the encoders, checks a full edit, and imports a synthetic 8x asset onto a private piece.
- `ART-BRIEF.md` — the prompt and constraints for image models producing art for the importer.
- `test/ui_test.js rom.gba [out.ups]` — drives the page in headless Chromium (needs `playwright`; set `CHROME`).
