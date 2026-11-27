const { chromium } = require('playwright');
const OUT = process.argv[2];
const K = 'http://localhost:5601';
const Q = {
  '09-kibana-esql-channel-month': 'FROM posts | STATS posts = COUNT(*) BY channel, month | SORT month DESC, channel | LIMIT 1000',
  '10-kibana-esql-partitions-per-channel': 'FROM posts | STATS n = COUNT(*) BY channel, month | STATS months = COUNT(*), first_month = MIN(month), last_month = MAX(month) BY channel | SORT channel',
  '11-kibana-esql-ptt-2026-09': 'FROM posts | WHERE channel == "ptt" AND month == "2026-09" | STATS posts = COUNT(*)',
};
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  for (const [name, q] of Object.entries(Q)) {
    const a = `(dataSource:(type:esql),query:(esql:'${q}'))`;
    const g = `(time:(from:'2022-01-01T00:00:00.000Z',to:now))`;
    await p.goto(`${K}/app/discover#/?_g=${encodeURIComponent(g)}&_a=${encodeURIComponent(a)}`, { waitUntil: 'networkidle', timeout: 120000 });
    await p.waitForSelector('[data-test-subj="discoverDocTable"], [data-test-subj="docTable"], .euiDataGrid', { timeout: 90000 }).catch(() => {});
    await p.waitForTimeout(6000);
    for (const t of ['Dismiss', 'Got it', 'Close tour', 'Close']) { const x = p.getByRole('button', { name: t }); if (await x.count()) await x.first().click().catch(() => {}); }
    await p.waitForTimeout(1500);
    await p.screenshot({ path: `${OUT}/${name}.png` });
    console.log(name, 'ok');
  }
  await p.goto(`${K}/app/management/data/index_management/indices/index_details?indexName=posts`, { waitUntil: 'networkidle', timeout: 120000 });
  await p.waitForTimeout(6000);
  await p.screenshot({ path: `${OUT}/12-kibana-index-posts.png` });
  console.log('12 ok');
  await b.close();
})();
