// PM 頁即時狀態截圖：node shot_live.js <網址> <輸出.png> [等待毫秒]
// 用在端對端驗證：發版前、發版中、重播後各拍一張（畫面上的數字來自官方 ES／PostgreSQL）。
const { chromium } = require('playwright');
(async () => {
  const [url, out, settle] = [process.argv[2], process.argv[3], Number(process.argv[4] || 4000)];
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-TW', timezoneId: 'Asia/Taipei' });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url, { waitUntil: 'networkidle', timeout: 90000 });
  await page.waitForTimeout(settle);
  await page.screenshot({ path: out, fullPage: true });
  console.log(JSON.stringify({ shot: out, live: await page.textContent('#live-text'), env: (await page.textContent('#env')).trim().slice(0, 200), errors }));
  await browser.close();
})();
