// node render.js <url> <outdir> — PNGs of the master page for README
const { chromium } = require('playwright');
const URL = process.argv[2], OUT = process.argv[3];
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light', deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  const shot = async (name, hash, after) => {
    await p.evaluate(h => { location.hash = h; }, hash);
    await p.waitForTimeout(700);
    if (after) { await after(); await p.waitForTimeout(500); }
    await p.screenshot({ path: `${OUT}/${name}` });
    console.log('wrote', name);
  };
  await p.goto(URL + '#shape/int', { waitUntil: 'load' });
  await p.waitForTimeout(1000);
  await shot('qsearch-architecture.png', '#shape/int');
  await shot('qsearch-architecture-q6.png', '#shape/int', () => p.click('.qbar button[data-q="6"]'));
  await p.click('.qbar button[data-q="6"]');
  await shot('qsearch-architecture-node-deploy.png', '#shape/int/crawl', async () => {
    await p.evaluate(() => { const d = document.querySelector('.panel .dep'); if (d) d.scrollIntoView({ block: 'center' }); });
  });
  await shot('qsearch-q5-dataflow.png', '#q5flow', async () => { await p.evaluate(() => document.querySelector('#pane-q5flow .dg').scrollIntoView({ block: 'start' })); await p.click('#pane-q5flow [data-q5="threads"]'); });
  await shot('qsearch-evidence.png', '#evidence');
  await b.close();
})();
