#!/usr/bin/env node
/**
 * 用 Playwright 產生第 3 題的圖片素材，依小題放進 q3/answer/<小題>/，架構圖放 q3/architecture/。
 *   charts：把 q3/answer/<小題>/ 裡的圖表 HTML（make_charts.py 產生）和架構圖 HTML 轉成同名 PNG
 *   ptt：   打開 PTT 實際頁面截圖當佐證，並在截圖上加標註
 *
 * 用法（在 repo 根目錄）：
 *   python q3/answer/src/make_charts.py
 *   node q3/answer/src/capture.js           # 全部
 *   node q3/answer/src/capture.js charts    # 只轉圖表與架構圖（不連 PTT）
 *   node q3/answer/src/capture.js ptt       # 只截 PTT（每次換頁間隔 1 秒）
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ANSWER = path.resolve(__dirname, '..');      // q3/answer/
const ARCH = path.resolve(__dirname, '../../architecture');  // q3/architecture/
const Q3 = ANSWER;
const BOARD = 'https://www.ptt.cc/bbs/Gossiping';
const SCALE = 1.5;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = (dir, name) => {
  fs.mkdirSync(path.join(Q3, dir), { recursive: true });
  return path.join(Q3, dir, name);
};

function twTime(ts) {
  const d = new Date((Number(ts) + 8 * 3600) * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}/${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())}（${'日一二三四五六'[d.getUTCDay()]}）` +
         `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

// ── 圖表與架構圖 ─────────────────────────────────────────────────────────
async function charts(browser) {
  const page = await browser.newPage({ viewport: { width: 1700, height: 1200 }, deviceScaleFactor: SCALE });
  const dirs = fs.readdirSync(Q3).filter((d) => /^\d-/.test(d)).sort();
  for (const d of dirs) {
    for (const f of fs.readdirSync(path.join(Q3, d)).filter((f) => /^chart-.*\.html$/.test(f)).sort()) {
      const file = out(d, f.replace('.html', '.png'));
      await page.goto('file://' + path.join(Q3, d, f));
      await page.locator('.card').screenshot({ path: file });
      console.log('圖表', path.relative(Q3, file));
    }
  }
  await page.goto('file://' + path.join(ARCH, 'src/architecture.html'));
  await page.locator('#diagram').screenshot({ path: path.join(ARCH, 'architecture.png') });
  console.log('架構圖 q3/architecture/architecture.png');
  await page.close();
}

// ── PTT 截圖 ─────────────────────────────────────────────────────────────
const OVERLAY_CSS = `
  .q3-banner { background:#fffbe6; color:#0b0b0b; font:17px/1.6 "WenQuanYi Zen Hei",sans-serif;
               padding:14px 18px; border-left:6px solid #eda100; margin:0; }
  .q3-banner b { color:#c62828; }
  .q3-banner img { display:block; max-width:100%; margin:10px 0 4px; border:1px solid #c3c2b7; }
  .q3-badge { display:inline-block; min-width:34px; text-align:center; background:#eda100; color:#0b0b0b;
              font:bold 15px/1.6 sans-serif; border-radius:6px; margin-right:8px; }
  .q3-badge.gray { background:#898781; color:#fff; }
  .q3-badge.red { background:#e34948; color:#fff; }
  .q3-time { color:#eda100; font:14px sans-serif; margin-left:12px; }
  .q3-hl { outline:3px solid #e34948 !important; outline-offset:-3px; }
  .q3-sep { color:#fff; background:#e34948; font:16px/1.6 "WenQuanYi Zen Hei",sans-serif; padding:6px 14px; margin:10px 0; }
  .action-bar-margin { margin-top:0 !important; }
`;

async function goto(page, url) {
  const resp = await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: OVERLAY_CSS });
  // PTT 的頂端列與「返回看板」列是固定位置，會蓋住標註；改成一般排版
  await page.evaluate(() => {
    for (const el of document.querySelectorAll('body *')) {
      const pos = getComputedStyle(el).position;
      if (pos === 'fixed' || pos === 'sticky') el.style.setProperty('position', 'static', 'important');
    }
  });
  await sleep(1000); // 每次換頁間隔 1 秒，和設計中的抓取速度一致
  return resp;
}

// 原頁面上第 from～to-1 則留言（可從某個元素開始）的整頁座標範圍，給 clip 截圖用
async function pushesClip(page, n, startSelector) {
  return page.evaluate(([n, startSelector]) => {
    const ps = [...document.querySelectorAll('#main-content div.push')].slice(0, n);
    const start = startSelector ? document.querySelector(startSelector) : ps[0];
    const top = start.getBoundingClientRect().top + scrollY;
    const bottom = ps[ps.length - 1].getBoundingClientRect().bottom + scrollY;
    return { x: 0, y: Math.max(0, top), width: document.documentElement.clientWidth, height: bottom - top + 4 };
  }, [n, startSelector || null]);
}

async function banner(page, html) {
  await page.evaluate((h) => {
    const d = document.createElement('div');
    d.className = 'q3-banner';
    d.innerHTML = h;
    document.body.prepend(d);
  }, html);
}

async function listEntries(page) {
  return page.$$eval('div.r-list-container > div', (divs) => {
    const rows = [];
    let pinned = false;
    for (const d of divs) {
      if (d.classList.contains('r-list-sep')) { pinned = true; continue; }
      if (!d.classList.contains('r-ent')) continue;
      const a = d.querySelector('div.title a');
      const m = a && a.getAttribute('href').match(/M\.(\d+)\.A\.[0-9A-F]+/);
      rows.push({ pinned, href: a ? a.getAttribute('href') : null, ts: m ? Number(m[1]) : null,
                  title: d.querySelector('div.title').textContent.trim(), nrec: d.querySelector('div.nrec').textContent.trim() });
    }
    return rows;
  });
}

// 在每一列加上格位編號（可選：從文章 ID 換算的發文時間）
async function markSlots(page, withTime) {
  await page.evaluate((withTime) => {
    const twTime = (ts) => {
      const d = new Date((ts + 8 * 3600) * 1000);
      const p = (n) => String(n).padStart(2, '0');
      return `${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
    };
    let i = 0;
    let pinned = false;
    for (const d of document.querySelectorAll('div.r-list-container > div')) {
      if (d.classList.contains('r-list-sep')) { pinned = true; continue; }
      if (!d.classList.contains('r-ent')) continue;
      const title = d.querySelector('div.title');
      const a = title.querySelector('a');
      const badge = document.createElement('span');
      if (pinned) { badge.className = 'q3-badge gray'; badge.textContent = '置底'; }
      else { i += 1; badge.className = 'q3-badge' + (a ? '' : ' red'); badge.textContent = '#' + i; }
      title.prepend(badge);
      if (withTime && !pinned) {
        const t = document.createElement('span');
        t.className = 'q3-time';
        const m = a && a.getAttribute('href').match(/M\.(\d+)\.A/);
        t.textContent = m ? 'ID 時間 ' + twTime(Number(m[1])) : '刪文佔位：沒有連結，但仍佔一格';
        if (!m) t.style.color = '#ff8a80';
        title.append(t);
      }
    }
  }, withTime);
}

function dispNrec(n) {
  if (n >= 100) return 100;
  if (n <= -100) return -100;
  if (n <= -10) return -Math.floor(-n / 10) * 10;
  return Math.max(n, 0);
}
function parseNrec(t) {
  if (!t) return 0;
  if (t === '爆') return 100;
  if (t === 'XX') return -100;
  if (/^X\d$/.test(t)) return -10 * Number(t[1]);
  return /^\d+$/.test(t) ? Number(t) : 0;
}

async function ptt(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 }, deviceScaleFactor: SCALE });
  await ctx.addCookies([{ name: 'over18', value: '1', domain: 'www.ptt.cc', path: '/' }]);
  const page = await ctx.newPage();

  // 1. 最新頁：還沒填滿 + 置底公告
  await goto(page, `${BOARD}/index.html`);
  const prev = await page.$eval('div.btn-group-paging a:nth-child(2)', (a) => a.getAttribute('href'));
  const newest = Number(prev.match(/index(\d+)/)[1]) + 1;
  const top = await listEntries(page);
  const normal = top.filter((e) => !e.pinned).length;
  const pinned = top.filter((e) => e.pinned).length;
  await markSlots(page, false);
  await page.evaluate(({ normal, pinned }) => {
    const sep = document.querySelector('div.r-list-sep');
    const lab = document.createElement('div');
    lab.className = 'q3-sep';
    lab.textContent = `▲ 以上 ${normal} 篇：最新頁還沒填滿 20 篇　｜　▼ 以下 ${pinned} 篇：置底公告，不算在 20 格裡`;
    sep.replaceWith(lab);
  }, { normal, pinned });
  await banner(page, `最新頁 <b>index${newest}.html</b>（即 index.html）：目前 <b>${normal}</b> 篇一般文章，還沒滿 20 篇；` +
                     `分隔線下 <b>${pinned}</b> 篇是置底公告，只出現在最新頁，爬蟲要跳過`);
  await page.screenshot({ path: out('1-scale', 'ptt-newest-page-pinned.png'), fullPage: true });
  console.log('PTT 1-scale/ptt-newest-page-pinned.png');

  // 2. 填滿的列表頁：固定 20 格
  await goto(page, `${BOARD}/index${newest - 1}.html`);
  const full = await listEntries(page);
  await markSlots(page, false);
  await banner(page, `<b>index${newest - 1}.html</b>：已填滿的列表頁，固定 <b>${full.length} 格</b>（#1 最舊、#${full.length} 最新）。` +
                     `7 天實測 334 頁全部都是 20 格，所以「頁數 × 20」就是文章數（含刪文佔位）`);
  await page.screenshot({ path: out('1-scale', 'ptt-list-page-20-slots.png'), fullPage: true });
  console.log('PTT 1-scale/ptt-list-page-20-slots.png');

  // 3. 文章網址裡的發文時間
  const first = full.find((e) => e.href);
  await goto(page, 'https://www.ptt.cc' + first.href);
  await page.evaluate(() => {
    for (const m of document.querySelectorAll('div.article-metaline')) {
      if (m.textContent.includes('時間')) m.classList.add('q3-hl');
    }
  });
  const aid = first.href.split('/').pop();
  await banner(page, `網址：/bbs/Gossiping/<b>${aid}</b><br>` +
                     `M. 後面的 <b>${first.ts}</b> 是 Unix 時間（秒）→ 換算台灣時間 <b>${twTime(first.ts)}</b>，` +
                     `和下方紅框的「時間」欄一致。所以只看列表頁的網址，就能知道每篇的發文時間`);
  await page.screenshot({ path: out('1-scale', 'ptt-article-id-timestamp.png'), clip: { x: 0, y: 0, width: 1000, height: 560 } });
  console.log('PTT 1-scale/ptt-article-id-timestamp.png');

  // 4. 推文數 vs 推−噓，順便截留言欄位
  await goto(page, `${BOARD}/index${newest - 1}.html`);
  const cands = full.filter((e) => e.href && /^\d+$/.test(e.nrec) && Number(e.nrec) >= 5 && Number(e.nrec) <= 60);
  let shot = null;
  for (const c of cands.slice(0, 4)) {
    await goto(page, `${BOARD}/index${newest - 1}.html`);
    const row = page.locator(`div.r-ent:has(a[href="${c.href}"])`);
    await row.evaluate((el) => el.classList.add('q3-hl'));
    const rowImg = (await row.screenshot()).toString('base64');
    await goto(page, 'https://www.ptt.cc' + c.href);
    const cnt = await page.$$eval('div.push span.push-tag', (ts) => {
      const r = { push: 0, boo: 0, arrow: 0 };
      for (const t of ts) { const s = t.textContent.trim(); if (s === '推') r.push++; else if (s === '噓') r.boo++; else if (s === '→') r.arrow++; }
      return r;
    });
    const listed = parseNrec(c.nrec);
    const net = dispNrec(cnt.push - cnt.boo);
    shot = { c, cnt, listed, net, rowImg };
    if (net === listed) break;
  }
  const { c, cnt, listed, net, rowImg } = shot;
  const nshow = Math.min(8, await page.locator('#main-content div.push').count());
  const excerpt = (await page.screenshot({ fullPage: true, clip: await pushesClip(page, nshow) })).toString('base64');
  await banner(page, `<span>① 列表上這一列的推文數：<b>${c.nrec}</b></span><img src="data:image/png;base64,${rowImg}">` +
                     `<span>② 打開文章逐則數留言：推 <b>${cnt.push}</b>、噓 <b>${cnt.boo}</b>、→ ${cnt.arrow}（→ 不影響推文數）</span>` +
                     `<img src="data:image/png;base64,${excerpt}">` +
                     `<span>③ 推 − 噓 = ${cnt.push} − ${cnt.boo} = <b>${cnt.push - cnt.boo}</b>，列表顯示 <b>${listed}</b> ` +
                     `${net === listed ? '✔ 一致' : '✘ 不一致（可能是抓取之間有人留言）'}。實測 3,797 篇穩定文章中 97.2% 完全一致</span>`);
  await page.locator('.q3-banner').screenshot({ path: out('4-invariant', 'ptt-nrec-vs-comments.png') });
  console.log('PTT 4-invariant/ptt-nrec-vs-comments.png');
  await page.evaluate(() => document.querySelector('.q3-banner').remove());

  // 5. 留言欄位：推/噓/→、帳號、內容、時間（含 IP）；直接在原頁面上框出欄位
  await page.evaluate((n) => {
    const ps = [...document.querySelectorAll('#main-content div.push')].slice(0, n);
    for (const p of ps) {
      for (const [sel, col] of [['span.push-tag', '#eda100'], ['span.push-userid', '#3987e5'], ['span.push-ipdatetime', '#e34948']]) {
        const s = p.querySelector(sel);
        if (s) s.style.cssText += `outline:2px solid ${col}; outline-offset:1px;`;
      }
    }
    const b = document.createElement('div');
    b.className = 'q3-banner';
    b.id = 'q3-fields-banner';
    b.innerHTML = `每則留言存的欄位（10/27 普查 23 萬則實測平均）：` +
      `<span style="color:#b07800">■ 推/噓/→ 3.0 B</span>　<span style="color:#1c5cab">■ 帳號 7.8 B</span>　` +
      `內容 33.7 B　<span style="color:#c62828">■ 時間 25.2 B（99.9% 帶發文者 IP）</span>　→ 每則約 <b>69.7 B</b>`;
    ps[0].before(b);
  }, nshow);
  await page.screenshot({ path: out('1-scale', 'ptt-comment-fields.png'), fullPage: true,
                          clip: await pushesClip(page, nshow, '#q3-fields-banner') });
  console.log('PTT 1-scale/ptt-comment-fields.png');

  // 6. 列表頁的 20 格不變量：找一頁含刪文佔位的
  let found = null;
  for (let p = newest - 1; p > newest - 25 && !found; p--) {
    await goto(page, `${BOARD}/index${p}.html`);
    const rows = await listEntries(page);
    if (rows.some((e) => !e.href)) found = { p, rows };
  }
  if (found) {
    const { p, rows } = found;
    const ts = rows.filter((e) => e.ts).map((e) => e.ts);
    const mono = ts.every((t, i) => i === 0 || t >= ts[i - 1]);
    await markSlots(page, true);
    await banner(page, `<b>index${p}.html</b>：${rows.length} 格 = ${rows.filter((e) => e.href).length} 篇文章 + ` +
                       `<b>${rows.filter((e) => !e.href).length} 格刪文佔位</b>（紅色編號）。` +
                       `右側是從文章 ID 換算的發文時間，由上而下${mono ? '<b>單調遞增 ✔</b>' : '<b>有倒序 ✘</b>'}` +
                       `<br>不變量：每個填滿的頁剛好 20 格、時間不倒序、每個非刪文的格子都要在資料庫裡找得到`);
    await page.screenshot({ path: out('4-invariant', 'ptt-list-slots-deleted.png'), fullPage: true });
    console.log('PTT 4-invariant/ptt-list-slots-deleted.png');
  } else {
    console.log('！最近 24 頁沒有刪文佔位，略過 4-invariant/ptt-list-slots-deleted.png');
  }

  // 7. 被截斷的文章（「檔案過大」）與被刪的文章（404）
  const targets = JSON.parse(fs.readFileSync(path.join(__dirname, 'ptt_targets.json'), 'utf-8'));
  const tr = targets.truncated[0];
  if (tr) {
    await goto(page, `${BOARD}/${tr.aid}.html`);
    const warn = page.locator('div.push:not(:has(span.push-tag))').first();
    const shown = await page.locator('div.push span.push-tag').count();
    await warn.evaluate((el, info) => {
      el.classList.add('q3-hl');
      const b = document.createElement('div');
      b.className = 'q3-banner';
      b.innerHTML = `▼ PTT 網頁版在這裡截斷：「${info.title}」網頁上只顯示到第 <b>${info.shown}</b> 則留言，之後的留言網頁版抓不到。` +
                    `7 天 6,224 篇裡只有 ${info.n} 篇這樣（約 0.03%），列為已知且接受的風險`;
      el.before(b);
    }, { title: tr.title, shown, n: targets.truncated.length });
    const box = await page.locator('.q3-banner').boundingBox();
    const wb = await warn.boundingBox();
    const y = Math.max(0, box.y + (await page.evaluate(() => window.scrollY)) - 260);
    const y2 = wb.y + (await page.evaluate(() => window.scrollY)) + wb.height + 40;
    await page.screenshot({ path: out('5-not-monitored', 'ptt-truncated-article.png'), fullPage: true,
                            clip: { x: 0, y, width: 1000, height: y2 - y } });
    console.log('PTT 5-not-monitored/ptt-truncated-article.png');
  }
  const del = targets.deleted[0];
  if (del) {
    const resp = await goto(page, `${BOARD}/${del.aid}.html`);
    await banner(page, `10/26 列表快照時還在：<b>${del.title}</b>（發文 ${twTime(del.ts)}）<br>` +
                       `10/27 普查時打開回 <b>${resp.status()}</b>：文章已被刪除。7 天 ${targets.articles_total.toLocaleString()} 篇裡有 ` +
                       `${targets.deleted_total} 篇在這期間被刪（${(targets.deleted_total / targets.articles_total * 100).toFixed(1)}%）；` +
                       `每天重抓遇到 404 就標記刪除，最多晚 24 小時`);
    await page.screenshot({ path: out('5-not-monitored', 'ptt-deleted-404.png'), clip: { x: 0, y: 0, width: 1000, height: 260 } });
    console.log('PTT 5-not-monitored/ptt-deleted-404.png');
  }
  await ctx.close();
}

(async () => {
  const mode = process.argv[2] || 'all';
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined;
  const browser = await chromium.launch({ proxy });
  try {
    if (mode === 'all' || mode === 'charts') await charts(browser);
    if (mode === 'all' || mode === 'ptt') await ptt(browser);
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
