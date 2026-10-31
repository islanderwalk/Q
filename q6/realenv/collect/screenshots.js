// Q6 真實渠道：每種「還沒有截圖」的來源各拍一張未登入截圖（1440×900，非整頁）。
// PTT、Threads 個人頁在 repo 已有截圖（q6/evidence/playwright、q4/evidence/playwright），
// 這裡的 Threads 是本次用到的台灣帳號 @pts.news。不登入、不存 cookie（每張用新的 context，結束即丟）。
// 用法：node q6/realenv/collect/screenshots.js [名稱…]（不給名稱＝全部；結果併入 screenshots.json）
// 2026-10-31 01:39 第一次：YouTube 影片頁 /watch?v=9qqyUi2Npmc 回 429 並轉到 google.com/sorry（reCAPTCHA），
// 不繞過；該張改名 youtube_watch_429_recaptcha.png 留作失敗紀錄，改拍公開頻道頁。
// Threads 未登入會跳「透過 Threads 暢所欲言」登入邀請框：按 Esc 關掉（不登入）再拍。
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const OUT = path.resolve(__dirname, '../../evidence/realenv/channels');
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';
const SHOTS = [
  { name: 'youtube_pnnpts_channel', url: 'https://www.youtube.com/@PNNPTS/videos', wait: 6000 },
  { name: 'media_cna_article', url: 'https://www.cna.com.tw/news/aloc/202610300316.aspx', wait: 5000 },
  { name: 'threads_pts_news', url: 'https://www.threads.com/@pts.news', wait: 6000 },
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const logPath = path.join(OUT, 'screenshots.json');
  let old = [];
  try { old = JSON.parse(fs.readFileSync(logPath, 'utf8')); } catch (e) { old = []; }
  const want = process.argv.slice(2);
  const log = [];
  for (const s of SHOTS.filter(x => !want.length || want.includes(x.name))) {
    const ctx = await browser.newContext({
      viewport: { width: 1440, height: 900 }, locale: 'zh-TW', timezoneId: 'Asia/Taipei', userAgent: UA,
    });
    const page = await ctx.newPage();
    let status = null;
    try {
      const resp = await page.goto(s.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
      status = resp ? resp.status() : null;
      await page.waitForTimeout(s.wait);
      if (s.name.startsWith('threads')) {
        // 關掉未登入時的登入邀請框（只按 Esc，不登入）
        await page.keyboard.press('Escape');
        await page.waitForTimeout(1200);
      }
      if (s.name.startsWith('youtube')) {
        // 影片不播放、靜音，只看標題與說明區
        await page.evaluate(() => { const v = document.querySelector('video'); if (v) { v.pause(); v.muted = true; } });
        await page.waitForTimeout(500);
      }
      const file = path.join(OUT, `${s.name}.png`);
      await page.screenshot({ path: file, fullPage: false });
      log.push({ name: s.name, url: s.url, status, final_url: page.url(), file: path.relative(path.resolve(__dirname, '../../..'), file),
                 captured_at: new Date(Date.now() + 8 * 3600e3).toISOString().replace('Z', '+08:00').replace(/\.\d{3}/, '') });
      console.log(status, s.url, '->', file);
    } catch (e) {
      log.push({ name: s.name, url: s.url, status, error: String(e).slice(0, 200) });
      console.log('ERROR', s.url, String(e).slice(0, 200));
    }
    await ctx.close();
    await new Promise(r => setTimeout(r, 2500));
  }
  await browser.close();
  const merged = old.concat(log);
  fs.writeFileSync(logPath, JSON.stringify(merged, null, 1));
})();
