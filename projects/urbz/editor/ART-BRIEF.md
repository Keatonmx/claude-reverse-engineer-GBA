# Brief for image models (ChatGPT, Midjourney, etc.) producing Urbz GBA art

Paste the block below into the image model, then add the asset list you need. Save its output as PNG and load it in
the editor's *Import art* tab. The converter snaps the grid, keys the transparency, quantizes colours and reports what
it had to change. If the report shows a high colour error or wrong scale, send the numbers back to the model and ask
for a redo.

```
You are producing pixel art for a Game Boy Advance game. Hard rules:

CANVAS: Deliver the image at exactly 8x the final size as blocky nearest-neighbour pixels,
so each final pixel is a flat 8x8 block. State the final size in your reply. The final
size is given per asset below.

COLOURS: Flat colours only. No anti-aliasing, no gradients, no blur, no soft shadows, no
dithering unless asked. The whole asset must use at most 15 colours. Colours are rounded
to 32 levels per channel (multiples of 8), so avoid subtle shades that will merge.

TRANSPARENCY: Paint transparent areas solid magenta #FF00FF. Nothing else may be magenta.

STYLE: Isometric city, 3/4 view, matching a mid-2000s handheld look. Strong silhouettes,
readable at 1x on a 240x160 screen. Light comes from the top-left.

ASSETS (final sizes):
- Ground piece: 32x32, tiles seamlessly with itself horizontally and vertically.
- Wall or building piece: 32x32, isometric face painted inside the square, magenta where
  the ground beneath should show.
- Object piece (vending machine, lamp, bench, planter): 32x32, magenta background.

Deliver one asset per image.
```

## What to send along with the prompt

The prompt alone gets generic pixel art. Attach references from the editor's *Import art* tab so the model matches the
game's look:

1. **Piece @8x** — the piece you are replacing, blown up so the model sees the exact pixel grid and the isometric angle.
   Ask it to "redraw this piece as X, same angle, same light direction, same footprint".
2. **Area around it @4x** — seven by seven pieces around the target, so the new art fits its neighbours (kerb lines,
   pavement texture, shadow side).
3. **Palette swatch** — the district's 16 palette banks. Say "use colours close to bank N" when you want *piece's own
   banks* in the converter, or leave the model free when you will use *new palette into bank N*.
4. **Whole district** (optional) — for overall style and scale.

Then describe the asset in one or two sentences: what it is, which of the three roles it plays (ground, wall or object),
where the transparent parts are, and anything that must line up with neighbours. One asset per request works best.

## What the converter enforces, so you do not have to

- Grid: it detects 8x, 4x or 2x upscaling and samples block centres. A blurry grid still works if blocks are mostly flat.
- Size: the result must be at least 32x32 after dividing by the scale; extra pixels are ignored.
- Colours: every 8x8 tile gets the closest colours from a 16-colour bank (15 plus transparent). Choose *new palette into
  bank N* to build a palette from the image (median cut, 15 colours) into an unused bank, or *piece's own banks* to
  match existing art exactly.
- Transparency: magenta or alpha under 50 % becomes index 0. On the ground layer that shows the backdrop colour, so give
  ground pieces no transparent pixels.

## What the hardware and this game impose

- Every district's tile bank is fully used and cannot grow (it is read straight from ROM), so new art always *replaces*
  an existing piece's 16 tiles. Use *Suggest a private piece* to get one whose tiles no other piece shares; the report
  says if shared tiles would change other pieces too.
- A piece is 4x4 tiles of 8x8; each tile picks one of 16 palette banks. Bank 0 is taken by the HUD in-game.
- Sprites (Sims, NPCs, furniture drawn as objects) and HUD graphics are not importable yet; they live in the sprite
  directory and use the object palettes, which pass through a tint stage.
