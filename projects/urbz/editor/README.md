# Urbz District Editor

A browser page that edits the districts of *The Urbz: Sims in the City* (GBA) and exports the result as a UPS patch.
Open `index.html` in any modern browser (no server, no build step, nothing leaves your machine) and load your own dump.

- **Layers.** Each district has up to three visual layers (ground = BG2, middle = BG1, top = BG0) made of 32x32-pixel
  metatiles, plus a collision layer of 4x4 cells per metatile. Pick a layer or Collision in *Edit*, click a piece in the
  panel on the right, then click or drag on the map. Right-click a map cell to pick up whatever is there.
- **Collision colours.** Red blocks the Sim (byte `0x03`), green is pavement the Sim walks on (`0x40`, `0x43`), blue marks
  special edge/step values, unpainted is open (`0x00`). These meanings were confirmed by a scripted walk test in mGBA.
- **Undo/redo** with the buttons or Ctrl+Z / Ctrl+Y. Strokes across several districts are one history.
- **Export .ups** writes a patch with every edited district. Apply it to the same dump with any UPS patcher or
  `python3 ../../../.claude/skills/gba-reverse-engineering/scripts/gba_patchfile.py apply rom.gba patch.ups out.gba`.
  **Patched .gba** downloads the patched ROM for your own testing. Ship the patch, never the ROM.
- **Continue an edit.** Load the ROM, then load a previously exported `.ups` in *Patch*; new edits are exported against the
  original dump again, so the new patch replaces the old one.

## How it writes back

The game's loader dispatches on a header type nibble and its type 1 branch is BIOS LZ77, so edited maps are stored as
plain LZ77 blobs in the ROM's zero-filled tail (about 32 KB) and the district record pointers are redirected. No custom
encoder, no code patch. Metatile definitions and tile graphics are not editable yet, only which piece goes where and the
collision grid. The free-space counter in the footer shows how much of the tail is used.

## Files

- `urbz-core.js` — decoding (type 6 + Diff16, LZ77, raw), record scan, level model, metatile painter, LZ77 encoder, UPS
  make/apply, write-back. Also loads in Node; it is tested against the Python tools in `..` (same UPS byte for byte, same
  pixels as `urbz_level.py render`).
- `index.html` — the UI. `window.__editorApi` is a small hook used by the headless browser test.
