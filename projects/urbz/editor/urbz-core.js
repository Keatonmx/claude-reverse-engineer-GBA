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

  function decodeType6(data, offset, size, info) {
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
    if (info) info.end = br.pos;
    return out.subarray(0, size);
  }

  function lz77Decode(data, offset, info) {
    if (((data[offset] >> 4) & 7) !== 1) throw new Error("not an LZ77 header");
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
    if (info) info.end = p;
    return out;
  }

  function rleDecode(data, offset, info) {
    const size = u32(data, offset) >>> 8;
    const out = new Uint8Array(size); let n = 0, p = offset + 4;
    while (n < size) {
      const f = data[p++];
      if (f & 0x80) { const len = (f & 0x7F) + 3, b = data[p++]; for (let i = 0; i < len && n < size; i++) out[n++] = b; }
      else { const len = (f & 0x7F) + 1; for (let i = 0; i < len && n < size; i++) out[n++] = data[p++]; }
    }
    if (info) info.end = p;
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

  function decode(rom, off, info) {
    const t = (rom[off] >> 4) & 7, size = u32(rom, off) >>> 8;
    let out;
    if (t === 0) { out = rom.slice(off + 4, off + 4 + size); if (info) info.end = off + 4 + size; }
    else if (t === 1) out = lz77Decode(rom, off, info);
    else if (t === 3) out = rleDecode(rom, off, info);
    else if (t === 6) out = decodeType6(rom, off, undefined, info);
    else throw new Error("unsupported resource type " + t + " at 0x" + off.toString(16));
    if (rom[off] & 0x80) out = unfilter16(out);
    return out;
  }
  // Byte extent of a resource blob in the ROM (header to end of stream, 4-byte aligned).
  function blobExtent(rom, off) {
    const info = {}; decode(rom, off, info);
    return ((info.end + 3) & ~3) - off;
  }

  // ------------------------------------------------------------------ records and level model
  // The district table: 80-byte records on a fixed 0x50 stride from 0x73568 (the game computes 0x08073568 + index*0x50).
  function records(rom) {
    const out = [];
    for (let i = 0; ; i++) {
      const off = 0x73568 + i * 0x50;
      const m = ptr(rom, off), t = ptr(rom, off + 4), bank = ptr(rom, off + F_BANK);
      if (m === null || t === null || bank === null || (rom[bank] >> 4) !== 0) break;
      out.push(off);
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
      return { w, h, cells, n, refs, attrs, raw: tm, metaRaw: mt };
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


  // ------------------------------------------------------------------ type-6 encoder (optimal parse)
  function filter16(d) {
    const out = new Uint8Array(d); let prev = 0;
    for (let i = 0; i + 1 < out.length; i += 2) { const v = out[i] | (out[i + 1] << 8); const x = (v - prev) & 0xFFFF; prev = v; out[i] = x & 0xFF; out[i + 1] = x >> 8; }
    return out;
  }
  class BitWriter {
    constructor() { this.words = []; this.cur = 0; this.n = 0; }
    bit(b) { this.cur = ((this.cur << 1) | (b & 1)) >>> 0; if (++this.n === 32) { this.words.push(this.cur); this.cur = 0; this.n = 0; } }
    bits(v, n) { for (let i = n - 1; i >= 0; i--) this.bit((v >>> i) & 1); }
    code(v) { const n = 31 - Math.clz32(v); for (let i = 0; i < n; i++) this.bit(1); if (n < 7) this.bit(0); this.bits(v & ((1 << n) - 1), n); }
    finish() {
      if (this.n) this.words.push((this.cur << (32 - this.n)) >>> 0);
      this.words.push(0);
      const out = new Uint8Array(this.words.length * 4);
      this.words.forEach((w, i) => { out[i * 4] = w & 0xFF; out[i * 4 + 1] = (w >>> 8) & 0xFF; out[i * 4 + 2] = (w >>> 16) & 0xFF; out[i * 4 + 3] = w >>> 24; });
      return out;
    }
  }
  const costCode = (v) => { const k = 31 - Math.clz32(v); return 2 * k + (k < 7 ? 1 : 0); };
  const MATCH_LENS = [3, 4, 8, 16, 32, 64, 128, 256], FILL_LENS = [2, 3, 4, 8, 16, 32, 64, 128];

  function encodeType6Stream(raw, nC, esc, table) {
    const nA = 8 - nC, n = raw.length;
    const tblIndex = new Int16Array(256).fill(-1); table.forEach((b, i) => { if (tblIndex[b] < 0) tblIndex[b] = i + 1; });
    const litCost = (v) => (nC && (v >> nA) !== esc ? 8 : nC + 11);
    const fillCost = (r, f) => { if (tblIndex[f] < 0) return 1e9; const c = nC + 3 + (r <= 128 ? costCode(r - 1) : 16 + costCode(((r - 1) >> 8) + 1)); return c + costCode(tblIndex[f]); };
    const runlen = new Int32Array(n + 1); runlen[n] = 0; if (n) runlen[n - 1] = 1;
    for (let i = n - 2; i >= 0; i--) runlen[i] = raw[i] === raw[i + 1] ? runlen[i + 1] + 1 : 1;
    const short = new Int32Array(n); const pairs = new Map(); const heads = new Map(); const cands = new Array(n);
    for (let i = 0; i < n; i++) {
      if (i + 2 <= n) { const k2 = (raw[i] << 8) | raw[i + 1]; const j = pairs.get(k2); if (j !== undefined && i - j <= 256) short[i] = i - j; pairs.set(k2, i); }
      if (i + 3 <= n) { const k3 = (raw[i] << 16) | (raw[i + 1] << 8) | raw[i + 2]; let lst = heads.get(k3); if (!lst) heads.set(k3, lst = []); cands[i] = lst.slice(-12); lst.push(i); }
    }
    const cost = new Float64Array(n + 1); const chKind = new Uint8Array(n), chLen = new Int32Array(n), chDist = new Int32Array(n);
    cost[n] = 0;
    for (let i = n - 1; i >= 0; i--) {
      const b = raw[i]; let best = litCost(b) + cost[i + 1], kind = 0, len = 1, dist = 0;
      const r = Math.min(runlen[i], 65280);
      if (r >= 2) for (const L of FILL_LENS.concat([r])) if (L >= 2 && L <= r) { const c = fillCost(L, b) + cost[i + L]; if (c < best) { best = c; kind = 1; len = L; } }
      if (short[i]) { const c = nC + 10 + cost[i + 2]; if (c < best) { best = c; kind = 3; len = 2; dist = short[i]; } }
      if (cands[i] && cands[i].length) {
        const lim = Math.min(256, n - i);
        for (let ci = cands[i].length - 1; ci >= 0; ci--) {
          const j = cands[i][ci], d = i - j; if (d > 254 * 256) continue;
          let L = 3; while (L < lim && raw[j + L] === raw[i + L]) L++;
          const dc = nC + costCode(((d - 1) >> 8) + 1) + 8;
          for (const LL of MATCH_LENS.concat([L])) if (LL >= 3 && LL <= L) { const c = dc + costCode(LL - 1) + cost[i + LL]; if (c < best) { best = c; kind = 2; len = LL; dist = d; } }
        }
      }
      cost[i] = best; chKind[i] = kind; chLen[i] = len; chDist[i] = dist;
    }
    const bw = new BitWriter();
    for (let i = 0; i < n;) {
      const kind = chKind[i], L = chLen[i], dist = chDist[i];
      if (kind === 0) { const v = raw[i]; if (nC && (v >> nA) !== esc) bw.bits(v, 8); else { bw.bits(esc, nC); bw.bit(0); bw.bit(1); bw.bit(0); bw.bits(esc, nC); bw.bits(v & ((1 << nA) - 1), nA); } }
      else if (kind === 1) { bw.bits(esc, nC); bw.bit(0); bw.bit(1); bw.bit(1); if (L <= 128) bw.code(L - 1); else { const cnt = (L - 1) & 0xFF, hi = (L - 1) >> 8; bw.code(0x80 | (cnt >> 1)); bw.bit(cnt & 1); bw.code(hi + 1); } bw.code(tblIndex[raw[i]]); }
      else if (kind === 2) { const d = dist - 1; bw.bits(esc, nC); bw.code(L - 1); bw.code((d >> 8) + 1); bw.bits(d & 0xFF, 8); }
      else { bw.bits(esc, nC); bw.bit(0); bw.bit(0); bw.bits(dist - 1, 8); }
      i += L;
    }
    bw.bits(esc, nC); bw.code(2); bw.code(0xFF);
    return bw.finish();
  }

  // Complete type-6 resource. filtered=true stores halfword differences (header 0xE0), as the game does for maps and
  // metatiles. The dictionary is padded to a multiple of 4 because the IWRAM decoder fetches the bitstream with word
  // loads (verified against the game's own decoder through the emulator oracle).
  function encodeType6(raw, filtered, nCs) {
    if (filtered && raw.length % 2) throw new Error("Diff16 needs an even length");
    const payload = filtered ? filter16(raw) : raw;
    const runs = new Map();
    for (let i = 0; i < payload.length;) { let j = i; while (j < payload.length && payload[j] === payload[i]) j++; if (j - i >= 2) runs.set(payload[i], (runs.get(payload[i]) || 0) + 1); i = j; }
    let table = [...runs.entries()].filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 28).map(([b]) => b);
    while (table.length % 4) table.push(table.length ? table[0] : 0);
    let best = null;
    for (const nC of nCs || [0, 1, 2, 3]) {
      const hist = new Int32Array(1 << nC); for (const b of payload) hist[b >> (8 - nC)]++;
      let esc = 0; if (nC) { for (let v = 1; v < hist.length; v++) if (hist[v] < hist[esc]) esc = v; }
      const stream = encodeType6Stream(payload, nC, esc, table);
      if (!best || stream.length < best.stream.length) best = { stream, nC, esc };
    }
    const out = new Uint8Array(8 + table.length + best.stream.length);
    const hdr = ((0x60 | (filtered ? 0x80 : 0)) | (raw.length << 8)) >>> 0;
    out[0] = hdr & 0xFF; out[1] = (hdr >>> 8) & 0xFF; out[2] = (hdr >>> 16) & 0xFF; out[3] = hdr >>> 24;
    out[4] = table.length; out[5] = best.esc; out[6] = 0; out[7] = best.nC;
    out.set(table, 8); out.set(best.stream, 8 + table.length);
    return out;
  }

  // Smallest blob the loader accepts for `raw`: type 6 (filtered when even), LZ77 (+Diff16, header 0x90, which the
  // game itself ships) or plain LZ77. Every candidate is decoded back before it is allowed to win.
  function encodeBest(raw, allowFilter) {
    const even = raw.length % 2 === 0, cands = [];
    const tryAdd = (fn) => { try { const b = fn(); if (bytesEqual(decode(b, 0), raw)) cands.push(b); } catch (e) { /* skip */ } };
    if (allowFilter && even) tryAdd(() => encodeType6(raw, true));
    tryAdd(() => encodeType6(raw, false));
    if (allowFilter && even) tryAdd(() => { const b = lz77Encode(filter16(raw)); b[0] = 0x90; return b; });
    tryAdd(() => lz77Encode(raw));
    if (!cands.length) throw new Error("no encoder produced a valid blob");
    return cands.reduce((a, b) => (b.length < a.length ? b : a));
  }
  function bytesEqual(a, b) { if (a.length !== b.length) return false; for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false; return true; }


  // ------------------------------------------------------------------ art import (reskin pieces in place)
  // The tile bank is raw and read straight from ROM by the tile cache, and EA's pipeline left no unused tiles or
  // pieces, so new art replaces the pixels of an existing piece's 16 tiles. Palettes are 16 banks x 16 BGR555 colours.
  const rgb555 = (c) => [(c[0] >> 3) << 3, (c[1] >> 3) << 3, (c[2] >> 3) << 3];
  const dist2 = (a, b) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2;

  // Detect the integer scale of a blocky upscaled image (largest k in 8,4,2 where every kxk block is flat).
  function detectScale(rgba, w, h) {
    outer: for (const k of [8, 4, 2]) {
      if (w % k || h % k) continue;
      for (let y = 0; y < h; y += k) for (let x = 0; x < w; x += k) {
        const o = (y * w + x) * 4;
        for (let dy = 0; dy < k; dy++) for (let dx = 0; dx < k; dx++) { const p = ((y + dy) * w + x + dx) * 4; if (rgba[p] !== rgba[o] || rgba[p + 1] !== rgba[o + 1] || rgba[p + 2] !== rgba[o + 2] || rgba[p + 3] !== rgba[o + 3]) continue outer; }
      }
      return k;
    }
    return 1;
  }
  // Nearest-neighbour downscale by k (samples the block centre, so slightly noisy AI output still works).
  function downscale(rgba, w, h, k) {
    const W = Math.floor(w / k), H = Math.floor(h / k), out = new Uint8ClampedArray(W * H * 4), c = k >> 1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const src = ((y * k + c) * w + x * k + c) * 4, dst = (y * W + x) * 4; out[dst] = rgba[src]; out[dst + 1] = rgba[src + 1]; out[dst + 2] = rgba[src + 2]; out[dst + 3] = rgba[src + 3]; }
    return { rgba: out, w: W, h: H };
  }
  const isKey = (r, g, b, a) => a < 128 || (r >= 240 && g <= 32 && b >= 240);

  // Median-cut to at most 15 colours (index 0 stays transparent). Returns 16 entries, entry 0 = [0,0,0].
  function buildPalette(rgba, maxColours = 15) {
    const seen = new Map();
    for (let i = 0; i < rgba.length; i += 4) { if (isKey(rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3])) continue; const c = rgb555([rgba[i], rgba[i + 1], rgba[i + 2]]); const k = (c[0] << 16) | (c[1] << 8) | c[2]; seen.set(k, (seen.get(k) || 0) + 1); }
    let boxes = [[...seen.entries()].map(([k, n]) => ({ c: [k >> 16, (k >> 8) & 255, k & 255], n }))];
    while (boxes.length < maxColours) {
      boxes.sort((a, b) => b.length - a.length); const box = boxes[0]; if (box.length < 2) break;
      const ranges = [0, 1, 2].map((ch) => Math.max(...box.map((p) => p.c[ch])) - Math.min(...box.map((p) => p.c[ch])));
      const ch = ranges.indexOf(Math.max(...ranges)); box.sort((a, b) => a.c[ch] - b.c[ch]);
      const total = box.reduce((s, p) => s + p.n, 0); let acc = 0, cut = 0; while (cut < box.length - 1 && acc < total / 2) acc += box[cut++].n;
      boxes.splice(0, 1, box.slice(0, cut), box.slice(cut));
    }
    const pal = [[0, 0, 0]];
    for (const box of boxes) { if (!box.length) continue; const n = box.reduce((s, p) => s + p.n, 0); pal.push(rgb555([0, 1, 2].map((ch) => Math.round(box.reduce((s, p) => s + p.c[ch] * p.n, 0) / n)))); }
    while (pal.length < 16) pal.push([0, 0, 0]);
    return pal;
  }

  // Quantize one 8x8 tile against a 16-colour bank (index 0 transparent). Returns {pixels: Uint8Array(64), error}.
  function quantizeTile(rgba, w, x0, y0, bank) {
    const px = new Uint8Array(64); let err = 0;
    for (let i = 0; i < 64; i++) {
      const x = x0 + (i & 7), y = y0 + (i >> 3), o = (y * w + x) * 4;
      if (isKey(rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3])) { px[i] = 0; continue; }
      const c = [rgba[o], rgba[o + 1], rgba[o + 2]]; let best = 1, bd = Infinity;
      for (let k = 1; k < 16; k++) { const d = dist2(c, bank[k]); if (d < bd) { bd = d; best = k; } }
      px[i] = best; err += bd;
    }
    return { pixels: px, error: err };
  }
  function packTile(px) { const out = new Uint8Array(32); for (let i = 0; i < 64; i += 2) out[i >> 1] = (px[i] & 15) | ((px[i + 1] & 15) << 4); return out; }

  // Import a 32x32 (after downscale) image onto piece `cid` of layer L. opts: {scale: 0|1|2|4|8, palette: "auto" |
  // bankIndex | {newInto: bankIndex}, banks: allowed bank indices for "auto"}. Mutates level.tileData / level.palette
  // copies handed in via `out` ({tileData, palette}) and the layer's attrs; returns a report.
  function importPiece(level, L, cid, rgba, w, h, opts, out) {
    const k = opts.scale || detectScale(rgba, w, h);
    let img = { rgba, w, h }; if (k > 1) img = downscale(rgba, w, h, k);
    if (img.w < 32 || img.h < 32) throw new Error(`image is ${img.w}x${img.h} after dividing by ${k}; a piece needs 32x32`);
    if (opts.offsetX || opts.offsetY) { // take a 32x32 window out of a larger image (multi-piece objects)
      const ox = opts.offsetX || 0, oy = opts.offsetY || 0, win = new Uint8ClampedArray(32 * 32 * 4);
      for (let y = 0; y < 32; y++) for (let x = 0; x < 32; x++) { const so = ((oy + y) * img.w + ox + x) * 4, d = (y * 32 + x) * 4; for (let c = 0; c < 4; c++) win[d + c] = img.rgba[so + c]; }
      img = { rgba: win, w: 32, h: 32 };
    }
    if (opts.underCid != null) { // composite over another piece's pixels (an object standing on existing pavement)
      const under = new Uint8ClampedArray(32 * 32 * 4); const uL = opts.underLayer == null ? L : opts.underLayer;
      paintMetatile(level, level.layers[uL], opts.underCid, true, under);
      const comp = new Uint8ClampedArray(32 * 32 * 4);
      for (let i = 0; i < 32 * 32; i++) { const o = i * 4; const src = img.rgba; if (isKey(src[o], src[o + 1], src[o + 2], src[o + 3])) { for (let c = 0; c < 4; c++) comp[o + c] = under[o + c]; } else { for (let c = 0; c < 4; c++) comp[o + c] = src[o + c]; } }
      img = { rgba: comp, w: 32, h: 32 };
    }
    const lay = level.layers[L], palette = out.palette, tileData = out.tileData;
    let banks;
    if (opts.palette && typeof opts.palette === "object") {
      const pal = buildPalette(img.rgba); const b = opts.palette.newInto;
      for (let i = 0; i < 16; i++) palette[b * 16 + i] = pal[i];
      banks = [b];
    } else if (typeof opts.palette === "number") banks = [opts.palette];
    else banks = opts.banks && opts.banks.length ? opts.banks : [...new Set([...Array(16).keys()].map((kk) => (lay.attrs[cid * 16 + kk] >> 2) & 15))];
    const bankPal = (b) => palette.slice(b * 16, b * 16 + 16);
    // how many other pieces (all layers) share each tile of this piece
    const shared = [];
    for (let kk = 0; kk < 16; kk++) {
      const t = lay.refs[cid * 16 + kk]; let n = 0;
      for (let L2 = 0; L2 < 3; L2++) { const l2 = level.layers[L2]; if (!l2) continue; for (let i = 0; i < l2.n * 16; i++) if (l2.refs[i] === t && !(L2 === L && (i >> 4) === cid)) n++; }
      shared.push(n);
    }
    let totalErr = 0; const used = new Set(); const painted = new Map(); const duplicates = [];
    for (let kk = 0; kk < 16; kk++) {
      const x0 = (kk & 3) * 8, y0 = (kk >> 2) * 8; let best = null;
      for (const b of banks) { const q = quantizeTile(img.rgba, img.w, x0, y0, bankPal(b)); if (!best || q.error < best.q.error) best = { q, b }; }
      const t = lay.refs[cid * 16 + kk];
      if (painted.has(t)) { duplicates.push(kk); lay.attrs[cid * 16 + kk] = lay.attrs[cid * 16 + painted.get(t)]; continue; } // a piece cannot hold two images in one tile
      painted.set(t, kk);
      tileData.set(packTile(best.q.pixels), t * 32);
      lay.attrs[cid * 16 + kk] = (best.b << 2); // new art is stored unflipped
      totalErr += best.q.error; used.add(best.b);
    }
    return { scale: k, banks: [...used], sharedTiles: shared.filter((n) => n > 0).length, shareCounts: shared, duplicateSlots: duplicates, meanError: Math.sqrt(totalErr / 1024) };
  }

  // How private a piece's tiles are: distinct tiles, and how many of them no other piece (any layer) uses.
  function pieceStats(level, L, cid) {
    const lay = level.layers[L]; const tiles = new Set(); for (let kk = 0; kk < 16; kk++) tiles.add(lay.refs[cid * 16 + kk]);
    const others = new Map();
    for (let L2 = 0; L2 < 3; L2++) { const l2 = level.layers[L2]; if (!l2) continue; for (let i = 0; i < l2.n * 16; i++) { const t = l2.refs[i]; if (tiles.has(t) && !(L2 === L && (i >> 4) === cid)) others.set(t, (others.get(t) || 0) + 1); } }
    return { distinct: tiles.size, exclusive: tiles.size - others.size, sharedUses: [...others.values()].reduce((a, b) => a + b, 0) };
  }
  // Pieces of a layer ranked as reskin targets: 16 distinct tiles first, then most exclusive, then least placed.
  function bestTargets(level, L, limit = 20) {
    const lay = level.layers[L]; const placed = new Int32Array(lay.n); for (const c of lay.cells) if (c < lay.n) placed[c]++;
    const rows = []; for (let cid = 0; cid < lay.n; cid++) { const st = pieceStats(level, L, cid); rows.push({ cid, ...st, placed: placed[cid] }); }
    rows.sort((a, b) => (b.distinct - a.distinct) || (b.exclusive - a.exclusive) || (a.placed - b.placed));
    return rows.slice(0, limit);
  }

  // ------------------------------------------------------------------ write-back
  function freeSpaceStart(rom) {
    let last = rom.length - 1;
    while (last > 0 && rom[last] === 0) last--;
    return (last + 1 + 0x10 + 3) & ~3;
  }

  // Both maps and the collision map are `4-byte header, u16 cells`. Write the cells into a copy of the original decoded
  // bytes so the declared size and any trailing padding stay exactly as the game shipped them.
  function serializeCells(raw, cells) {
    const out = new Uint8Array(raw);
    for (let i = 0; i < cells.length; i++) { out[4 + i * 2] = cells[i] & 0xFF; out[5 + i * 2] = cells[i] >> 8; }
    return out;
  }
  // Metatile blob: 8-byte header, count x 16 u16 tile refs, then count x 16 attribute bytes (structure of arrays).
  function serializeMetatiles(metaRaw, n, refs, attrs) {
    const out = new Uint8Array(metaRaw);
    for (let i = 0; i < n * 16; i++) { out[4 + i * 2] = refs[i] & 0xFF; out[5 + i * 2] = refs[i] >> 8; }
    out.set(attrs.subarray(0, n * 16), 4 + n * 32);
    return out;
  }

  // Free-space allocator: the zero tail of the ROM plus the bytes of every blob a redirected pointer abandons (each
  // district blob is referenced exactly once, so a replaced blob is dead). Best fit, 4-byte aligned.
  class Allocator {
    constructor(rom) { this.regions = [{ start: freeSpaceStart(rom), end: rom.length }]; this.tailStart = this.regions[0].start; }
    free(start, len) {
      start = (start + 3) & ~3; const end = start + len;
      if (end - start < 8) return;
      this.regions.push({ start, end });
      this.regions.sort((a, b) => a.start - b.start);
      const merged = [];
      for (const r of this.regions) { const last = merged[merged.length - 1]; if (last && r.start <= last.end) last.end = Math.max(last.end, r.end); else merged.push({ ...r }); }
      this.regions = merged;
    }
    alloc(len) {
      let best = null;
      for (const r of this.regions) { const size = r.end - r.start; if (size >= len && (!best || size < best.end - best.start)) best = r; }
      if (!best) return -1;
      const addr = best.start; best.start = (addr + len + 3) & ~3;
      if (best.end - best.start < 8) this.regions.splice(this.regions.indexOf(best), 1);
      return addr;
    }
    tailUsed(rom) { const tail = this.regions.find((r) => r.end === rom.length); return rom.length - (tail ? tail.start : rom.length) === 0 ? rom.length - this.tailStart : (tail.start - this.tailStart); }
  }

  // edits: [{rec, layers: [cells|null x3], collision: cells|null, metas: [{refs, attrs}|null x3]}]
  // Returns {rom, log, tailUsed, tailFree}.
  function applyEdits(rom, edits) {
    const out = new Uint8Array(rom); const alloc = new Allocator(rom); const log = [];
    const jobs = [];
    for (const e of edits) {
      const level = loadLevel(rom, e.rec);
      for (let L = 0; L < 3; L++) {
        const lay = level.layers[L]; if (!lay) continue;
        if (e.layers && e.layers[L]) jobs.push({ rec: e.rec, field: F_LAYERS[L], label: "layer" + L, blob: encodeBest(serializeCells(lay.raw, e.layers[L]), true) });
        if (e.metas && e.metas[L]) jobs.push({ rec: e.rec, field: F_LAYERS[L] + 4, label: "metatiles" + L, blob: encodeBest(serializeMetatiles(lay.metaRaw, lay.n, e.metas[L].refs, e.metas[L].attrs), true) });
      }
      if (e.collision && level.collision) jobs.push({ rec: e.rec, field: F_COLL_MAP, label: "collision", blob: encodeBest(serializeCells(level.collision.raw, e.collision), true) });
      if (e.tileData) { // raw bank: header kept, pixels replaced; same size so it goes back into its own slot
        const bank = ptr(rom, e.rec + F_BANK); const blob = new Uint8Array(4 + e.tileData.length); blob.set(rom.subarray(bank, bank + 4)); blob.set(e.tileData, 4);
        jobs.push({ rec: e.rec, field: F_BANK, label: "tilebank", blob });
      }
      if (e.palette) { const blob = new Uint8Array(512); for (let i = 0; i < 256; i++) { const c = e.palette[i]; const v = (c[0] >> 3) | ((c[1] >> 3) << 5) | ((c[2] >> 3) << 10); blob[i * 2] = v & 0xFF; blob[i * 2 + 1] = v >> 8; } jobs.push({ rec: e.rec, field: F_PALETTE, label: "palette", blob, rawExtent: 512 }); }
    }
    // release every replaced blob first, then place (a blob may land in its own old slot when it fits)
    for (const j of jobs) { j.old = u32(out, j.rec + j.field); const off = j.old - ROM_BASE; try { alloc.free(off, j.rawExtent || blobExtent(rom, off)); } catch (err) { /* unknown extent: do not reclaim */ } }
    jobs.sort((a, b) => b.blob.length - a.blob.length); // largest first: best fit then wastes the least
    for (const j of jobs) {
      const addr = alloc.alloc(j.blob.length);
      if (addr < 0) throw new Error("out of free space while placing " + j.label + " (" + j.blob.length + " bytes)");
      out.set(j.blob, addr);
      const off = j.rec + j.field, a = ROM_BASE + addr;
      out[off] = a & 0xFF; out[off + 1] = (a >>> 8) & 0xFF; out[off + 2] = (a >>> 16) & 0xFF; out[off + 3] = a >>> 24;
      log.push({ rec: j.rec, label: j.label, old: j.old, addr: a, size: j.blob.length, inPlace: addr < alloc.tailStart });
    }
    const tail = alloc.regions.find((r) => r.end === out.length);
    const tailUsed = (tail ? tail.start : out.length) - alloc.tailStart;
    return { rom: out, log, tailUsed, tailFree: out.length - alloc.tailStart - tailUsed };
  }

  function romInfo(rom) {
    const s = (o, n) => String.fromCharCode(...rom.subarray(o, o + n)).replace(/\0+$/, "");
    return { title: s(0xA0, 12), code: s(0xAC, 4), maker: s(0xB0, 2), size: rom.length };
  }

  return { ROM_BASE, F_LAYERS, F_COLL_META, F_COLL_MAP, F_OBJECTS, F_BANK, F_PALETTE, KNOWN_SHA1, u16, u32, ptr,
           decodeType6, lz77Decode, rleDecode, unfilter16, decode, records, decodePalette, loadLevel, paintMetatile, collisionClass,
           lz77Encode, filter16, encodeType6, encodeBest, blobExtent, Allocator, crc32, upsMake, upsApply, freeSpaceStart,
           serializeCells, serializeMetatiles, applyEdits, romInfo, detectScale, downscale, buildPalette, quantizeTile, packTile, importPiece, pieceStats, bestTargets };
});
