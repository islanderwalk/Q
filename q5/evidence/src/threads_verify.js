// 核對 Threads 公開頁面：畫面上顯示的數字 vs 頁面內嵌 JSON 的欄位，並截三篇代表性貼文
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [url, outDir] = process.argv.slice(2);

// 畫面文字 → 數字："1,185" → 1185、"1.2 萬" → 12000、空白 → null
function parseShown(s) {
  if (s === undefined) return undefined;
  const t = (s || '').replace(/,/g, '').trim();
  if (!t) return null;
  const m = t.match(/^(\d+(?:\.\d+)?)\s*(萬|億)?$/);
  if (!m) return NaN;
  return Math.round(parseFloat(m[1]) * ({ '萬': 1e4, '億': 1e8 }[m[2]] || 1));
}

// 在內嵌 JSON 裡找帶有 text_post_app_info.repost_count 的貼文物件
function walk(node, trail, out) {
  if (Array.isArray(node)) return node.forEach((v, i) => walk(v, trail.concat(`[${i}]`), out));
  if (!node || typeof node !== 'object') return;
  const t = node.text_post_app_info;
  if (t && typeof t === 'object' && 'repost_count' in t) {
    out.push({ code: node.code, like_count: node.like_count, direct_reply_count: t.direct_reply_count,
               repost_count: t.repost_count, quote_count: t.quote_count, reshare_count: t.reshare_count,
               path: trail.slice(-3).join('.') + '.text_post_app_info.repost_count' });
  }
  for (const [k, v] of Object.entries(node)) walk(v, trail.concat(k), out);
}

(async () => {
  const browser = await chromium.launch(process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY } } : {});
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 1600 }, deviceScaleFactor: 2, locale: 'zh-TW', timezoneId: 'Asia/Taipei',
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
  });
  const page = await ctx.newPage();
  const netBodies = [];  // 頁面載入後另外拉的貼文資料（GraphQL 回應）
  page.on('response', async resp => {
    try {
      const t = await resp.text();
      if (t.includes('repost_count')) netBodies.push(t.replace(/^for \(;;\);/, ''));
    } catch (_) {}
  });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(8000);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(1000);
  const capturedAt = new Date().toISOString();

  // 1. JSON
  const blocks = await page.$$eval('script[type="application/json"]', els => els.map(e => e.textContent));
  const jsonPosts = [];
  for (const b of blocks.concat(netBodies)) {
    try { walk(JSON.parse(b), [], jsonPosts); continue; } catch (_) {}
    for (const line of b.split('\n')) { try { walk(JSON.parse(line), [], jsonPosts); } catch (_) {} }  // 一行一個 JSON 的串流回應
  }
  const byCode = new Map();
  for (const p of jsonPosts) if (p.code && !byCode.has(p.code)) byCode.set(p.code, p);

  // 2. 畫面
  const shown = await page.$$eval('div[data-pressable-container="true"]', posts => posts.map((post, idx) => {
    const a = post.querySelector('a[href*="/post/"]');
    const code = a ? a.getAttribute('href').split('/post/')[1].split(/[/?]/)[0] : null;
    const counts = {};
    for (const name of ['讚', '留言', '轉發', '分享']) {
      const title = [...post.querySelectorAll('svg title')].find(t => t.textContent === name);
      const btn = title && title.closest('[role="button"]');
      counts[name] = btn ? btn.innerText.trim() : undefined;
    }
    return { idx, code, ...counts };
  }));

  // 3. 配對核對
  const rows = shown.map(s => {
    const j = byCode.get(s.code) || {};
    const r = { code: s.code, 畫面: s, json: j };
    r.likeOk = Math.abs(parseShown(s['讚']) - j.like_count) <= (/(萬|億)/.test(s['讚']) ? 500 : 0);
    r.replyOk = parseShown(s['留言']) === j.direct_reply_count;
    r.repostIsSum = parseShown(s['轉發']) === (j.repost_count || 0) + (j.quote_count || 0);
    r.repostIsField = parseShown(s['轉發']) === j.repost_count;
    r.shareOk = parseShown(s['分享']) === (j.reshare_count === undefined ? undefined : j.reshare_count);
    return r;
  });
  fs.writeFileSync(path.join(outDir, 'verify.json'),
    JSON.stringify({ url, capturedAt, jsonPath: jsonPosts[0] && jsonPosts[0].path, rows }, null, 1));

  console.log(`擷取時間 ${capturedAt}｜畫面貼文 ${shown.length} 篇｜JSON 找到 ${byCode.size} 篇`);
  console.log(`JSON 路徑範例：${jsonPosts[0] && jsonPosts[0].path}`);
  console.log('代碼          畫面(讚/留言/轉發/分享)        JSON like/reply/repost+quote/reshare      轉發=repost+quote  轉發=repost  分享=reshare');
  for (const r of rows) {
    const s = r.畫面, j = r.json;
    console.log(`${(r.code || '').padEnd(12)}  ${[s['讚'], s['留言'], s['轉發'], s['分享'] || '(無)'].join(' / ').padEnd(26)}  `
      + `${j.like_count} / ${j.direct_reply_count} / ${j.repost_count}+${j.quote_count} / ${j.reshare_count}`.padEnd(38)
      + `  ${r.repostIsSum ? '✓' : '✗'}               ${r.repostIsField ? '✓' : '✗'}            ${r.shareOk ? '✓' : '✗'}`);
  }
  const n = rows.length;
  const cnt = k => rows.filter(r => r[k]).length;
  console.log(`彙總：讚 ${cnt('likeOk')}/${n}、留言 ${cnt('replyOk')}/${n}、畫面轉發＝repost+quote ${cnt('repostIsSum')}/${n}、`
    + `畫面轉發＝repost_count ${cnt('repostIsField')}/${n}、分享＝reshare_count ${cnt('shareOk')}/${n}`);

  // 4. 截三篇：轉發含引用、分享為 null、萬 的簡寫
  const pick = [
    rows.find(r => r.json.quote_count > 0 && r.json.reshare_count),
    rows.find(r => r.json.reshare_count === null),
    rows.find(r => /萬/.test(r.畫面['讚'] || '')),
  ];
  // 只框當下這篇（紅＝轉發、藍＝分享），截完就拿掉，避免框線跑到下一張
  const setMarks = (post, on) => {
    for (const [name, color] of [['轉發', '#E53935'], ['分享', '#1E88E5']]) {
      const title = [...post.querySelectorAll('svg title')].find(t => t.textContent === name);
      const btn = title && title.closest('[role="button"]');
      if (!btn) continue;
      btn.style.outline = on ? `3px solid ${color}` : '';
      btn.style.outlineOffset = on ? '2px' : '';
      btn.style.borderRadius = on ? '8px' : '';
    }
  };
  const PAD = 10;
  const containers = page.locator('div[data-pressable-container="true"]');
  for (const [i, r] of pick.entries()) {
    if (!r) continue;
    const el = containers.nth(r.畫面.idx);
    await el.scrollIntoViewIfNeeded();
    await el.evaluate(setMarks, true);
    const b = await el.boundingBox();
    const file = path.join(outDir, `post_${i + 1}_${r.code}.png`);
    await page.screenshot({ path: file, clip: { x: b.x - PAD, y: b.y - PAD, width: b.width + 2 * PAD, height: b.height + 2 * PAD } });
    await el.evaluate(setMarks, false);
    console.log(`截圖 ${file}`);
  }

  // 來源：頁首（官方帳號、未登入時右側的登入框）
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(outDir, 'source_header.png'), clip: { x: 130, y: 0, width: 1050, height: 470 } });
  console.log('截圖 source_header.png');
  await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
