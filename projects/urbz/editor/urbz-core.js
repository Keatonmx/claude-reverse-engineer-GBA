// Pure logic for the Urbz district editor: resource decoding, record scan, level model, LZ77 encoder, UPS patches.
// Works in the browser (global `UrbzCore`) and in Node (`require`), so the same code is unit-tested against the
// Python tools in ../ (urbz_codec.py, urbz_level.py, urbz_patch.py).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.UrbzCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const ROM_BASE = 0x08000000;
  const F_LAYERS = [0x00, 0x10, 0x20];
  const F_COLL_META = 0x30, F_COLL_MAP = 0x34, F_OBJECTS = 0x40, F_BANK = 0x44, F_PALETTE = 0x48;
  const KNOWN_SHA1 = "8efd27375d1f92b43fe0d1c93a63c08b7a259acc";

  const u16 = (b, o) => b[o] | (b[o + 1] << 8);
  const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
  function ptr(rom, off) {
    const w = u32(rom, off);
    return w >= ROM_BASE && w < ROM_BASE + rom.length ? w - ROM_BASE : null;
  }

  // ------------------------------------------------------------------ decoders
  class BitReader {
    constructor(data, pos) { this.data = data; this.pos = pos; this.buf = 0x80000000; }
    bit() {
      let carry = this.buf >>> 31;
      this.buf = (this.buf << 1) >>> 0;
      if (this.buf === 0) {
        const w = u32(this.data, this.pos); this.pos += 4;
        this.buf = ((w << 1) | carry) >>> 0;
        carry = w >>> 31;
      }
      return carry;
    }
    bits(n) { let v = 0; for (let i = 0; i < n; i++) v = (v << 1) | this.bit(); return v >>> 0; }
    code() { let n = 0; while (n < 7 && this.bit()) n++; return n ? ((1 << n) | this.bits(n)) : 1; }
  }

  function decodeType6(data, offset, size) {
    if (size == null) size = u32(data, offset) >>> 8;
    let p = offset + 4;
    const w0 = u32(data, p); p += 4;
    const tblLen = w0 & 0xFF; let esc = (w0 >>> 8) & 0xFF;
    const nB = (w0 >>> 16) & 0xFF, nC = w0 >>> 24, nA = 8 - nC;
    const table = data.subarray(p, p + tblLen); p += tblLen;
    const br = new BitReader(data, p);
    const out = new Uint8Array(size + 0x200); let n = 0;
    const copy = (dist, len) => {
      const start = n - dist - 1;
      if (start < 0) throw new Error("type6: back-reference before start");
      for (let i = 0; i < len; i++) out[n++] = out[start + i];
    };
    for (;;) {
      if (n >= size) break;
      let v = nC ? br.bits(nC) : 0;
      if (v !== esc) { if (nA) v = (v << nA) | br.bits(nA); out[n++] = v & 0xFF; continue; }
      const c = br.code();
      if (c >= 2) {
        const d = br.code();
        if (d === 0xFF) break;
        let dist = d - 1;
        if (nB) dist = (dist << nB) | br.bits(nB);
        dist = (dist << 8) | br.bits(8);
        copy(dist, c + 1);
      } else if (!br.bit()) {
        copy(br.bits(8), 2);
      } else if (!br.bit()) {
        const newEsc = nC ? br.bits(nC) : 0;
        v = esc; esc = newEsc;
        if (nA) v = (v << nA) | br.bits(nA);
        out[n++] = v & 0xFF;
      } else {
        const c1 = br.code(); let cnt = c1, hi = 0;
        if (c1 >= 0x80) { cnt = ((c1 << 1) | br.bit()) & 0xFF; hi = br.code() - 1; }
        const f = br.code();
        const fill = f < 0x20 ? table[f - 1] : ((f << 3) | br.bits(3)) & 0xFF;
        const total = cnt + 1 + (hi << 8);
        for (let i = 0; i < total; i++) out[n++] = fill;
      }
    }
    return out.subarray(0, size);
  }

  function lz77Decode(data, offset) {
    if ((data[offset] >> 4) !== 1) throw new Error("not an LZ77 header");
    const size = u32(data, offset) >>> 8;
    const out = new Uint8Array(size); let n = 0, p = offset + 4;
    while (n < size) {
      const flags = data[p++];
      for (let bit = 7; bit >= 0 && n < size; bit--) {
        if (flags & (1 << bit)) {
          const b0 = data[p++], b1 = data[p++];
          const len = (b0 >> 4) + 3, disp = (((b0 & 0xF) << 8) | b1) + 1;
          for (let i = 0; i < len && n < size; i++) { out[n] = out[n - disp]; n++; }
        } else out[n++] = data[p++];
      }
    }
    return out;
  }

  function unfilter16(d) {
    const out = new Uint8Array(d); let acc = 0;
    for (let i = 0; i + 1 < out.length; i += 2) {
      acc = (acc + (out[i] | (out[i + 1] << 8))) & 0xFFFF;
      out[i] = acc & 0xFF; out[i + 1] = acc >> 8;
    }
    return out;
  }

  function decode(rom, off) {
    const t = (rom[off] >> 4) & 7, size = u32(rom, off) >>> 8;
    let out;
    if (t === 0) out = rom.slice(off + 4, off + 4 + size);
    else if (t === 1) out = lz77Decode(rom, off);
    else if (t === 6) out = decodeType6(rom, off);
    else throw new Error("unsupported resource type " + t + " at 0x" + off.toString(16));
    if (rom[off] & 0x80) out = unfilter16(out);
    return out;
  }

  // ------------------------------------------------------------------ records and level model
  function records(rom) {
    const out = []; let off = 0x73000;
    while (off + 0x50 <= 0x7A000) {
      let ok = true;
      for (let i = 0; i < 3 && ok; i++) {
        const base = F_LAYERS[i], a = ptr(rom, off + base), b = ptr(rom, off + base + 4);
        if (a === null || b === null) { if (i === 0 || u32(rom, off + base) !== 0) ok = false; continue; }
        if (((rom[a] >> 4) & 7) !== 6 || ((rom[b] >> 4) & 7) !== 6) ok = false;
      }
      const bank = ptr(rom, off + F_BANK), pal = ptr(rom, off + F_PALETTE);
      if (ok && bank !== null && pal !== null && (rom[bank] >> 4) === 0) { out.push(off); off += 0x50; } else off += 4;
    }
    return out;
  }

  function decodePalette(rom, off) {
    const pal = new Array(256);
    for (let i = 0; i < 256; i++) {
      const c = u16(rom, off + i * 2);
      pal[i] = [(c & 31) << 3, ((c >> 5) & 31) << 3, ((c >> 10) & 31) << 3];
    }
    return pal;
  }

  function loadLevel(rom, rec) {
    const bank = ptr(rom, rec + F_BANK);
    const bankSize = u32(rom, bank) >>> 8;
    const tileData = rom.subarray(bank + 4, bank + 4 + bankSize); // 4bpp, 32 bytes per tile
    const nTiles = bankSize >> 5;
    const palOff = ptr(rom, rec + F_PALETTE);
    const palette = palOff !== null ? decodePalette(rom, palOff) : new Array(256).fill([0, 0, 0]);
    const layers = F_LAYERS.map((base) => {
      const m = ptr(rom, rec + base), t = ptr(rom, rec + base + 4);
      if (m === null || t === null) return null;
      const tm = decode(rom, m), mt = decode(rom, t);
      const w = u16(tm, 0), h = u16(tm, 2);
      const cells = new Uint16Array(w * h);
      for (let i = 0; i < w * h; i++) cells[i] = u16(tm, 4 + i * 2);
      const n = u16(mt, 0);
      const refs = new Uint16Array(n * 16);
      for (let i = 0; i < n * 16; i++) refs[i] = u16(mt, 4 + i * 2);
      const attrs = mt.slice(4 + n * 32, 4 + n * 32 + n * 16);
      return { w, h, cells, n, refs, attrs, raw: tm, metaHeader: mt.slice(0, 8) };
    });
    const cm = ptr(rom, rec + F_COLL_MAP), cmeta = ptr(rom, rec + F_COLL_META);
    let collision = null;
    if (cm !== null && cmeta !== null && layers[0]) {
      const c = decode(rom, cm), metas = decode(rom, cmeta);
      const count = u16(c, 0), w = layers[0].w, h = layers[0].h;
      const cells = new Uint16Array(w * h);
      for (let i = 0; i < w * h; i++) cells[i] = u16(c, 4 + i * 2);
      collision = { count, w, h, cells, metas, raw: c };
    }
    return { rec, tileData, nTiles, palette, layers, collision };
  }

  // Paint a metatile into an RGBA buffer (32x32). `opaque` = base layer (index 0 -> palette[0]); else transparent.
  function paintMetatile(level, layer, cid, opaque, rgba) {
    const { tileData, palette, nTiles } = level;
    rgba.fill(0);
    for (let k = 0; k < 16; k++) {
      const t = layer.refs[cid * 16 + k], a = layer.attrs[cid * 16 + k];
      if ((t === 0 && !opaque) || t >= nTiles) continue;
      const hf = a & 1, vf = a & 2, bank = (a >> 2) & 0xF;
      const x0 = (k & 3) * 8, y0 = (k >> 2) * 8, tb = t * 32;
      for (let i = 0; i < 64; i++) {
        const idx = (tileData[tb + (i >> 1)] >> ((i & 1) * 4)) & 0xF;
        if (idx === 0 && !opaque) continue;
        let x = i & 7, y = i >> 3;
        if (hf) x = 7 - x;
        if (vf) y = 7 - y;
        const c = idx ? palette[bank * 16 + idx] : palette[0];
        const o = ((y0 + y) * 32 + x0 + x) * 4;
        rgba[o] = c[0]; rgba[o + 1] = c[1]; rgba[o + 2] = c[2]; rgba[o + 3] = 255;
      }
    }
    if (opaque) for (let o = 3; o < rgba.length; o += 4) rgba[o] = 255;
  }

  // Colour class of a collision byte for the overlay.
  function collisionClass(v) {
    if (v === 0) return 0;          // open
    if (v === 3) return 1;          // blocked (outside / walls)
    if ((v & 0x40) && (v & 0x3F) <= 3) return 2; // walkable pavement (0x40, 0x43)
    return 3;                       // special (edges, steps, 0x0c-0x13, 0x4c-0x4f)
  }

  // ------------------------------------------------------------------ encoders
  function lz77Encode(raw) {
    const n = raw.length, minDisp = 2;
    const out = [0x10, n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF];
    const table = new Map();
    const key = (i) => (raw[i] << 16) | (raw[i + 1] << 8) | raw[i + 2];
    const add = (i) => { if (i + 3 <= n) { const k = key(i); let l = table.get(k); if (!l) table.set(k, l = []); l.push(i); } };
    let i = 0;
    while (i < n) {
      const flagPos = out.length; out.push(0); let flags = 0;
      for (let bit = 7; bit >= 0 && i < n; bit--) {
        let bestLen = 0, bestDisp = 0;
        if (i + 3 <= n) {
          const cands = table.get(key(i));
          if (cands) {
            const lo = Math.max(0, cands.length - 64);
            for (let ci = cands.length - 1; ci >= lo; ci--) {
              const j = cands[ci], disp = i - j;
              if (disp < minDisp) continue;
              if (disp > 0x1000) break;
              let len = 0; const limit = Math.min(18, n - i);
              while (len < limit && raw[j + len] === raw[i + len]) len++;
              if (len > bestLen) { bestLen = len; bestDisp = disp; if (len === 18) break; }
            }
          }
        }
        if (bestLen >= 3) {
          flags |= 1 << bit;
          out.push(((bestLen - 3) << 4) | ((bestDisp - 1) >> 8), (bestDisp - 1) & 0xFF);
          for (let k = i; k < i + bestLen; k++) add(k);
          i += bestLen;
        } else { out.push(raw[i]); add(i); i++; }
      }
      out[flagPos] = flags;
    }
    while (out.length % 4) out.push(0);
    return Uint8Array.from(out);
  }

  const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let i = 0; i < 256; i++) { let c = i; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[i] = c >>> 0; }
    return t;
  })();
  function crc32(b) { let c = 0xFFFFFFFF; for (let i = 0; i < b.length; i++) c = CRC_TABLE[(c ^ b[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }

  function upsVarint(n, out) { for (;;) { const x = n & 0x7F; n = Math.floor(n / 128); if (n === 0) { out.push(x | 0x80); return; } out.push(x); n -= 1; } }
  function upsReadVarint(buf, st) { let n = 0, shift = 1; for (;;) { const x = buf[st.pos++]; n += (x & 0x7F) * shift; if (x & 0x80) return n; shift *= 128; n += shift; } }
  function pushU32(out, v) { out.push(v & 0xFF, (v >>> 8) & 0xFF, (v >>> 16) & 0xFF, (v >>> 24) & 0xFF); }

  function upsMake(orig, mod) {
    const p = [0x55, 0x50, 0x53, 0x31]; upsVarint(orig.length, p); upsVarint(mod.length, p);
    const n = Math.max(orig.length, mod.length);
    const A = (i) => (i < orig.length ? orig[i] : 0), B = (i) => (i < mod.length ? mod[i] : 0);
    let i = 0, last = 0;
    while (i < n) {
      if (A(i) === B(i)) { i++; continue; }
      upsVarint(i - last, p);
      while (i < n && A(i) !== B(i)) { p.push(A(i) ^ B(i)); i++; }
      p.push(0); i++; last = i;
    }
    pushU32(p, crc32(orig)); pushU32(p, crc32(mod));
    const body = Uint8Array.from(p); const tail = []; pushU32(tail, crc32(body));
    const out = new Uint8Array(body.length + 4); out.set(body); out.set(tail, body.length);
    return out;
  }

  function upsApply(orig, patch) {
    if (String.fromCharCode(...patch.subarray(0, 4)) !== "UPS1") throw new Error("not a UPS file");
    const inCrc = u32(patch, patch.length - 12), outCrc = u32(patch, patch.length - 8), pCrc = u32(patch, patch.length - 4);
    if (crc32(patch.subarray(0, patch.length - 4)) !== pCrc) throw new Error("UPS file is corrupt");
    if (crc32(orig) !== inCrc) throw new Error("this patch is for a different ROM (CRC32 " + crc32(orig).toString(16) + ", patch expects " + inCrc.toString(16) + ")");
    const st = { pos: 4 }; upsReadVarint(patch, st); const outSize = upsReadVarint(patch, st);
    const out = new Uint8Array(Math.max(outSize, orig.length)); out.set(orig);
    let i = 0; const end = patch.length - 12;
    while (st.pos < end) {
      i += upsReadVarint(patch, st);
      while (patch[st.pos]) { if (i < out.length) out[i] ^= patch[st.pos]; i++; st.pos++; }
      st.pos++; i++;
    }
    const res = out.subarray(0, outSize);
    if (crc32(res) !== outCrc) throw new Error("patched output failed its CRC check");
    return res;
  }

  // ------------------------------------------------------------------ write-back
  function freeSpaceStart(rom) {
    let last = rom.length - 1;
    while (last > 0 && rom[last] === 0) last--;
    return (last + 1 + 0x10 + 3) & ~3;
  }

  // Both blobs are `4-byte header, u16 cells`. Write the cells into a copy of the original decoded bytes so the declared
  // size and any trailing padding stay exactly as the game shipped them.
  function serializeCells(raw, cells) {
    const out = new Uint8Array(raw);
    for (let i = 0; i < cells.length; i++) { out[4 + i * 2] = cells[i] & 0xFF; out[5 + i * 2] = cells[i] >> 8; }
    return out;
  }

  // edits: array of {rec, layers: [cells|null x3], collision: cells|null}. Returns {rom, log, used, free}.
  function applyEdits(rom, edits) {
    const out = new Uint8Array(rom); let cursor = freeSpaceStart(rom); const log = [];
    const place = (blob, rec, field, label) => {
      if (cursor + blob.length > out.length) throw new Error("out of free space (" + label + ")");
      out.set(blob, cursor);
      const off = rec + field, old = u32(out, off), addr = ROM_BASE + cursor;
      out[off] = addr & 0xFF; out[off + 1] = (addr >>> 8) & 0xFF; out[off + 2] = (addr >>> 16) & 0xFF; out[off + 3] = addr >>> 24;
      log.push({ rec, label, old, addr, size: blob.length });
      cursor = (cursor + blob.length + 3) & ~3;
    };
    for (const e of edits) {
      const level = loadLevel(rom, e.rec);
      for (let L = 0; L < 3; L++) {
        if (!e.layers[L] || !level.layers[L]) continue;
        place(lz77Encode(serializeCells(level.layers[L].raw, e.layers[L])), e.rec, F_LAYERS[L], "layer" + L);
      }
      if (e.collision && level.collision) place(lz77Encode(serializeCells(level.collision.raw, e.collision)), e.rec, F_COLL_MAP, "collision");
    }
    return { rom: out, log, used: cursor - freeSpaceStart(rom), free: out.length - cursor };
  }

  function romInfo(rom) {
    const s = (o, n) => String.fromCharCode(...rom.subarray(o, o + n)).replace(/\0+$/, "");
    return { title: s(0xA0, 12), code: s(0xAC, 4), maker: s(0xB0, 2), size: rom.length };
  }

  return { ROM_BASE, F_LAYERS, F_COLL_META, F_COLL_MAP, F_OBJECTS, F_BANK, F_PALETTE, KNOWN_SHA1, u16, u32, ptr,
           decodeType6, lz77Decode, unfilter16, decode, records, decodePalette, loadLevel, paintMetatile, collisionClass,
           lz77Encode, crc32, upsMake, upsApply, freeSpaceStart, serializeCells, applyEdits, romInfo };
});
