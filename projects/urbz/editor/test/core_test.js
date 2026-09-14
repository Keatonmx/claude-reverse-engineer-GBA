// node core_test.js path/to/urbz.gba
// Round-trips every district blob through the JS decoders and the best-of encoder, then exercises the write-back
// (allocator with slot reclaim, map + collision + metatile edits) and checks the patched ROM decodes to the edits.
const fs = require("fs"); const path = require("path");
const C = require(path.join(__dirname, "..", "urbz-core.js"));
const rom = new Uint8Array(fs.readFileSync(process.argv[2]));
const eq = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
const recs = C.records(rom); console.log("districts:", recs.length);
let n = 0, fails = 0, orig = 0, ours = 0; const t0 = Date.now();
for (const r of recs) for (const f of [0, 4, 0x10, 0x14, 0x20, 0x24, 0x30, 0x34]) {
  const a = C.ptr(rom, r + f); if (a === null || ((rom[a] >> 4) & 7) !== 6) continue;
  const d = C.decode(rom, a), b = C.encodeBest(d, true); n++;
  if (!eq(C.decode(b, 0), d)) fails++;
  orig += C.blobExtent(rom, a); ours += b.length;
}
console.log(`type-6 blobs ${n}, roundtrip failures ${fails}, original ${orig} B, re-encoded ${ours} B (${(100 * ours / orig).toFixed(0)}%), ${Date.now() - t0} ms`);
const rec = recs[62] || recs[0]; const lvl = C.loadLevel(rom, rec);
const e = { rec, layers: [new Uint16Array(lvl.layers[0].cells), null, null], collision: lvl.collision ? new Uint16Array(lvl.collision.cells) : null, metas: [null, null, null] };
for (let y = 7; y < 12; y++) for (let x = 8; x < 10; x++) { if (y < lvl.layers[0].h && x < lvl.layers[0].w) { e.layers[0][y * lvl.layers[0].w + x] = 0; if (e.collision) e.collision[y * lvl.layers[0].w + x] = 0; } }
const refs = new Uint16Array(lvl.layers[0].refs), attrs = new Uint8Array(lvl.layers[0].attrs); refs[5] = 1; attrs[5] = (3 << 2) | 1; e.metas[0] = { refs, attrs };
const res = C.applyEdits(rom, [e]);
console.log("placed:", res.log.map((l) => `${l.label} ${l.size} B ${l.inPlace ? "reclaimed slot" : "tail"}`).join(", "), "| tail used", res.tailUsed);
const l2 = C.loadLevel(res.rom, rec);
const ok = eq(l2.layers[0].cells, e.layers[0]) && (!e.collision || eq(l2.collision.cells, e.collision)) && eq(l2.layers[0].refs, refs) && eq(l2.layers[0].attrs, attrs);
console.log("patched ROM decodes to the edits:", ok);
const ups = C.upsMake(rom, res.rom); console.log("UPS", ups.length, "bytes, re-applies:", eq(C.upsApply(rom, ups), res.rom));
process.exit(fails || !ok ? 1 : 0);
