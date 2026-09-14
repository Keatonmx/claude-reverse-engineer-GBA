# GBA compression formats and how to find compressed assets

GBA games lean on the BIOS decompressors (see `gba-hardware.md` section 6) because they are free and tiny. Level maps,
tilesets, palettes and sprites are very often stored LZ77-compressed, sometimes wrapped in Huffman too, and the
first four bytes tell you which. `scripts/gba_compress.py` implements every format below (decode and encode) and can
scan a ROM for candidates.

## Contents
1. Common header
2. LZ77 (type 0x10)
3. Huffman (type 0x24 / 0x28)
4. RLE (type 0x30)
5. Chained formats (Huffman over LZ77)
6. Finding the compressed source of something you see in RAM
7. Re-compressing: size growth and where to put the result

## 1. Common header

```
byte 0   : high nibble = type (1 LZ77, 2 Huffman, 3 RLE, 8 diff-filter), low nibble = Huffman symbol bits (4/8), else 0
bytes 1-3: decompressed size, little-endian 24 bits
```

So a block starting `10 73 62 00` is LZ77 that inflates to `0x6273` = 25203 bytes, and one starting `28 xx xx xx` is
Huffman with 8-bit symbols. Klonoa's vision 1-1 tilemap at `0x1B27FC` is a Huffman block whose *output* is an LZ77 block
that inflates to 25203 bytes (the number the article quotes). `gba_compress.py info rom.gba 0x1B27FC` prints the outer
header; `decompress --chain` peels both.

## 2. LZ77 (SWI 0x11 / 0x12)

After the header, groups of one **flag byte** followed by 8 items, MSB first. Flag bit 0 = one literal byte.
Flag bit 1 = a two-byte back-reference:

```
byte A: high nibble = length - 3   (3..18)
        low nibble  = displacement high 4 bits
byte B: displacement low 8 bits          displacement = value + 1  (1..4096)
```

The reference copies `length` bytes starting `displacement` bytes back in the *output*. Overlap is allowed
(displacement 1 with length 10 repeats a byte 10 times), except when the target is VRAM: SWI 0x12 writes halfwords, so
displacement 1 is illegal there and encoders have a "VRAM-safe" mode (`--vram`).

## 3. Huffman (SWI 0x13)

```
header             : 0x24 (4-bit symbols) or 0x28 (8-bit symbols) + size
tree size byte     : (size of tree table in bytes / 2) - 1
tree table         : node bytes, root first, children stored in adjacent pairs
bitstream          : 32-bit little-endian words, bit 31 consumed first; 0 = left child, 1 = right child
```

Node byte: bits 0-5 = offset, bit 7 = left child is a leaf, bit 6 = right child is a leaf. The children of the node at
address `a` are at `(a & ~1) + offset*2 + 2` (left) and `+1` (right). Leaves hold the symbol. With 4-bit symbols the
first decoded nibble is the *low* nibble of the output byte.

The 6-bit offset limit means an 8-bit tree with many symbols and flat frequencies may be impossible to lay out;
the bundled encoder falls back to 4-bit symbols when that happens (the BIOS decodes both).

## 4. RLE (SWI 0x14 / 0x15)

Flag byte: bit 7 set → repeat the next byte `(flag & 0x7F) + 3` times; clear → copy the next `(flag & 0x7F) + 1` bytes.

## 5. Chained formats

Because Huffman removes symbol redundancy and LZ77 removes repetition, some games apply both. Klonoa stores tilemaps,
tilesets and palettes as **Huffman(LZ77(data))**: to read, Huffman-decode first, then LZ77-decode; to write, LZ77-encode
first, then Huffman-encode. You recognise a chain because the *output* of one decode starts with another valid header.

```
gba_compress.py decompress rom.gba 0x081B27FC --chain -o tilemap.bin      # prints: layers: huffman > lz77
gba_compress.py compress tilemap.bin --type lz77,huffman -o tilemap.huf    # reverse order for writing
```

Watch for a game-specific prefix in front of the real data: Klonoa's decoded tilemap begins with 4 metadata bytes
before the tile indices, and the tileset/palette pointers point 4 bytes *before* the compression header. When a decode
fails at an address a pointer table gave you, try `address + 4` (and check the bytes with `gba_rom.py dump`).

## 6. Finding the compressed source of data you can see in RAM

1. Locate the decompressed copy in WRAM (a write watchpoint on VRAM leads you to the DMA, whose source is WRAM; or
   search RAM for the bytes you see in the VRAM viewer).
2. Put a **write watchpoint on the first bytes of that WRAM buffer** and reload the level. The break lands either inside
   the BIOS (`0000_xxxx`, you are inside SWI 0x11/0x13) or inside a game routine.
3. Read **r0** (source) and **r1** (destination) at the `swi` instruction, or step out to the caller. r0 is the ROM address
   of the compressed asset. For the Klonoa tilemap that was `0x081B27FC`.
4. Confirm with `gba_compress.py info rom.gba <addr>`: the header type and decompressed size must make sense.
5. Find who references it: `gba_rom.py pointers rom.gba <addr>`. A hit inside a regular table (equal stride between
   entries) is the per-level asset table; dump it with `gba_rom.py table` to get every other level for free.

If you have no debugger handy, `gba_compress.py scan rom.gba --min-size 1024 --types lz77,huffman` lists every block that decodes cleanly and shrinks its input (RLE hits are noisy, hence the filter);
plausible tilemaps are the larger ones and their sizes match `width * height (+ small header)`.

## 7. Re-compressing

A modified asset almost never compresses back to the same size. Writing it in place is only safe if it got smaller;
otherwise it overwrites whatever follows (in Klonoa the next level's tilemap, which crashed level N+1). The reliable
pattern is: write the new blob into free space (`gba_rom.py freespace`), then redirect the pointer that referenced the
original (`gba_rom.py pointers`), or, when the address is computed in code rather than read from a table, hook the
loader (see `patching.md`). Keep the original untouched so you can always fall back.
