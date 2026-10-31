// Render every evidence card listed in cards/manifest.json to its file under q6/.
// Usage: node render_cards.js <cards_dir> <q6_dir> [id-filter]
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
(async () => {
  const [cardsDir, outRoot, filter] = process.argv.slice(2);
  const manifest = JSON.parse(fs.readFileSync(path.join(cardsDir, 'manifest.json'), 'utf8')).filter(c => !filter || c.id.includes(filter));
  const browser = await chromium.launch({ proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined });
  const ctx = await browser.newContext({ viewport: { width: 1344, height: 1000 }, deviceScaleFactor: 1.5 });
  for (const c of manifest) {
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('requestfailed', r => errors.push('failed: ' + r.url().slice(0, 80)));
    await page.goto('file://' + path.resolve(cardsDir, c.id + '.html'), { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    const out = path.join(outRoot, c.out);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    await page.locator('.ev').screenshot({ path: out });
    const b = await page.locator('.ev').boundingBox();
    console.log(`${c.id} -> ${c.out} (${Math.round(b.width)}x${Math.round(b.height)})${errors.length ? ' ERR ' + errors.join(' | ') : ''}`);
    await page.close();
  }
  await browser.close();
})();
