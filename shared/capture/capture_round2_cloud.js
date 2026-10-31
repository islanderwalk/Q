// QSearch 第二輪補證據（雲端容器，Playwright 無頭 Chromium，未登入）。
//
//   node capture_round2_cloud.js cloud    Q4(b)/Q2/Q5：同一個雲端出口 IP 依序開 Threads、Facebook、Instagram，記錄狀態碼與最終網址
//                                          ＋ Q5(d)：同一則 Threads 貼文與 10/30 01:56 的互動數對照（指標會一直變）
//   node capture_round2_cloud.js ptt      Q6：PTT 省錢板（Lifeismoney，不需 18 歲確認）多種短網址／電話雜訊
//
// 規則（沿用 shared/capture/README.md）：只開未登入公開頁；同站兩次導覽間隔 ≥2 秒；每站載入 ≤30 次；
// 不存 cookie、token、cursor；FB／IG／Threads 不存整頁 HTML；不點登入、不關登入牆、不用代理輪替、不偽裝指紋。
// 作者 ID、留言者 ID、發信 IP、編輯者 ID、電話以黑框遮住。
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const OUT = process.env.OUT || path.resolve(__dirname, 'out');  // 目錄結構與 repo 相同（q2/…、q4/…）
const MODE = process.argv[2] || 'cloud';
const LAST = {}, LOADS = {};
const MANIFEST = [], LOG = [];

const nowTW = () => {
  const d = new Date(Date.now() + 8 * 3600e3);
  return d.toISOString().replace(/\.\d+Z$/, '+08:00');
};
const log = (...a) => { const s = a.join(' '); console.log(s); LOG.push(s); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function polite(site, gap = 2000) {
  const d = Date.now() - (LAST[site] || 0);
  if (d < gap) await sleep(gap - d);
  LAST[site] = Date.now();
}

// 主框架的導覽鏈：每一跳的狀態碼與網址（只記網址，不記標頭與內容）
async function goto(page, site, url, waitMs) {
  await polite(site);
  LOADS[site] = (LOADS[site] || 0) + 1;
  const hops = [];
  const onResp = r => {
    if (r.request().isNavigationRequest() && r.frame() === page.mainFrame()) hops.push({ status: r.status(), url: r.url().split('#')[0] });
  };
  page.on('response', onResp);
  let resp = null, err = null;
  try { resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 }); } catch (e) { err = e.message.split('\n')[0]; }
  await page.waitForTimeout(waitMs);
  page.off('response', onResp);
  return { resp, hops, final: page.url(), err };
}

function add(e) {
  MANIFEST.push({ kind: 'screenshot', status: 'real', captured_at: nowTW(), ...e });
  log('  +', e.id, '|', e.result || '');
}

async function shot(page, rel, opts = {}) {
  const file = path.join(OUT, rel);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  await page.screenshot({ path: file, fullPage: false, ...opts });
  return rel;
}

// 與 shared/capture/capture.py 相同的標註樣式（彩色框＋編號標籤＋右上說明卡＋左下時間戳）
const OVERLAY = (cfg) => {
  const old = document.getElementById('__ev'); if (old) old.remove();
  document.querySelectorAll('.__ev_fixed').forEach(e => e.remove());
  const root = document.createElement('div'); root.id = '__ev';
  root.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none';
  document.body.appendChild(root);
  const FONT = '"Microsoft JhengHei","PingFang TC","Noto Sans TC","WenQuanYi Zen Hei",system-ui,sans-serif';
  const COLORS = ['#f43f5e', '#f59e0b', '#22c55e', '#3b82f6', '#a855f7', '#06b6d4'];
  for (const sel of (cfg.masks || [])) {  // 黑框遮一般使用者的帳號與頭像
    document.querySelectorAll(sel).forEach(el => { for (const r of el.getClientRects()) {
      if (r.width < 1 || r.height < 1) continue;
      const m = document.createElement('div');
      m.style.cssText = `position:absolute;left:${r.left + scrollX}px;top:${r.top + scrollY}px;width:${r.width}px;height:${r.height}px;background:#111`;
      root.appendChild(m); } });
  }
  let n = 0; const found = [];
  (cfg.items || []).forEach((it, k) => {
    const color = it.color || COLORS[k % COLORS.length];
    let els = [...document.querySelectorAll(it.sel)];
    if (it.text) els = els.filter(e => (e.textContent || '').includes(it.text));
    if (it.closest) els = els.map(e => e.closest(it.closest) || e);
    els = [...new Set(els)].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    els = els.slice(0, it.max || (it.all ? 60 : 1));
    found.push(els.length);
    els.forEach((el, i) => {
      const r = el.getBoundingClientRect();
      const b = document.createElement('div');
      b.style.cssText = `position:absolute;left:${r.left + scrollX - 3}px;top:${r.top + scrollY - 3}px;width:${r.width + 6}px;height:${r.height + 6}px;border:2.5px solid ${color};border-radius:6px;box-sizing:border-box;box-shadow:0 0 0 1px rgba(0,0,0,.35)`;
      root.appendChild(b);
      if (i === 0 && it.label) {
        n += 1;
        const t = document.createElement('div');
        t.textContent = n + '｜' + it.label;
        const top = r.top + scrollY - 30;
        const y = (it.below || top < 2) ? r.bottom + scrollY + 6 : top;
        t.style.cssText = `position:absolute;left:${Math.max(4, r.left + scrollX - 3 + (it.dx || 0))}px;top:${y}px;background:${color};color:#fff;font:600 14px/1.7 ${FONT};padding:0 9px;border-radius:6px;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,.35)`;
        root.appendChild(t);
      }
    });
  });
  if (cfg.card) {
    const c = document.createElement('div'); c.className = '__ev_fixed';
    c.style.cssText = `position:fixed;${cfg.card.left ? 'left:16px' : 'right:16px'};top:16px;max-width:${cfg.card.width || 440}px;background:rgba(17,24,39,.95);color:#f9fafb;font:14px/1.65 ${FONT};padding:12px 16px;border-radius:10px;border:1px solid rgba(255,255,255,.18);z-index:2147483647;box-shadow:0 6px 24px rgba(0,0,0,.4)`;
    const h = document.createElement('div'); h.textContent = cfg.card.title; h.style.cssText = 'font-weight:700;font-size:15px;margin-bottom:4px';
    c.appendChild(h);
    (cfg.card.lines || []).forEach(s => { const p = document.createElement('div'); p.textContent = s; p.style.whiteSpace = 'pre-wrap'; p.style.wordBreak = 'break-all'; c.appendChild(p); });
    document.body.appendChild(c);
  }
  if (cfg.note) {
    const nb = document.createElement('div'); nb.className = '__ev_fixed';
    nb.style.cssText = `position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);max-width:640px;border:3px solid #ef4444;background:rgba(239,68,68,.08);color:#111827;font:600 18px/1.7 ${FONT};padding:18px 24px;border-radius:12px;text-align:center;z-index:2147483647`;
    nb.textContent = cfg.note; document.body.appendChild(nb);
  }
  const tag = document.createElement('div'); tag.className = '__ev_fixed';
  tag.textContent = (cfg.tag || '標註為截圖時加上 · Playwright 實抓（未登入）') + ' · ' + cfg.stamp;
  tag.style.cssText = `position:fixed;left:12px;bottom:12px;background:rgba(17,24,39,.9);color:#e5e7eb;font:12px/1.6 ${FONT};padding:3px 10px;border-radius:6px;z-index:2147483647`;
  document.body.appendChild(tag);
  return found;
};
const overlay = (page, items, card, extra = {}) => page.evaluate(OVERLAY, { items, card, ...extra, stamp: nowTW() }).catch(e => { log('  overlay error', e.message); return []; });

async function launch() {
  // 雲端容器的對外連線一律經過環境預設的出口代理（不是輪替代理，也不是住宅 IP）
  return chromium.launch(process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY } } : {});
}

// ------------------------------------------------------------------ Threads 互動數（畫面＋內嵌 JSON）
const THREADS_ACTIONS = () => {
  const names = { Like: 'like', '讚': 'like', Reply: 'reply', '回覆': 'reply', '留言': 'reply', Repost: 'repost', '轉發': 'repost', Share: 'share', '分享': 'share' };
  const out = {};
  for (const s of document.querySelectorAll('svg')) {
    const t = s.querySelector('title'); const l = (t && t.textContent) || s.getAttribute('aria-label');
    const k = names[l]; if (!k || k in out) continue;
    const btn = s.closest('[role=button]') || (s.parentElement && s.parentElement.parentElement);
    if (btn) btn.setAttribute('data-ev-act', k);
    out[k] = btn ? btn.innerText.trim() : '';
  }
  // 瀏覽數：最小的、文字以 views 結尾的元素
  const cands = [...document.querySelectorAll('span, div, a')].filter(el => /^[\d.,]+\s?[KM萬]?\s?(views|次瀏覽)$/i.test((el.innerText || '').trim()));
  const best = cands.find(el => !cands.some(o => o !== el && el.contains(o))) || null;
  if (best) best.setAttribute('data-ev-views', '1');
  out.views = best ? best.innerText.trim() : '';
  return out;
};

function walkThreads(node, out) {
  if (Array.isArray(node)) return node.forEach(v => walkThreads(v, out));
  if (!node || typeof node !== 'object') return;
  const t = node.text_post_app_info;
  if (t && typeof t === 'object' && 'repost_count' in t && node.code) {
    out.push({ code: node.code, taken_at: node.taken_at, like_count: node.like_count, direct_reply_count: t.direct_reply_count,
               repost_count: t.repost_count, quote_count: t.quote_count, reshare_count: t.reshare_count });
  }
  for (const v of Object.values(node)) walkThreads(v, out);
}

async function threadsJson(page) {
  const blocks = await page.$$eval('script[type="application/json"]', els => els.map(e => e.textContent));
  const out = [];
  for (const b of blocks) { try { walkThreads(JSON.parse(b), out); } catch (_) {} }
  return out;
}

// ------------------------------------------------------------------ mode: cloud
const THREADS_POST = 'https://www.threads.com/@nasa/post/DfFP4pEET5Y';
const EARLIER = { at: '2026-10-30T01:56:24+08:00', like: '148', reply: '—', repost: '16', share: '1', views: '5.5K views',
                  image: 'q5/evidence/playwright/threads_post_repost.png' };
// 第 2 次（10/30 15:31，雲端容器，同一支腳本）：畫面 讚 266｜轉發 22｜分享 1｜9.1K views；JSON repost 18＋quote 4、reshare 1、reply 14
const SECOND = { at: '2026-10-30T15:31:17+08:00', like: '266', repost: '22', share: '1', views: '9.1K views', repost_count: 18, quote_count: 4 };
const THREADS_MASKS = ['a[href^="/@"]:not([href^="/@nasa"])', 'img[alt*="profile picture" i]:not([alt^="nasa" i])'];

async function runCloud(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-US', timezoneId: 'Asia/Taipei' });
  const page = await ctx.newPage();
  const wall = {};
  const ONLY = (process.env.ONLY || 'threads,fb,ig').split(',');
  let acts = null, js = null, r, rel;

  if (ONLY.includes('threads')) {
  // 1) Threads 貼文頁：同一個雲端 IP 拿得到內容；順便拍 Q5 的互動數變化
  r = await goto(page, 'threads', THREADS_POST, 7000);
  acts = await page.evaluate(THREADS_ACTIONS);
  js = (await threadsJson(page)).find(p => p.code === 'DfFP4pEET5Y') || null;
  wall.threads = { hops: r.hops, final: r.final, err: r.err, served: !!(acts.like || js) };
  log('  threads', JSON.stringify(wall.threads), JSON.stringify(acts), JSON.stringify(js));
  const ageH = js && js.taken_at ? ((Date.now() / 1000 - js.taken_at) / 3600).toFixed(1) : null;
  const jsLine = js ? `JSON：like_count ${js.like_count}｜direct_reply_count ${js.direct_reply_count}｜repost_count ${js.repost_count}＋quote_count ${js.quote_count}｜reshare_count ${js.reshare_count}` : 'JSON：這次沒讀到 text_post_app_info';
  await overlay(page, [
    { sel: "[data-ev-act='repost']", label: `轉發 ${EARLIER.repost} → ${SECOND.repost} → ${acts.repost || '—'}` },
    { sel: "[data-ev-act='like']", label: `讚 ${EARLIER.like} → ${SECOND.like} → ${acts.like || '—'}`, below: true },
    { sel: "[data-ev-act='share']", label: `分享 ${EARLIER.share} → ${SECOND.share} → ${acts.share || '—'}`, below: true, dx: 40 },
    { sel: '[data-ev-views]', label: `瀏覽 ${EARLIER.views} → ${SECOND.views} → ${acts.views || '—'}` },
  ], { title: '同一則貼文，三次未登入抓取的互動數', width: 500, lines: [
    `貼文：${THREADS_POST}`,
    js && js.taken_at ? `發文時間 taken_at：${new Date(js.taken_at * 1000 + 8 * 3600e3).toISOString().slice(0, 16).replace('T', ' ')}（台北），本次距發文約 ${ageH} 小時` : '發文時間：沒讀到 taken_at',
    `第 1 次 ${EARLIER.at.slice(5, 16).replace('T', ' ')}：讚 ${EARLIER.like}｜轉發 ${EARLIER.repost}｜分享 ${EARLIER.share}｜${EARLIER.views}`,
    `第 2 次 ${SECOND.at.slice(5, 16).replace('T', ' ')}：讚 ${SECOND.like}｜轉發 ${SECOND.repost}（JSON ${SECOND.repost_count}＋${SECOND.quote_count}）｜分享 ${SECOND.share}｜${SECOND.views}`,
    `第 3 次 ${nowTW().slice(5, 16).replace('T', ' ')}：讚 ${acts.like || '—'}｜轉發 ${acts.repost || '—'}｜分享 ${acts.share || '—'}｜${acts.views || '—'}`,
    jsLine,
    js ? `畫面轉發 ${acts.repost || '—'} ${Number(acts.repost) === js.repost_count + js.quote_count ? '＝' : '≠'} repost_count ${js.repost_count} ＋ quote_count ${js.quote_count}` : '',
    '一般使用者的帳號與頭像已黑框遮住',
    '結論：發文後數字還在變，SLA 的「1 小時內可查」只是第一筆；之後要定期重抓並保留抓取時間',
  ].filter(Boolean) }, { masks: THREADS_MASKS });
  rel = await shot(page, 'q5/evidence/playwright/threads_post_repost_drift.png');
  add({ q: '5', id: 'q5-threads-repost-drift', node: 'platforms', title: '同一則 Threads 貼文，互動數隨時間變化',
        claim: 'repost_count 等互動數在發文後持續變動；Q5 的 1 小時 SLA 只保證第一筆，之後要排程重抓、每筆值附抓取時間，回補也要記「抓到當下」的時間',
        result: `第 1 次 ${EARLIER.at}：讚 ${EARLIER.like}、轉發 ${EARLIER.repost}、分享 ${EARLIER.share}、${EARLIER.views}；第 2 次 ${SECOND.at}：讚 ${SECOND.like}、轉發 ${SECOND.repost}（${SECOND.repost_count}＋${SECOND.quote_count}）、${SECOND.views}；第 3 次：讚 ${acts.like || '—'}、轉發 ${acts.repost || '—'}、分享 ${acts.share || '—'}、${acts.views || '—'}` + (js ? `；JSON repost_count ${js.repost_count}、quote_count ${js.quote_count}、reshare_count ${js.reshare_count}` : ''),
        image: rel, compare_with: EARLIER.image, json: js,
        source: `Playwright 實抓（未登入、雲端容器）${THREADS_POST}；標註為截圖時加上` });

  }

  if (ONLY.includes('fb')) {
  // 2) Facebook 粉專頁：同一個雲端 IP
  r = await goto(page, 'fb', 'https://www.facebook.com/NASA', 5000);
  wall.fb = { hops: r.hops, final: r.final, err: r.err, login: await page.evaluate(() => !!document.querySelector('input[name=email], input[name=pass]')) };
  log('  fb', JSON.stringify(wall.fb));
  await overlay(page, [{ sel: 'form', label: '登入表單（不是粉專內容）' }], { title: 'Facebook 粉專頁：雲端容器出口 IP、未登入', width: 470, left: true, lines: [
    '要求：https://www.facebook.com/NASA',
    ...r.hops.map(h => `HTTP ${h.status}  ${h.url.length > 90 ? h.url.slice(0, 90) + '…' : h.url}`),
    `最後停在：${r.final.length > 90 ? r.final.slice(0, 90) + '…' : r.final}`,
    '對照：10/30 01:52 在本機（住宅 IP）開同一頁，粉專內容照常顯示，只有登入視窗疊在上面（q2/evidence/playwright/fb_page_nasa.png）',
    `頻率：這是本容器今天對 facebook.com 的第 ${process.env.FB_NTH || '?'} 次請求，談不上高頻`,
  ] });
  rel = await shot(page, 'q2/evidence/playwright/fb_cloud_ip_login_redirect.png');
  wall.fb.image = rel;
  add({ q: '2', id: 'q2-fb-cloud-ip-login', node: 'crawl', title: '雲端 IP 開 FB 粉專頁直接被導到登入頁',
        claim: '從雲端機房 IP 未登入開粉專頁，拿不到任何貼文欄位；Q2 的 100 萬則貼文不能靠雲端爬網頁，要走 Graph API 或有授權的資料來源',
        result: `${r.hops.map(h => h.status).join(' → ')}；最後網址 ${r.final.split('?')[0]}；登入表單 ${wall.fb.login ? '有' : '無'}`,
        image: rel, compare_with: 'q2/evidence/playwright/fb_page_nasa.png',
        source: 'Playwright 實抓（未登入、雲端容器）https://www.facebook.com/NASA；標註為截圖時加上' });

  }

  if (ONLY.includes('ig')) {
  // 3) Instagram Reels 分頁：同一個雲端 IP
  r = await goto(page, 'ig', 'https://www.instagram.com/nasa/reels/', 6000);
  wall.ig = { hops: r.hops, final: r.final, err: r.err, login: await page.evaluate(() => !!document.querySelector('input[name=username], input[name=password], input[name=email], input[name=pass]')) };
  log('  ig', JSON.stringify(wall.ig));
  await overlay(page, [{ sel: 'form', label: '登入表單（不是 Reels 縮圖）' }], { title: 'Instagram Reels 分頁：雲端容器出口 IP、未登入', width: 470, lines: [
    '要求：https://www.instagram.com/nasa/reels/',
    ...r.hops.map(h => `HTTP ${h.status}  ${h.url.length > 90 ? h.url.slice(0, 90) + '…' : h.url}`),
    `最後停在：${r.final.length > 90 ? r.final.slice(0, 90) + '…' : r.final}`,
    '對照：10/30 在本機（住宅 IP）開同一頁，縮圖與觀看數照常顯示（q5/evidence/playwright/ig_reels_grid.png）',
    `頻率：這是本容器今天對 instagram.com 的第 ${process.env.IG_NTH || '?'} 次請求，談不上高頻`,
  ] }, { note: wall.ig.hops.some(h => h.status === 429)
      ? '頁面是空白的：被導去的登入頁本身回 HTTP 429（Too Many Requests）。這個雲端出口 IP 一開始就被限流，連登入頁都不給，更看不到 Reels 縮圖與觀看數。'
      : '被導去登入頁：看不到 Reels 縮圖與觀看數。' });
  rel = await shot(page, 'q5/evidence/playwright/ig_reels_cloud_ip_login_redirect.png');
  wall.ig.image = rel;
  add({ q: '5', id: 'q5-ig-cloud-ip-login', node: 'crawl', title: '雲端 IP 開 IG Reels 分頁直接被導到登入頁',
        claim: 'IG Reels 的 play_count 只有住宅 IP 未登入看得到；雲端機房 IP 會被導去登入頁，所以 Reels 爬蟲要走住宅代理（同 Q4 的代理池），不能直接跑在 Cloud Run 出口',
        result: `${r.hops.map(h => h.status).join(' → ')}；最後網址 ${r.final.split('?')[0]}；登入表單 ${wall.ig.login ? '有' : '無'}`,
        image: rel, compare_with: 'q5/evidence/playwright/ig_reels_grid.png',
        source: 'Playwright 實抓（未登入、雲端容器）https://www.instagram.com/nasa/reels/；標註為截圖時加上' });

  }

  await ctx.close();
  fs.writeFileSync(path.join(OUT, `cloud_wall_${ONLY.join('_')}.json`), JSON.stringify({ captured_at: nowTW(), wall, threads_actions: acts, threads_json: js }, null, 1));
}

// ------------------------------------------------------------------ mode: ptt（Q6）
const SHORT_RE = /(?:https?:\/\/)?(?:bit\.ly|reurl\.cc|ppt\.cc|lihi\d?\.(?:com|cc|me)|pse\.is|tinyurl\.com|goo\.gl|t\.co|shope\.ee|s\.shopee\.tw|momo\.dm|forms\.gle|maps\.app\.goo\.gl|youtu\.be|amzn\.to|t\.ly|is\.gd|cutt\.ly|rb\.gy|0rz\.tw)\/[A-Za-z0-9_\-\/]+/g;
const PHONE_RE = /(?<!\d)(?:0800[-\s]?\d{3}[-\s]?\d{3}|09\d{2}[-\s]?\d{3}[-\s]?\d{3}|0\d{1,2}[-\s]\d{3,4}[-\s]?\d{4}|\(0\d{1,2}\)\s?\d{3,4}[-\s]?\d{4})(?!\d)/g;

const PTT_ROWS = () => [...document.querySelectorAll('.r-ent')].map(e => {
  const a = e.querySelector('.title a');
  return { title: (e.querySelector('.title') || {}).innerText ? e.querySelector('.title').innerText.trim() : '', href: a ? a.getAttribute('href') : null };
}).filter(r => r.href);
const PTT_PREV = () => { const a = [...document.querySelectorAll('.btn-group-paging a')].find(x => x.textContent.includes('上頁')); return a ? a.getAttribute('href') : null; };
const PTT_BODY = () => { const m = document.querySelector('#main-content'); return m ? m.innerText : ''; };

// 與 capture_gaps.py 的 PTT_MARK_JS 相同：紅框標雜訊、黑框遮個資
const PTT_MARK = (cfg) => {
  document.querySelectorAll('.__gap_box').forEach(e => e.remove());
  const root = document.createElement('div'); root.className = '__gap_box';
  root.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none';
  document.body.appendChild(root);
  const boxAt = (rect, style) => { const b = document.createElement('div'); b.className = '__gap_box';
    b.style.cssText = `position:absolute;left:${rect.left + scrollX - 2}px;top:${rect.top + scrollY - 2}px;width:${rect.width + 4}px;height:${rect.height + 4}px;box-sizing:border-box;${style}`;
    root.appendChild(b); };
  const main = document.querySelector('#main-content');
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
  let labelled = 0, firstTop = null;
  for (const n of nodes) {
    // 推文內容（.push-content）也要能標；帳號、IP、時間欄位不標（另外整格蓋黑）
    if (n.parentElement && n.parentElement.closest('.article-metaline, .article-metaline-right, .article-meta-value, .push-userid, .push-ipdatetime, .push-tag')) continue;
    for (const hit of cfg.hits) {
      let idx = n.textContent.indexOf(hit.text);
      while (idx >= 0) {
        const r = document.createRange(); r.setStart(n, idx); r.setEnd(n, idx + hit.text.length);
        for (const rc of r.getClientRects()) {
          if (hit.mask) boxAt(rc, 'background:#111;border:none');
          if (!hit.blackOnly) boxAt(rc, 'border:3px solid #ef4444;border-radius:4px;background:rgba(239,68,68,.08)');
          if (!hit.blackOnly && firstTop === null && (!cfg.focus || hit.text.includes(cfg.focus))) firstTop = rc.top + scrollY;
        }
        if (!hit.blackOnly && r.getClientRects().length) labelled += 1;
        idx = n.textContent.indexOf(hit.text, idx + hit.text.length);
      }
    }
  }
  const authorEl = document.querySelector('.article-meta-value');
  if (authorEl) for (const rc of authorEl.getClientRects()) boxAt(rc, 'background:#111;border:none');
  document.querySelectorAll('.push-userid, .push-ipdatetime').forEach(el => { for (const rc of el.getClientRects()) boxAt(rc, 'background:#111;border:none'); });
  if (firstTop !== null) window.scrollTo(0, Math.max(0, firstTop - 240));
  const card = document.createElement('div'); card.className = '__gap_box';
  card.style.cssText = 'position:fixed;right:14px;top:14px;max-width:460px;background:rgba(17,24,39,.95);color:#f9fafb;font:14px/1.6 "Microsoft JhengHei","WenQuanYi Zen Hei",system-ui,sans-serif;padding:10px 14px;border-radius:10px;z-index:2147483647';
  card.innerHTML = `<div style="font-weight:700">${cfg.title}</div>` + cfg.lines.map(l => `<div>${l}</div>`).join('');
  document.body.appendChild(card);
  return labelled;
};

function classify(body) {
  const hits = [];
  for (const m of body.matchAll(SHORT_RE)) hits.push({ text: m[0], cat: '短網址', domain: m[0].replace(/^https?:\/\//, '').split('/')[0] });
  for (const m of body.matchAll(PHONE_RE)) hits.push({ text: m[0], cat: '電話', mask: true });
  return hits;
}

async function runPtt(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-TW', timezoneId: 'Asia/Taipei' });
  const page = await ctx.newPage();
  const BOARD = 'https://www.ptt.cc/bbs/Lifeismoney/index.html';
  const MAX_ARTICLES = Number(process.env.PTT_MAX_ARTICLES || 12);
  let url = BOARD, rows = [];
  const RETAKE = process.env.PTT_RETAKE ? JSON.parse(process.env.PTT_RETAKE) : null;  // [{href,title,n,focus}]
  if (RETAKE) url = null;
  for (let i = 0; i < 2 && url; i++) {
    await goto(page, 'ptt', url.startsWith('http') ? url : 'https://www.ptt.cc' + url, 1200);
    rows = rows.concat((await page.evaluate(PTT_ROWS)).reverse());
    url = await page.evaluate(PTT_PREV);
  }
  // 省錢板的情報文最常帶短網址；公告、置底不看
  const cand = RETAKE || rows.filter(r => /\[(情報|分享|集點|好康)\]/.test(r.title) && !/公告|置底/.test(r.title));
  log(`  ptt rows=${rows.length} candidates=${cand.length}`);
  const found = [], seenDomains = new Set();
  let opened = 0;
  for (const r of cand) {
    if (opened >= MAX_ARTICLES || found.length >= 3) break;
    opened += 1;
    const aurl = 'https://www.ptt.cc' + r.href;
    await goto(page, 'ptt', aurl, 1000);
    const body = await page.evaluate(PTT_BODY);
    const hits = classify(body);
    const domains = [...new Set(hits.filter(h => h.cat === '短網址').map(h => h.domain))];
    const fresh = RETAKE ? domains : domains.filter(d => !seenDomains.has(d) && d !== 'reurl.cc');  // reurl.cc 已有（ptt_noise_01）
    log(`  ptt article domains=${domains.join(',') || '-'} phones=${hits.filter(h => h.cat === '電話').length} title=${r.title.slice(0, 26)}`);
    if (!fresh.length) continue;
    fresh.forEach(d => seenDomains.add(d));
    const seen = new Set(), pick = [];
    for (const h of hits) { if (!seen.has(h.text)) { seen.add(h.text); pick.push(h); } }
    const top = pick.slice(0, 10);
    const ips = [...new Set(body.match(/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/g) || [])];
    const editors = [...body.matchAll(/編輯:\s*(\S+)\s*\(/g)].map(m => m[1]);
    const foot = ips.map(t => ({ text: t, mask: true, blackOnly: true })).concat(editors.map(t => ({ text: t, mask: true, blackOnly: true })));
    const cats = [...new Set(top.map(h => h.cat))].join('、');
    const n = r.n || found.length + 5;  // 接在既有 ptt_noise_01..04 之後
    const labelled = await page.evaluate(PTT_MARK, { focus: r.focus || null, hits: top.map(h => ({ text: h.text, mask: !!h.mask })).concat((r.extra || []).map(t => ({ text: t }))).concat(foot), title: 'PTT 省錢板：斷詞雜訊佐證', lines: [
      '看板：Lifeismoney（不需 18 歲確認）', `文章：${r.title.slice(0, 32)}`,
      `短網址網域：${domains.join('、')}`, `雜訊類型：${cats}`, ...(r.lines || []),
      '紅框：會被舊字典切成碎詞的字串（電話先黑框遮住數字）', '黑框：作者 ID、留言者 ID、發信 IP、編輯者 ID（隱私遮罩）'] });
    const rel = await shot(page, `q6/evidence/playwright/ptt_noise_${String(n).padStart(2, '0')}.png`);
    found.push({ rel, url: aurl, title: r.title, domains, phones: top.filter(h => h.cat === '電話').length, labelled });
    add({ q: '6', id: `q6-ptt-noise-${String(n).padStart(2, '0')}`, node: 'tokenizer', title: `PTT 省錢板：短網址 ${domains.join('、')}`,
          claim: '社群／論壇內文的短網址不只 reurl.cc 一種；前處理要用網域白名單＋路徑規則整段移除，不能交給字典斷詞',
          result: `短網址網域 ${domains.join('、')}；紅框 ${labelled} 處；電話 ${top.filter(h => h.cat === '電話').length} 則（已遮）`,
          image: rel, source: `Playwright 實抓（未登入、雲端容器）${aurl}；紅框、黑框為截圖時加上` });
  }
  // 斷詞報告頁要用的原文片段：只留含雜訊的那幾行，電話數字另外遮
  const samples = [];
  for (const f of found) samples.push({ url: f.url, title: f.title, domains: f.domains });
  fs.writeFileSync(path.join(OUT, 'ptt_noise_round2.json'), JSON.stringify({ captured_at: nowTW(), opened, found }, null, 1));
  await ctx.close();
}


// ------------------------------------------------------------------ mode: q3fix（重拍 Q3 留言列截圖，補隱私遮罩）
// 原圖 q3/evidence/playwright/ptt_article_comments.png 露出一般使用者帳號與 IP。同一篇文章重拍：
// 帳號、IP 用黑框遮住，時間與推噓保留；捲到第一則「噓」讓推／噓／→ 三種都在畫面裡。
const Q3_URL = 'https://www.ptt.cc/bbs/Gossiping/M.1793282644.A.B5B.html';
const PTT_MASK_IDS = () => {
  const root = document.createElement('div'); root.className = '__gap_box';
  root.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483646;pointer-events:none';
  document.body.appendChild(root);
  const black = rc => { const b = document.createElement('div');
    b.style.cssText = `position:absolute;left:${rc.left + scrollX - 1}px;top:${rc.top + scrollY - 1}px;width:${rc.width + 2}px;height:${rc.height + 2}px;background:#111`;
    root.appendChild(b); };
  const ipRe = /\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/g;
  let ips = 0, ids = 0;
  document.querySelectorAll('.push-userid').forEach(el => { for (const rc of el.getClientRects()) black(rc); ids++; });
  const a = document.querySelector('.article-meta-value'); if (a) for (const rc of a.getClientRects()) black(rc);
  const walker = document.createTreeWalker(document.querySelector('#main-content'), NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode;
    for (const m of n.textContent.matchAll(ipRe)) {
      const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
      for (const rc of r.getClientRects()) black(rc); ips++;
    }
    const ed = n.textContent.match(/編輯:\s*(\S+)\s*\(/);
    if (ed) { const i = n.textContent.indexOf(ed[1]); const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + ed[1].length); for (const rc of r.getClientRects()) black(rc); }
  }
  return { ids, ips };
};

async function runQ3fix(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-TW', timezoneId: 'Asia/Taipei' });
  // 等同在確認頁按「我同意」（原腳本就是按這顆按鈕）；cookie 只在這次瀏覽器裡，不存檔
  await ctx.addCookies([{ name: 'over18', value: '1', domain: 'www.ptt.cc', path: '/' }]);
  const page = await ctx.newPage();
  await goto(page, 'ptt', Q3_URL, 1500);
  const stats = await page.evaluate(() => {
    const t = {}; let n = 0;
    document.querySelectorAll('#main-content div.push span.push-tag').forEach(s => { const k = s.textContent.trim(); t[k] = (t[k] || 0) + 1; n++; });
    return { n, t };
  });
  // 內文的圖片預覽會在捲動後才載入、把版面往下推：先捲過去等圖片載完，再捲一次，版面穩定後才畫遮罩
  const toBoo = () => page.evaluate(() => {
    const boo = [...document.querySelectorAll('#main-content div.push')].find(p => (p.querySelector('.push-tag') || {}).textContent?.trim() === '噓');
    const el = boo || document.querySelector('#main-content div.push');
    if (el) el.scrollIntoView({ block: 'start' });
    window.scrollBy(0, -120);
  });
  await toBoo(); await page.waitForTimeout(3000);
  await page.waitForLoadState('networkidle').catch(() => {});
  await toBoo(); await page.waitForTimeout(800);
  const masked = await page.evaluate(PTT_MASK_IDS);
  const t = stats.t;
  await overlay(page, [
    { sel: '#main-content div.push', all: true, max: 40, label: '留言列：推噓、帳號（已遮）、內容、IP（已遮）與時間' },
  ], { title: '文章頁的留言列（隱私遮罩版）', width: 460, lines: [
    `文章：${Q3_URL}`,
    `留言 ${stats.n} 則：推 ${t['推'] || 0}／噓 ${t['噓'] || 0}／→ ${t['→'] || 0}（全頁計數）`,
    '畫面捲到第一則「噓」，推／噓／→ 三種都看得到',
    '每列都有：推噓、帳號、內容、IP、時間 → parser 依這 5 欄拆成資料列',
    `黑框：留言者帳號 ${masked.ids} 個、IP ${masked.ips} 處、作者帳號（隱私遮罩）`,
  ] });
  const rel = await shot(page, 'q3/evidence/playwright/ptt_article_comments.png');
  add({ q: '3', id: 'q3-ptt-article-comments', node: 'parser', title: '留言列的欄位（隱私遮罩版，2026-10-30 重拍）',
        claim: '每則留言都有推噓、帳號、內容、IP 與時間，parser 依這些欄位拆成資料列',
        result: `留言 ${stats.n} 則：推 ${t['推'] || 0}、噓 ${t['噓'] || 0}、→ ${t['→'] || 0}；帳號 ${masked.ids} 個與 IP ${masked.ips} 處已遮`,
        image: rel, replaces: '2026-10-30T01:51:38+08:00 版（帳號與 IP 未遮）',
        source: `Playwright 實抓（未登入、雲端容器）${Q3_URL}；黑框、彩框為截圖時加上` });
  await ctx.close();
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launch();
  try {
    if (MODE === 'cloud') await runCloud(browser);
    else if (MODE === 'ptt') await runPtt(browser);
    else if (MODE === 'q3fix') await runQ3fix(browser);
    else throw new Error('unknown mode ' + MODE);
  } finally {
    await browser.close();
    log(`  loads: ${JSON.stringify(LOADS)}`);
    const mf = path.join(OUT, `manifest_${MODE}.json`);
    fs.writeFileSync(mf, JSON.stringify(MANIFEST, null, 1));
    fs.appendFileSync(path.join(OUT, 'capture_log.txt'), `== round2 ${MODE} ${nowTW()} ==\n` + LOG.join('\n') + '\n');
  }
})().catch(e => { console.error('ERR', e.stack || e.message); process.exit(1); });
