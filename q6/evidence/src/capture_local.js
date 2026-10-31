// Screenshot the research page (same HTML as the published artifact) rendered locally.
// Usage: node capture_local.js <research-page.html> <crops_dir>
const { chromium } = require('playwright');
(async () => {
  const [file, cropsDir] = process.argv.slice(2);
  const browser = await chromium.launch({ proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 1.5, colorScheme: 'light' });
  const page = await ctx.newPage();
  await page.goto('file://' + file + '#overview', { waitUntil: 'load' });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(800);
  const tableBottom = await page.evaluate(() => {
    const t = document.querySelector('#p-overview .tbl');
    return Math.round(t.getBoundingClientRect().bottom + scrollY);
  });
  await page.screenshot({ path: `${cropsDir}/ev_00_research_page_1.png`, clip: { x: 0, y: 0, width: 1280, height: tableBottom + 16 }, fullPage: true });
  await page.goto('file://' + file + '#lab', { waitUntil: 'load' });
  await page.waitForTimeout(800);
  await page.locator('#p-lab .panel').first().screenshot({ path: `${cropsDir}/ev_00_research_page_2.png` });
  const sizes = await page.evaluate(() => ({ lab: document.querySelector('#p-lab .panel').getBoundingClientRect().width }));
  console.log('overview height', tableBottom, 'lab panel width', Math.round(sizes.lab));
  await browser.close();
})();
