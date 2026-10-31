// Render every diagrams/arch_*.html to PNG with Playwright (Chromium). diagrams/targets.json maps
// each source to its file under q6/. Usage: node render_diagrams.js <diagrams_dir> <q6_dir> [name-filter]
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');


(async () => {
  const [dir, outRoot, filter] = process.argv.slice(2);
  const targets = JSON.parse(fs.readFileSync(path.join(dir, 'targets.json'), 'utf8'));
  const files = fs.readdirSync(dir).filter(f => /^arch_.*\.html$/.test(f) && (!filter || f.includes(filter))).sort();
  const browser = await chromium.launch({ proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  for (const f of files) {
    const out = path.join(outRoot, targets[f]);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('file://' + path.resolve(dir, f), { waitUntil: 'load' });
    await page.waitForFunction(() => window.__done === true, null, { timeout: 60000 });
    await page.locator('.canvas').screenshot({ path: out });
    const box = await page.locator('.canvas').boundingBox();
    console.log(`${f} -> ${path.relative(outRoot, out)} (${Math.round(box.width)}x${Math.round(box.height)} css px)${errors.length ? ' ERRORS: ' + errors.join(' | ') : ''}`);
    await page.close();
  }
  await browser.close();
})();
