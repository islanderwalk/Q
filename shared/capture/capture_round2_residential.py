# -*- coding: utf-8 -*-
"""QSearch 第二輪補證據（本機、住宅 IP、Playwright，未登入）。雲端出口 IP 開 FB／IG 會被導去登入頁，所以這兩站在本機跑。

  fb   Q2：NASA 粉專 → 一則公開貼文。畫面只有相對時間（如 5h），頁面內嵌資料有發文的 unix 秒數；
           粉專數字 ID 出現在哪些欄位；粉專透明度頁有沒有寫「粉專編號」
  ig   Q5：@nasa Reels 分頁的 play_count，和 10/30 11:05 那一次（q5/evidence/playwright/ig_reel_fields.json）逐支對照

用法：python capture_round2_residential.py fb,ig [輸出資料夾，預設 ./out]
規則（沿用 shared/capture/README.md）：只開未登入公開頁；同站兩次導覽間隔 ≥2 秒；每站載入 ≤30 次；
不存 cookie、token、cursor；FB／IG 不存整頁 HTML，只存欄位名與數值；登入或註冊視窗照實截下，不點擊；
不用代理、不偽裝指紋。留言者姓名、頭像以黑框遮住。
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TW = timezone(timedelta(hours=8))
OUT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).resolve().parent / "out"
LAST, LOADS = {}, {"fb": 0, "ig": 0}
MANIFEST, LOG = [], []


def now_iso():
    return datetime.now(TW).isoformat(timespec="seconds")


def tw(ts):
    return datetime.fromtimestamp(ts, TW).strftime("%Y-%m-%d %H:%M")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def goto(page, site, url, wait_ms, gap=2.0):
    d = time.time() - LAST.get(site, 0.0)
    if d < gap:
        time.sleep(gap - d)
    LAST[site] = time.time()
    LOADS[site] += 1
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(wait_ms)
    return resp


def shot(page, rel, **kw):
    (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / rel), full_page=False, **kw)
    return rel


def add(**kw):
    kw.setdefault("kind", "screenshot")
    kw.setdefault("status", "real")
    kw.setdefault("captured_at", now_iso())
    MANIFEST.append(kw)
    log("  +", kw["id"], "|", kw.get("result", ""))


# 與 shared/capture/capture.py 相同的標註樣式；另加 masks（黑框遮個資）與 below（標籤放框下方）
OVERLAY = r"""
(cfg) => {
  const old = document.getElementById('__ev'); if (old) old.remove();
  document.querySelectorAll('.__ev_fixed').forEach(e => e.remove());
  const root = document.createElement('div'); root.id = '__ev';
  root.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none';
  document.body.appendChild(root);
  const FONT = '"Microsoft JhengHei","PingFang TC","Noto Sans TC",system-ui,sans-serif';
  const COLORS = ['#f43f5e', '#f59e0b', '#22c55e', '#3b82f6', '#a855f7', '#06b6d4'];
  for (const sel of (cfg.masks || [])) {
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
        t.style.cssText = `position:absolute;left:${Math.max(4, r.left + scrollX - 3)}px;top:${y}px;background:${color};color:#fff;font:600 14px/1.7 ${FONT};padding:0 9px;border-radius:6px;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,.35)`;
        root.appendChild(t);
      }
    });
  });
  if (cfg.card) {
    const c = document.createElement('div'); c.className = '__ev_fixed';
    c.style.cssText = `position:fixed;${cfg.card.left ? 'left:16px' : 'right:16px'};${cfg.card.bottom ? 'bottom:52px' : 'top:16px'};max-width:${cfg.card.width || 440}px;background:rgba(17,24,39,.95);color:#f9fafb;font:14px/1.65 ${FONT};padding:12px 16px;border-radius:10px;border:1px solid rgba(255,255,255,.18);z-index:2147483647;box-shadow:0 6px 24px rgba(0,0,0,.4)`;
    const h = document.createElement('div'); h.textContent = cfg.card.title; h.style.cssText = 'font-weight:700;font-size:15px;margin-bottom:4px';
    c.appendChild(h);
    (cfg.card.lines || []).forEach(s => { const p = document.createElement('div'); p.textContent = s; p.style.whiteSpace = 'pre-wrap'; p.style.wordBreak = 'break-all'; c.appendChild(p); });
    document.body.appendChild(c);
  }
  const tag = document.createElement('div'); tag.className = '__ev_fixed';
  tag.textContent = '標註為截圖時加上 · Playwright 實抓（未登入、住宅 IP） · ' + cfg.stamp;
  tag.style.cssText = `position:fixed;left:12px;bottom:12px;background:rgba(17,24,39,.9);color:#e5e7eb;font:12px/1.6 ${FONT};padding:3px 10px;border-radius:6px;z-index:2147483647`;
  document.body.appendChild(tag);
  return found;
}
"""


def overlay(page, items, card=None, masks=None):
    try:
        return page.evaluate(OVERLAY, {"items": items, "card": card, "masks": masks or [], "stamp": now_iso()})
    except Exception as e:
        log("  overlay error:", e)
        return []


# ----------------------------------------------------------------- Q2 Facebook
COMMENT_MASKS = [
    "[role=article][aria-label*='Comment' i]", "[role=article][aria-label*='留言']",
    "a[href*='comment_id'] img", "a[href*='comment_id']", "[role=article] svg image",
]

FB_DATA_JS = r"""() => {
  const h = document.documentElement.innerHTML;
  const nums = re => [...h.matchAll(re)].map(m => m[1]);
  const uniq = a => [...new Set(a)];
  const keys = ['pageID', 'page_id', 'delegate_page_id', 'userID', 'profile_owner_id', 'owning_profile_id', 'actorID', 'id_of_page'];
  const ids = {};
  for (const k of keys) { const v = uniq(nums(new RegExp('"' + k + '"\\s*:\\s*"?(\\d{6,})', 'g'))); if (v.length) ids[k] = v.slice(0, 4); }
  const metas = {};
  document.querySelectorAll('meta[property^="al:"], meta[name^="al:"]').forEach(m => {
    const c = m.getAttribute('content') || ''; const mm = c.match(/fb:\/\/(page|profile)\/(\d{6,})/); if (mm) metas[m.getAttribute('property') || m.getAttribute('name')] = mm[1] + '/' + mm[2]; });
  const ctimes = nums(/"creation_time"\s*:\s*(\d{10})/g).map(Number);
  const ptimes = nums(/"publish_time"\s*:\s*(\d{10})/g).map(Number);
  const count = a => { const c = {}; a.forEach(x => c[x] = (c[x] || 0) + 1); return Object.entries(c).sort((x, y) => y[1] - x[1]).slice(0, 5).map(([t, n]) => ({t: Number(t), n})); };
  const timeRe = /^(\d+\s?(s|m|h|d|w|mins?|hrs?|days?|weeks?)|Yesterday|Just now|\d{1,2}\s?[A-Z][a-z]{2}\s?\d{0,4}|[A-Z][a-z]+ \d{1,2}( at [\d:]+ ?[AP]M)?)$/i;
  let rel = null;
  for (const a of document.querySelectorAll('a[href*="/posts/"], a[href*="permalink"], a[href*="story_fbid"]')) {
    const t = (a.innerText || '').trim(); if (timeRe.test(t)) { a.setAttribute('data-ev-reltime', '1'); rel = t; break; }
  }
  const heads = [...document.querySelectorAll('h1, h2, h3')].map(e => e.innerText.trim()).filter(Boolean).slice(0, 3);
  return {ids, metas, ctimes: count(ctimes), ptimes: count(ptimes), rel, heads,
          dialog: !!document.querySelector('[role=dialog]'), title: document.title.slice(0, 80)};
}"""


def rel_hours(rel):
    m = re.match(r"^(\d+)\s?(m|h|d|w)", rel or "", re.I)
    if not m:
        return None
    return int(m.group(1)) * {"m": 1 / 60, "h": 1, "d": 24, "w": 168}[m.group(2).lower()]


def run_fb(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    goto(page, "fb", "https://www.facebook.com/NASA", 5000)
    home = page.evaluate(FB_DATA_JS)
    post = page.evaluate(r"""() => { const h = [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href') || '')
        .find(h => /\/NASA\/posts\//.test(h)); return h ? h.split('?')[0] : null; }""")
    log("  fb home:", json.dumps(home, ensure_ascii=False)[:500], "post:", post)
    if not post:
        log("  ! fb：粉專頁沒有公開貼文連結（可能被登入牆擋住）")
        ctx.close()
        return
    post_url = post if post.startswith("http") else "https://www.facebook.com" + post
    goto(page, "fb", post_url, 6000)
    d = page.evaluate(FB_DATA_JS)
    log("  fb post:", json.dumps(d, ensure_ascii=False)[:800])
    # 貼文的發文時間：內嵌資料中出現最多次的 creation_time（留言也有自己的時間，次數少）
    ct = d["ctimes"][0]["t"] if d["ctimes"] else (d["ptimes"][0]["t"] if d["ptimes"] else None)
    age_h = (time.time() - ct) / 3600 if ct else None
    rh = rel_hours(d["rel"])
    agree = (age_h is not None and rh is not None and abs(age_h - rh) <= max(1.0, rh * 0.25))
    ids = dict(home["ids"])
    for k, v in d["ids"].items():
        ids[k] = sorted(set(ids.get(k, []) + v))[:4]
    metas = {**home["metas"], **d["metas"]}
    id_lines = [f"{k}: {', '.join(v)}" for k, v in ids.items()] + [f"{k}: {v}" for k, v in metas.items()]
    lines = [
        f"貼文：{post_url[:80]}…",
        f"畫面上的發文時間：{d['rel'] or '沒讀到'}（相對時間）",
        (f"內嵌資料 creation_time = {ct} → {tw(ct)}（台北），距今 {age_h:.1f} 小時" if ct else "內嵌資料沒有 creation_time"),
        (f"兩者一致：可存絕對時間，不必解析畫面上的「{d['rel']}」" if agree else "兩者對不上，要再查"),
        "數字 ID（只取數字）：" + ("；".join(id_lines)[:300] if id_lines else "未出現"),
        "留言者姓名與頭像已黑框遮住；登入視窗照實截下",
    ]
    page.evaluate("() => window.scrollTo(0, 0)")
    overlay(page, [{"sel": "[data-ev-reltime]", "label": f"畫面只有相對時間：{d['rel'] or '—'}", "below": True},
                   {"sel": "[role=dialog]", "label": "未登入時的登入視窗（沒有點擊）"}],
            card={"title": "FB 單則貼文：發文時間與粉專 ID 從哪裡來", "width": 520, "lines": lines}, masks=COMMENT_MASKS)
    rel = shot(page, "q2/evidence/playwright/fb_post_time_and_ids.png")
    add(q="2", id="q2-fb-post-time-ids", node="parser", title="FB 貼文：畫面相對時間 vs 內嵌 creation_time；數字 ID 欄位",
        claim="Q2 的「發文時間」可以存成絕對時間（內嵌資料的 unix 秒數），不必解析畫面上的「5h」；粉專數字 ID 在頁面資料欄位裡，網址只有使用者名稱",
        result=f"畫面 {d['rel'] or '—'}；creation_time {ct or '—'}" + (f"（{tw(ct)}，距今 {age_h:.1f} 小時）" if ct else "") +
               f"；{'一致' if agree else '不一致或缺值'}；ID 欄位 " + ("；".join(id_lines) if id_lines else "未見"),
        image=rel, fields={"rel_time": d["rel"], "creation_time": ct, "creation_time_candidates": d["ctimes"], "ids": ids, "app_link_meta": metas},
        source=f"Playwright 實抓（未登入、住宅 IP）https://www.facebook.com/NASA → {post_url}；只記數字，不存 HTML；標註為截圖時加上")

    # 粉專透明度頁：新版粉專在這裡列「Page ID」
    goto(page, "fb", "https://www.facebook.com/NASA/about_profile_transparency", 5000)
    t = page.evaluate(r"""() => {
      const out = {pageIdText: null, created: null};
      const els = [...document.querySelectorAll('span, div')];
      for (const e of els) { const s = (e.innerText || '').trim();
        if (!out.pageIdText && /^\d{6,}$/.test(s)) { const box = e.closest('div[class]'); const ctx = box ? box.parentElement.innerText : '';
          if (/Page ID|粉絲專頁編號|ID/.test(ctx)) { e.setAttribute('data-ev-pageid', '1'); out.pageIdText = s; } }
        if (!out.created && /^Page created|^粉絲專頁建立日期|^Created/.test(s) && s.length < 80) { e.setAttribute('data-ev-created', '1'); out.created = s; } }
      out.hasLabel = /Page ID|粉絲專頁編號/.test(document.body.innerText);
      out.dialog = !!document.querySelector('[role=dialog]'); out.url = location.href; return out; }""")
    log("  fb transparency:", json.dumps(t, ensure_ascii=False))
    overlay(page, [{"sel": "[data-ev-pageid]", "label": f"Page ID：{t['pageIdText'] or '—'}"},
                   {"sel": "[data-ev-created]", "label": "粉專建立日期"}],
            card={"title": "粉專透明度頁（未登入）", "width": 480, "lines": [
                f"網址：{t['url'][:90]}",
                f"頁面上有「Page ID」字樣：{'有' if t['hasLabel'] else '沒有'}；讀到的數字：{t['pageIdText'] or '沒有'}",
                f"與貼文頁資料欄位對照：{'；'.join(id_lines)[:200] if id_lines else '貼文頁沒有數字 ID'}",
                "登入視窗照實截下" if t["dialog"] else "沒有登入視窗"]})
    rel = shot(page, "q2/evidence/playwright/fb_page_transparency_id.png")
    add(q="2", id="q2-fb-page-transparency", node="platforms", title="FB 粉專透明度頁：粉專數字 ID",
        claim="粉專數字 ID（Q2 的「粉絲頁 ID」欄位與 B-tree 索引的鍵）要從頁面資料或透明度頁取得，網址只有使用者名稱",
        result=f"Page ID 字樣 {'有' if t['hasLabel'] else '無'}；數字 {t['pageIdText'] or '未見'}；登入視窗 {'有' if t['dialog'] else '無'}",
        image=rel, fields={"page_id_text": t["pageIdText"], "has_label": t["hasLabel"]},
        source="Playwright 實抓（未登入、住宅 IP）https://www.facebook.com/NASA/about_profile_transparency；標註為截圖時加上")
    ctx.close()


# ----------------------------------------------------------------- Q5 Instagram
# 10/30 11:05:03（台北）那一次的 play_count（q5/evidence/playwright/ig_reel_fields.json）
PREV_AT = "2026-10-30T11:05:03+08:00"
PREV = {"DfF3eqGSKKc": 1104795, "De7XCa_TJ4h": 4298087, "De2t82PPyx9": 2929414, "Dez3pRXgW1d": 2400913,
        "DeuH0dDFGcg": 3994446, "DeTUk4sDSp-": 11082721, "DeSM5CYPDfd": 3707327, "DePHLhtON_i": 3191143,
        "DeMIwYYPAMt": 4728200}

INLINE_JS = r"""() => {
  const out = [];
  const walk = n => {
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (n && typeof n === 'object') {
      if (typeof n.play_count === 'number' && typeof n.code === 'string') out.push({code: n.code, play_count: n.play_count, taken_at: n.taken_at || null});
      for (const k in n) walk(n[k]);
    }
  };
  for (const s of document.querySelectorAll('script[type="application/json"]')) { try { walk(JSON.parse(s.textContent)); } catch (e) {} }
  return out;
}"""


def fmt(v):
    return f"{v:,}"


def run_ig(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    resp = goto(page, "ig", "https://www.instagram.com/nasa/reels/", 6000)
    status = resp.status if resp else None
    final = page.url
    now = {}
    for r in page.evaluate(INLINE_JS):
        now.setdefault(r["code"], r)
    wall = page.evaluate(r"""() => location.pathname.startsWith('/accounts/login')""")
    rows = []
    for code, prev in PREV.items():
        if code in now:
            cur = now[code]["play_count"]
            rows.append({"code": code, "prev": prev, "now": cur, "delta": cur - prev, "pct": (cur - prev) / prev * 100,
                         "taken_at": now[code]["taken_at"]})
    log(f"  ig status={status} final={final} wall={wall} codes_now={len(now)} matched={len(rows)}")
    for r in rows:
        log(f"   {r['code']}: {r['prev']} -> {r['now']} (+{r['delta']}, {r['pct']:.2f}%)")
    # 第一列縮圖捲進視窗；只標有對照值的縮圖
    page.evaluate(r"""() => { const a = document.querySelector('a[href*="/reel/"]'); if (a) a.scrollIntoView({block: 'center'}); }""")
    page.wait_for_timeout(800)
    items = [{"sel": f"a[href*='/reel/{r['code']}']", "label": f"+{fmt(r['delta'])}（{r['pct']:+.1f}%）", "below": i % 2 == 1}
             for i, r in enumerate(rows[:6])]
    lines = [f"第 1 次 {PREV_AT[5:16].replace('T', ' ')}　→　第 2 次 {now_iso()[5:16].replace('T', ' ')}（同一個公開分頁、未登入）"]
    for r in rows[:8]:
        age = f"，發佈 {(time.time() - r['taken_at']) / 86400:.1f} 天" if r["taken_at"] else ""
        lines.append(f"{r['code']}：{fmt(r['prev'])} → {fmt(r['now'])}（+{fmt(r['delta'])}{age}）")
    lines.append("欄位是 play_count（不是 video_view_count）；JSON 只存欄位名與數值")
    lines.append("結論：觀看數發佈數天後仍在漲，1 小時 SLA 只管第一筆；之後要排程重抓，ES 存值要附抓取時間")
    overlay(page, items, card={"title": "@nasa Reels：同一批 Reels 的 play_count 隔幾小時再抓", "width": 560, "lines": lines})
    rel = shot(page, "q5/evidence/playwright/ig_reels_play_count_drift.png")
    data = {"prev_captured_at": PREV_AT, "captured_at": now_iso(), "status": status, "final_url_path": final.split("?")[0],
            "field": "play_count", "rows": rows}
    out = OUT / "q5/evidence/playwright/ig_reels_play_count_drift.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    add(q="5", id="q5-ig-reels-play-count-drift", node="platforms", title="IG Reels：同一批 Reels 的 play_count 隔幾小時再抓",
        claim="Reels 觀看數在發佈數天後仍持續增加；Q5 的 1 小時 SLA 只保證第一筆可查，之後要排程重抓，ES 存值附抓取時間，回補要標「抓到當下」",
        result=f"對上 {len(rows)} 支；" + "；".join(f"{r['code']} +{r['delta']:,}（{r['pct']:+.2f}%）" for r in rows[:4]),
        image=rel, data="q5/evidence/playwright/ig_reels_play_count_drift.json",
        source="Playwright 實抓（未登入、住宅 IP）https://www.instagram.com/nasa/reels/；與 10/30 11:05 的 ig_reel_fields.json 對照；標註為截圖時加上")
    ctx.close()


if __name__ == "__main__":
    modes = (sys.argv[1] if len(sys.argv) > 1 else "fb,ig").split(",")
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            if "fb" in modes:
                run_fb(b)
            if "ig" in modes:
                run_ig(b)
        finally:
            b.close()
    log("  loads:", json.dumps(LOADS))
    (OUT / f"manifest_{'_'.join(modes)}.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(OUT / "capture_log.txt", "a", encoding="utf-8") as fp:
        fp.write(f"== round2 residential {','.join(modes)} {now_iso()} ==\n" + "\n".join(LOG) + "\n")
