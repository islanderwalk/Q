// Capture highlighted excerpts from the official sources listed in evidence_web.json.
// For each excerpt: find the deepest visible element containing the phrase, outline it,
// scroll it to the middle of the viewport, and save a clipped screenshot.
// Usage: node capture_web.js <evidence_web.json> <crops_dir> <meta_out.json> [id-filter]
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const HIDE = `
  [id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i],
  [aria-label*="cookie" i], #onetrust-consent-sdk, .truste_box_overlay, #truste-consent-track,
  [class*="Banner_banner"], .announcement-banner { display: none !important; }
`;

function markInPage({ find, row, up, i }) {
  const norm = s => (s || '').replace(/\s+/g, ' ');
  const skip = new Set(['SCRIPT', 'STYLE', 'TEMPLATE', 'TEXTAREA', 'NOSCRIPT', 'TITLE', 'HEAD', 'META', 'svg', 'SVG']);
  const visible = e => {
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const cands = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  while (walker.nextNode()) {
    const e = walker.currentNode;
    if (skip.has(e.tagName)) continue;
    if (!norm(e.textContent).includes(find)) continue;
    if ([...e.children].some(c => !skip.has(c.tagName) && norm(c.textContent).includes(find))) continue;
    if (!visible(e)) continue;
    cands.push(e);
  }
  if (!cands.length) return null;
  let t = cands[0];
  while (t.parentElement && getComputedStyle(t).display.startsWith('inline')) t = t.parentElement;
  const hl = row ? (t.closest('tr') || t) : t;
  const crop = up ? (t.closest(up.toLowerCase()) || t) : hl;
  const paint = 'rgba(255, 229, 138, 0.6)';
  if (hl.tagName === 'TR') {
    for (const c of hl.children) c.style.setProperty('background-color', paint, 'important');
    hl.style.setProperty('outline', '3px solid #d39b00', 'important');
  } else {
    hl.style.setProperty('outline', '3px solid #d39b00', 'important');
    hl.style.setProperty('outline-offset', '3px', 'important');
    hl.style.setProperty('background-color', paint, 'important');
    hl.style.setProperty('border-radius', '3px', 'important');
  }
  crop.setAttribute('data-ev-crop', String(i));
  crop.scrollIntoView({ block: 'center', inline: 'nearest' });
  return { count: cands.length, tag: t.tagName, text: norm(t.textContent).slice(0, 140) };
}

function rectInPage(i) {
  // Read the crop target's viewport rect after scrolling has settled.
  const el = document.querySelector(`[data-ev-crop="${i}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height };
}

(async () => {
  const [cfgPath, cropsDir, metaOut, filter] = process.argv.slice(2);
  const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8')).filter(e => !filter || e.id.includes(filter));
  fs.mkdirSync(cropsDir, { recursive: true });
  const meta = fs.existsSync(metaOut) ? JSON.parse(fs.readFileSync(metaOut, 'utf8')) : {};
  const browser = await chromium.launch({ proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined });
  const VW = 1280, VH = 900;
  for (const ev of cfg) {
    const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, deviceScaleFactor: 1.5, locale: 'en-US' });
    const page = await ctx.newPage();
    const rec = { id: ev.id, url: ev.url, crops: [], notes: [] };
    try {
      const resp = await page.goto(ev.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      rec.status = resp ? resp.status() : null;
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(1500);
      await page.addStyleTag({ content: HIDE });
      rec.title = await page.title();
      rec.finalUrl = page.url();
      rec.capturedAt = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
      for (let i = 0; i < ev.excerpts.length; i++) {
        const ex = ev.excerpts[i];
        const hit = await page.evaluate(markInPage, { ...ex, i });
        if (!hit) { rec.notes.push(`not found: ${ex.find}`); continue; }
        // Lazy images above the target can shift it after scrolling: re-center until it holds still.
        let r = null, prev = null;
        for (let k = 0; k < 5; k++) {
          await page.waitForTimeout(700);
          r = await page.evaluate(rectInPage, i);
          if (!r) break;
          const inView = r.y >= 0 && r.y + Math.min(r.h, VH * 0.6) <= VH;
          if (inView && prev && Math.abs(prev.y - r.y) < 1) break;
          prev = r;
          await page.evaluate(n => document.querySelector(`[data-ev-crop="${n}"]`).scrollIntoView({ block: 'center' }), i);
        }
        if (!r) { rec.notes.push(`no rect: ${ex.find}`); continue; }
        const [padTop, padBottom] = ex.pad || [90, 70];
        const padX = 36;
        const x = ex.fullWidth ? 0 : Math.max(0, r.x - padX), y = Math.max(0, r.y - padTop);
        const w = ex.fullWidth ? VW : Math.min(VW - x, r.w + 2 * padX), h = Math.min(VH - y, r.h + padTop + padBottom);
        const file = path.join(cropsDir, `${ev.id}_${i + 1}.png`);
        await page.screenshot({ path: file, clip: { x, y, width: w, height: h } });
        rec.crops.push({ file: path.basename(file), find: ex.find, cssW: Math.round(w), cssH: Math.round(h), matches: hit.count, tag: hit.tag });
      }
    } catch (e) {
      rec.notes.push('error: ' + e.message.split('\n')[0]);
    }
    meta[ev.id] = rec;
    console.log(`${ev.id}: status=${rec.status} crops=${rec.crops.length}/${ev.excerpts.length}${rec.notes.length ? ' notes=' + rec.notes.join('; ') : ''}`);
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(metaOut, JSON.stringify(meta, null, 1));
})();
