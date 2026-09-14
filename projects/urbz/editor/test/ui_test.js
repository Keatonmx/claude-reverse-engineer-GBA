// node ui_test.js path/to/urbz.gba   (needs `npm install playwright` and a Chromium; set CHROME to its executable)
// Loads the page headless, checks the district renders, paints and edits through the test hook, exports, undoes, redoes.
const { chromium } = require("playwright"); const fs = require("fs"); const path = require("path");
(async () => {
  const browser = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  let errors = 0; page.on("pageerror", (e) => { errors++; console.log("PAGE ERROR", e.message); });
  await page.goto("file://" + path.resolve(__dirname, "..", "index.html"));
  await page.setInputFiles("#romfile", process.argv[2]);
  await page.waitForFunction(() => window.__editor && window.__editor.level);
  const n = await page.$$eval("#district option", (o) => o.length); console.log("districts:", n);
  await page.selectOption("#district", await page.$eval("#district option:nth-child(63)", (o) => o.value));
  await page.waitForFunction(() => window.__editor.rec === 0x748C8);
  for (let y = 7; y < 12; y++) for (let x = 8; x < 10; x++) await page.evaluate(([i]) => { window.__editorApi.paintCell("0", i, 0); window.__editorApi.paintCell("c", i, 0); }, [y * 25 + x]);
  await page.evaluate(() => window.__editorApi.setMetaSlot(0, 230, 5, 1, (3 << 2) | 1));
  await page.waitForTimeout(500); console.log("space:", await page.textContent("#space"));
  const ups = await page.evaluate(() => Array.from(UrbzCore.upsMake(window.__editor.base, window.__editorApi.build().rom)));
  if (process.argv[3]) fs.writeFileSync(process.argv[3], Buffer.from(ups));
  for (let i = 0; i < 21; i++) await page.click("#undo");
  await page.waitForTimeout(400); const left = await page.evaluate(() => window.__editorApi.edited().length);
  for (let i = 0; i < 21; i++) await page.click("#redo");
  await page.waitForTimeout(400);
  const ups2 = await page.evaluate(() => Array.from(UrbzCore.upsMake(window.__editor.base, window.__editorApi.build().rom)));
  console.log("undo leaves nothing:", left === 0, "| redo reproduces the patch:", Buffer.from(ups2).equals(Buffer.from(ups)), "| page errors:", errors);
  await browser.close(); process.exit(left === 0 && errors === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
