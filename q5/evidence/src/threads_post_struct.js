// 讀單篇 Threads 公開貼文的 JSON：is_markup、share_info（轉發／引用了誰）、各種計數欄位
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [outDir, ...urls] = process.argv.slice(2);

function walk(node, out) {
  if (Array.isArray(node)) return node.forEach(v => walk(v, out));
  if (!node || typeof node !== 'object') return;
  if (node.text_post_app_info && typeof node.text_post_app_info === 'object' && node.code) out.push(node);
  for (const v of Object.values(node)) walk(v, out);
}

function summarize(p) {
  const t = p.text_post_app_info || {};
  const si = t.share_info || {};
  const ref = k => (si[k] && typeof si[k] === 'object') ? `${k}=@${(si[k].user || {}).username}/${si[k].code}` : null;
  const counts = {};
  for (const [k, v] of Object.entries(t)) if (/count/.test(k) && (typeof v === 'number' || v === null)) counts[k] = v;
  for (const [k, v] of Object.entries(p)) if (/count/.test(k) && (typeof v === 'number' || v === null)) counts[k] = v;
  return {
    code: p.code, user: (p.user || {}).username, is_markup: t.is_markup,
    share_refs: ['reposted_post', 'quoted_post', 'quoted_attachment_post'].map(ref).filter(Boolean),
    share_info_keys: Object.keys(si).filter(k => si[k] !== null && si[k] !== false),
    counts,
    text: ((p.caption || {}).text || '').slice(0, 60).replace(/\n/g, ' '),
  };
}

(async () => {
  const browser = await chromium.launch(process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY } } : {});
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 1600 }, deviceScaleFactor: 2, locale: 'zh-TW', timezoneId: 'Asia/Taipei',
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
  });
  const results = [];
  for (const url of urls) {
    const page = await ctx.newPage();
    const net = [];
    page.on('response', async r => { try { const t = await r.text(); if (t.includes('text_post_app_info')) net.push(t.replace(/^for \(;;\);/, '')); } catch (_) {} });
    const want = url.split('/post/')[1];
    let status = null;
    try { status = (await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })).status(); } catch (e) { status = 'ERR ' + e.message.slice(0, 60); }
    await page.waitForTimeout(7000);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(800);
    const blocks = await page.$$eval('script[type="application/json"]', els => els.map(e => e.textContent)).catch(() => []);
    const posts = [];
    for (const b of blocks.concat(net)) {
      try { walk(JSON.parse(b), posts); continue; } catch (_) {}
      for (const line of b.split('\n')) { try { walk(JSON.parse(line), posts); } catch (_) {} }
    }
    const seen = new Map();
    for (const p of posts) if (!seen.has(p.code)) seen.set(p.code, summarize(p));
    const main = seen.get(want);
    const shot = path.join(outDir, `post_${want}.png`);
    await page.screenshot({ path: shot, clip: { x: 200, y: 0, width: 880, height: 1100 } }).catch(() => {});
    results.push({ url, status, finalUrl: page.url(), main, others: [...seen.values()].filter(s => s.code !== want).slice(0, 4) });
    await page.close();
  }
  fs.writeFileSync(path.join(outDir, 'post_struct.json'), JSON.stringify(results, null, 1));
  for (const r of results) {
    console.log(`\n== ${r.url}（${r.status}）`);
    if (!r.main) { console.log('   找不到這篇的 JSON'); continue; }
    const m = r.main;
    console.log(`   @${m.user} ${m.code}｜is_markup=${m.is_markup}｜share_info 有值的鍵：${m.share_info_keys.join(', ') || '（無）'}`);
    console.log(`   參照：${m.share_refs.join('；') || '（無）'}`);
    console.log(`   計數：${JSON.stringify(m.counts)}`);
    console.log(`   內文：${m.text}`);
  }
  await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
