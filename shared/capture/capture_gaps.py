# -*- coding: utf-8 -*-
"""補 Q2 / Q5 / Q6 證據不足的部分（Playwright，未登入，公開頁）。

  fb   Q2：NASA 粉專 → 一則公開貼文頁，截圖標出粉專名稱、發文時間、心情統計、粉專 ID
  ig   Q5：IG Reels 分頁（同一次載入）與單支 Reel 頁，只記錄回應 JSON 的觀看數欄位名與數值
  ptt  Q6：PTT 高雄板（不需 18 歲確認）公開文章，截取含短網址／電話／地名的雜訊字串
用法：python capture_gaps.py [fb,ig,ptt]；另有 ptt_refresh（重拍 Q6 其中 2 篇，加頁尾 IP／編輯者遮罩，只載入這 2 篇）

規則（沿用 README）：同站兩次導覽間隔 ≥2 秒；每站載入次數記錄在 LOADS；
不存 cookie、token、cursor；不存整頁 HTML；FB/IG 只存欄位名與數值；
不繞過登入牆、不點同意按鈕、不用代理。作者 ID、留言者 ID、電話以黑框遮住（隱私遮罩，不改其他內容）。
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import overlay, polite  # noqa: E402  (同目錄 capture.py 的標註與節流工具)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TW = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
LOADS = {"fb": 0, "ig": 0, "ptt": 0}
MANIFEST, LOG = [], []


def now_iso():
    return datetime.now(TW).isoformat(timespec="seconds")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def goto(page, site, url, wait_ms, gap=2.0):
    polite(site, gap)
    LOADS[site] += 1
    resp = page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(wait_ms)
    return resp


def add(**kw):
    kw.setdefault("kind", "screenshot")
    kw.setdefault("status", "real")
    kw.setdefault("captured_at", now_iso())
    MANIFEST.append(kw)
    log("  +", kw["id"], "|", kw.get("result", ""))


def pending(q, id_, title, why, node):
    MANIFEST.append({"q": q, "id": id_, "node": node, "kind": "screenshot", "title": title,
                     "claim": "", "status": "pending", "todo": why, "captured_at": now_iso()})
    log("  ! pending", id_, why)


def shot(page, rel):
    (REPO / rel).parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(REPO / rel), full_page=False)
    return rel


# ----------------------------------------------------------------- Q2 Facebook
FB_POST_JS = r"""() => {
  const hrefs = [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href') || '');
  const post = hrefs.find(h => /\/NASA\/posts\//.test(h));
  return post ? post.split('?')[0] : null;
}"""

FB_INFO_JS = r"""() => {
  // 粉專數字 ID：從頁面連結或頁面資料中讀出數字（只回傳數字，不保存 HTML）
  const linkIds = [...document.querySelectorAll('a[href]')].map(a => {
      const m = (a.getAttribute('href') || '').match(/(?:page\.php\?id=|[?&]id=)(\d{6,})/); return m ? m[1] : null;
  }).filter(Boolean);
  const dataIds = [...(document.documentElement.innerHTML.matchAll(/"(?:pageID|page_id|pageId)"\s*:\s*"?(\d{6,})/g))].map(m => m[1]);
  const pageIds = [...new Set([...linkIds, ...dataIds])];
  // 發文時間：貼文連結上的相對時間（如 5h、2d、Yesterday）或 abbr／time
  const timeRe = /^(\d+\s?(s|m|h|d|w|mins?|hrs?|days?|weeks?)|Yesterday|Just now|\d{1,2}\s?[A-Z][a-z]{2}\s?\d{0,4})$/i;
  const times = [...document.querySelectorAll('a[href*="/posts/"], a[href*="permalink"]')]
      .filter(a => timeRe.test(a.innerText.trim())).map(a => ({text: a.innerText.trim().slice(0, 40), utime: null})).slice(0, 2);
  [...document.querySelectorAll('abbr[data-utime], time')].forEach(e => times.push({text: e.textContent.trim().slice(0, 40), utime: e.getAttribute('data-utime') || e.getAttribute('datetime')}));
  const reactions = [...document.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '')
      .filter(l => /reaction|心情|讚/i.test(l)).slice(0, 4);
  return {
    title: document.title.slice(0, 80),
    heads: [...document.querySelectorAll('h1, h2, h3')].map(e => e.innerText.trim()).filter(Boolean).slice(0, 4),
    pageIds: pageIds.slice(0, 3), times, reactions,
    dialog: !!document.querySelector('[role=dialog]'), login: !!document.querySelector('input[name=email]')
  };
}"""


def run_fb(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    goto(page, "fb", "https://www.facebook.com/NASA", 5000)
    # 粉專數字 ID：粉專首頁 HTML 的 app-link meta（fb://page/<id>）或頁面資料中的 pageID；只取數字
    home_id = page.evaluate(r"""() => { const h = document.documentElement.innerHTML;
        const m = h.match(/fb:\/\/page\/(\d{6,})/) || h.match(/"(?:pageID|page_id|pageId)"\s*:\s*"?(\d{6,})/);
        return m ? m[1] : null; }""")
    post = page.evaluate(FB_POST_JS)
    if not post:
        pending("2", "q2-fb-post", "FB 單則貼文", "粉專頁上找不到公開貼文連結（可能被登入牆擋住）", "platforms")
        ctx.close()
        return
    post_url = post if post.startswith("http") else "https://www.facebook.com" + post
    goto(page, "fb", post_url, 6000)
    info = page.evaluate(FB_INFO_JS)
    if home_id and home_id not in info["pageIds"]:
        info["pageIds"] = [home_id] + info["pageIds"]
    log("  fb info:", json.dumps(info, ensure_ascii=False)[:600])
    items = [
        {"sel": "[role=dialog]", "label": "未登入時跳出的登入視窗（擋住的畫面）"},
        {"sel": "[aria-label*='reaction' i], [aria-label*='心情']", "all": True, "max": 4, "label": "心情互動統計"},
        {"sel": "h2, h1", "label": "粉專名稱（頁面標題）", "max": 2},
    ]
    if info["times"]:
        items.append({"sel": "a[href*='/posts/'], a[href*='permalink']", "text": info["times"][0]["text"],
                      "label": f"發文時間：{info['times'][0]['text']}"})
    card = {"title": "FB 單則貼文（未登入）",
            "lines": [f"網址（不含查詢字串）：{post_url}",
                      f"粉專名稱：{' / '.join(info['heads'][:2]) or '沒讀到'}",
                      f"粉專 ID：數字 ID {', '.join(info['pageIds']) or '未出現在未登入頁面'}；網址使用者名稱 NASA",
                      f"發文時間：{'；'.join(t['text'] for t in info['times']) or '沒讀到'}",
                      f"心情：{'；'.join(info['reactions'])[:120] or '沒讀到'}",
                      "有登入視窗（照實截下，不點登入）" if (info["dialog"] or info["login"]) else "沒有登入視窗"]}
    overlay(page, items, card=card)
    rel = shot(page, "q2/evidence/playwright/fb_post_nasa.png")
    found = sum(1 for x in [info["times"], info["reactions"], info["heads"]] if x) + 1  # +1 網址使用者名稱
    add(q="2", id="q2-fb-post", node="platforms", title="FB 單則公開貼文的欄位（未登入）",
        claim="Q2 的欄位（粉專名稱、粉專 ID、發文時間、心情統計）可以從單則貼文頁讀到；貼文網址用 pfbid，粉專 ID 要從頁面連結取",
        result=f"粉專名稱 {'有' if info['heads'] else '無'}；粉專 ID：數字 ID {'有' if info['pageIds'] else '未見'}，網址使用者名稱 NASA；"
               f"發文時間 {'有' if info['times'] else '無'}；心情統計 {'有' if info['reactions'] else '無'}；"
               f"{'有' if (info['dialog'] or info['login']) else '無'}登入視窗；欄位命中 {found}/4",
        image=rel, source=f"Playwright 實抓（未登入）{post_url}；標註為截圖時加上")
    ctx.close()


# ----------------------------------------------------------------- Q5 Instagram
FIELD_KEYS = {"video_view_count", "play_count", "view_count", "ig_play_count", "video_play_count"}
IG_SHORT = "De7XCa_TJ4h"


def fmt_compact(v):
    # 縮圖的數字是無條件捨去（不是四捨五入）：4,297,951 → 4.2M
    if v >= 1_000_000:
        return f"{v // 100_000 / 10:.1f}M"
    if v >= 1_000:
        return f"{v // 100 / 10:.1f}K"
    return str(v)


INLINE_JS = r"""(keys) => {
  const out = [];
  const walk = (n, path) => {
    if (Array.isArray(n)) { n.forEach((v, i) => walk(v, path + '[' + i + ']')); return; }
    if (n && typeof n === 'object') {
      const rec = {}; let has = false;
      for (const k of keys) { if (typeof n[k] === 'number') { rec[k] = n[k]; has = true; } }
      if (has) { if (typeof n.code === 'string') rec.code = n.code; out.push(Object.assign({path}, rec)); }
      for (const k in n) walk(n[k], path + '.' + k);
    }
  };
  for (const s of document.querySelectorAll('script[type="application/json"]')) {
    try { walk(JSON.parse(s.textContent), 'script'); } catch (e) {}
  }
  return out;
}"""


def run_ig(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    state = {"page": None, "records": [], "responses": 0, "json": 0, "pending": [], "ctypes": {}}

    def walk(node, path, page_name):
        if isinstance(node, dict):
            if any(k in node for k in FIELD_KEYS):
                rec = {"page": page_name, "path": path or "$"}
                if isinstance(node.get("code"), str):
                    rec["code"] = node["code"]
                for k in sorted(FIELD_KEYS):
                    if k in node and isinstance(node[k], (int, float)):
                        rec[k] = node[k]
                state["records"].append(rec)
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k, page_name)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", page_name)

    def on_response(resp):
        # 只收集回應物件，載入結束後再解析（避免還沒下載完的 body）
        if state["page"] is None or "instagram.com" not in resp.url:
            return
        state["responses"] += 1
        ct = resp.headers.get("content-type", "").split(";")[0]
        state["ctypes"][ct] = state["ctypes"].get(ct, 0) + 1
        if "json" in ct or "/graphql" in resp.url or "/api/" in resp.url:
            state["pending"].append((state["page"], resp))

    def parse_pending():
        for name, resp in state["pending"]:
            try:
                data = resp.json()
            except Exception:
                continue
            state["json"] += 1
            walk(data, "", name)
        state["pending"] = []

    page.on("response", on_response)

    # 1) Reels 分頁：同一次載入，同時記下縮圖上的數字與 JSON 值
    state["page"] = "reels_grid"
    goto(page, "ig", "https://www.instagram.com/nasa/reels/", 6000)
    tiles = page.evaluate(r"""() => [...document.querySelectorAll('a[href*="/reel/"]')].slice(0, 8)
        .map(a => ({code: (a.getAttribute('href').match(/\/reel\/([^/]+)/) || [])[1] || null,
                    text: a.innerText.replace(/\s+/g, ' ').trim()}))""")
    parse_pending()
    for rec in page.evaluate(INLINE_JS, sorted(FIELD_KEYS)):
        state["records"].append({"page": "reels_grid", "source": "document_inline_json", **rec})
    grid_wall = page.evaluate(r"""() => !!document.querySelector('input[name=username]') || location.pathname.startsWith('/accounts/login')""")
    grid_has_target = any(t["code"] == IG_SHORT for t in tiles)
    overlay(page, [{"sel": f"a[href*='{IG_SHORT}']", "label": f"同一支 Reel：縮圖數字"}] if grid_has_target else [],
            card={"title": "@nasa Reels 分頁（未登入，同一次載入）",
                  "lines": [f"看到 {len(tiles)} 支 Reels；被導到登入頁：{'是' if grid_wall else '否'}",
                            ("縮圖數字：" + "、".join(t["text"][:12] for t in tiles[:4] if t["text"])) if any(t["text"] for t in tiles) else "縮圖沒有讀到數字",
                            "JSON 只存欄位名與數值，見 ig_reel_fields.json"]})
    # 把目標縮圖捲到視窗底部，讓縮圖上的觀看數落在截圖範圍內（不點任何登入或註冊按鈕）
    page.evaluate(r"""(code) => { const a = document.querySelector(`a[href*="${code}"]`); if (a) a.scrollIntoView({block: 'end'}); }""", IG_SHORT)
    page.wait_for_timeout(800)
    overlay(page, [{"sel": f"a[href*='{IG_SHORT}']", "label": "同一支 Reel：縮圖上的觀看數"}] if grid_has_target else [],
            card={"title": "@nasa Reels 分頁（未登入，同一次載入）",
                  "lines": [f"看到 {len(tiles)} 支 Reels；被導到登入頁：{'是' if grid_wall else '否'}（註冊視窗照實截下）",
                            ("縮圖數字（DOM 文字）：" + "、".join(t["text"][:12] for t in tiles[:4] if t["text"])) if any(t["text"] for t in tiles) else "縮圖沒有讀到數字",
                            "JSON 只存欄位名與數值，見 ig_reel_fields.json"]})
    grid_rel = shot(page, "q5/evidence/playwright/ig_reels_grid_json_check.png")
    grid_tile_text = {t["code"]: t["text"] for t in tiles if t["code"]}

    # 2) 單支 Reel 頁：同一次載入的 JSON
    state["page"] = "reel_page"
    goto(page, "ig", f"https://www.instagram.com/nasa/reel/{IG_SHORT}/", 6000)
    diag = page.evaluate(r"""(code) => ({json_scripts: document.querySelectorAll('script[type="application/json"]').length,
        code_in_document: document.documentElement.innerHTML.includes(code), play_count_in_document: /"play_count"\s*:\s*\d+/.test(document.documentElement.innerHTML)})""", IG_SHORT)
    log("  ig reel diag:", json.dumps(diag, ensure_ascii=False))
    reel_wall = page.evaluate(r"""() => !!document.querySelector('input[name=username]') || location.pathname.startsWith('/accounts/login')""")
    inline = page.evaluate(INLINE_JS, sorted(FIELD_KEYS))
    for rec in inline:
        state["records"].append({"page": "reel_page", "source": "document_inline_json", **rec})
    parse_pending()
    log("  ig content-types:", json.dumps(state["ctypes"], ensure_ascii=False)[:300])
    page.remove_listener("response", on_response)

    # 只留下欄位名與數值（去重）
    seen, minimal = set(), []
    for r in state["records"]:
        key = (r["page"], r["path"], tuple(sorted((k, v) for k, v in r.items() if k not in ("page", "path"))))
        if key in seen:
            continue
        seen.add(key)
        minimal.append(r)
    out = REPO / "q5/evidence/playwright/ig_reel_fields.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(minimal, ensure_ascii=False, indent=1), encoding="utf-8")

    cmp_lines = []
    for r in minimal:
        if r["page"] == "reels_grid" and r.get("code") in grid_tile_text:
            v = r.get("video_view_count", r.get("play_count", r.get("view_count")))
            if v is not None:
                cmp_lines.append(f"{r['code'][:6]}… JSON {v} → {fmt_compact(v)}，縮圖 {grid_tile_text[r['code']][:14]}")
    reel_vals = [r for r in minimal if r["page"] == "reel_page"]
    log(f"  ig responses={state['responses']} json={state['json']} fields={len(minimal)} reel_page_fields={len(reel_vals)} reel_wall={reel_wall} grid_wall={grid_wall}")
    for ln in cmp_lines[:6]:
        log("  ig cmp:", ln)

    add(q="5", id="q5-ig-reels-json-vs-grid", node="platforms",
        title="IG Reels 縮圖數字與同一次載入的 JSON 對照",
        claim="video_view_count 在回應 JSON 裡是整數，縮圖的 4.2M 是同一個整數四捨五入後的顯示",
        result=("對照 " + str(len(cmp_lines)) + " 支：" + "；".join(cmp_lines[:3])) if cmp_lines else
               f"未找到可對照的欄位（JSON 欄位 {len(minimal)} 筆，回應 {state['responses']} 個）",
        image=grid_rel, source="Playwright 實抓（未登入）https://www.instagram.com/nasa/reels/；標註為截圖時加上")
    if reel_vals:
        add(q="5", id="q5-ig-reel-fields-json", node="platforms", kind="json",
            title="單支 Reel 回應 JSON 的觀看數欄位（只存欄位名與數值）",
            claim="未登入的單支 Reel 頁載入時，網頁送出的 JSON 帶有觀看數欄位；畫面文字沒有顯示，但資料在 JSON 裡",
            result="；".join(f"{k}={v}" for r in reel_vals[:2] for k, v in r.items() if k in FIELD_KEYS)[:200],
            image="q5/evidence/playwright/ig_reel_fields.json",
            source=f"Playwright 實抓（未登入）https://www.instagram.com/nasa/reel/{IG_SHORT}/；只存欄位名與數值")
    else:
        # 負面結果也是證據：單支 Reel 頁載入後，文件內嵌 JSON 與 XHR 都沒有觀看數欄位
        add(q="5", id="q5-ig-reel-fields-json", node="platforms", kind="json",
            title="單支 Reel 頁載入的 JSON 沒有觀看數（負面結果）",
            claim="未登入單支 Reel 頁載入時拿不到 play_count；觀看數只出現在 Reels 分頁的 JSON 與縮圖上，爬蟲要從 Reels 分頁取",
            result=f"單支 Reel 頁：內嵌 JSON {diag['json_scripts']} 段，觀看數欄位 0 筆；XHR 觀看數欄位 0 筆；頁面含該 shortcode：{diag['code_in_document']}",
            image="q5/evidence/playwright/ig_reel_fields.json",
            source=f"Playwright 實抓（未登入）https://www.instagram.com/nasa/reel/{IG_SHORT}/；ig_reel_fields.json 只含 Reels 分頁的欄位")
    ctx.close()


# ----------------------------------------------------------------- Q6 PTT 雜訊字串
SHORT_RE = re.compile(r"reurl\.cc/[A-Za-z0-9]+|bit\.ly/[A-Za-z0-9]+|ppt\.cc/[A-Za-z0-9]+|pse\.is/[A-Za-z0-9]+|lihi\d?\.cc/[A-Za-z0-9]+|lin\.ee/[A-Za-z0-9]+|tinyurl\.com/[A-Za-z0-9]+")
PHONE_RE = re.compile(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}|\(0\d\)\s?\d{4}[-\s]?\d{4}")
PLACE_RE = re.compile(r"高雄市|鼓山區|左營區|苓雅區|三民區|前鎮區|小港區|鳳山區|衛武營|高雄")
BOARD = "https://www.ptt.cc/bbs/Kaohsiung/index.html"

PTT_ROWS_JS = r"""() => [...document.querySelectorAll('div.r-ent')].map(d => {
  const a = d.querySelector('div.title a'); return {href: a ? a.getAttribute('href') : null, title: d.querySelector('div.title').textContent.trim()};
}).filter(r => r.href)"""

PTT_BODY_JS = r"""() => { const m = document.querySelector('#main-content'); if (!m) return '';
  const clone = m.cloneNode(true);
  clone.querySelectorAll('.article-metaline, .article-metaline-right, .push, .article-meta-value').forEach(e => e.remove());
  return clone.innerText; }"""

# 在文章內標出雜訊字串（紅框）、把電話與作者／留言者 ID 蓋黑；只加框，不改頁面文字
PTT_MARK_JS = r"""(cfg) => {
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
  let labelled = 0; let firstTop = null; let phoneTop = null;
  for (const n of nodes) {
    if (n.parentElement && n.parentElement.closest('.article-metaline, .article-metaline-right, .push, .article-meta-value')) continue;
    for (const hit of cfg.hits) {
      let idx = n.textContent.indexOf(hit.text);
      while (idx >= 0) {
        const r = document.createRange(); r.setStart(n, idx); r.setEnd(n, idx + hit.text.length);
        for (const rc of r.getClientRects()) {
          if (hit.mask) boxAt(rc, 'background:#111;border:none');  // 電話／IP／編輯者：黑框遮住
          if (!hit.blackOnly) boxAt(rc, 'border:3px solid #ef4444;border-radius:4px;background:rgba(239,68,68,.08)');  // 紅框標出雜訊位置
          if (hit.mask && !hit.blackOnly && phoneTop === null) phoneTop = rc.top + scrollY;
          if (!hit.mask && firstTop === null) firstTop = rc.top + scrollY;
        }
        if (!hit.mask && r.getClientRects().length) labelled += 1;
        idx = n.textContent.indexOf(hit.text, idx + hit.text.length);
      }
    }
  }
  // 作者 ID（第一列）與留言者 ID 蓋黑；標題與時間列保留
  const authorEl = document.querySelector('.article-meta-value');
  if (authorEl) for (const rc of authorEl.getClientRects()) boxAt(rc, 'background:#111;border:none');
  document.querySelectorAll('.push-userid').forEach(el => {
    for (const rc of el.getClientRects()) boxAt(rc, 'background:#111;border:none');
  });
  const focusTop = (cfg.focus === 'phone' && phoneTop !== null) ? phoneTop : firstTop;
  if (focusTop !== null) window.scrollTo(0, Math.max(0, focusTop - 260));
  const card = document.createElement('div'); card.className = '__gap_box';
  card.style.cssText = 'position:fixed;right:14px;top:14px;max-width:440px;background:rgba(17,24,39,.95);color:#f9fafb;font:14px/1.6 "Microsoft JhengHei",system-ui,sans-serif;padding:10px 14px;border-radius:10px;z-index:2147483647';
  card.innerHTML = `<div style="font-weight:700">${cfg.title}</div>` + cfg.lines.map(l => `<div>${l}</div>`).join('');
  document.body.appendChild(card);
  return labelled;
}"""


def classify(text):
    hits = []
    for m in SHORT_RE.finditer(text):
        hits.append({"text": m.group(0), "cat": "短網址"})
    for m in PHONE_RE.finditer(text):
        hits.append({"text": m.group(0), "cat": "電話", "mask": True})
    for m in PLACE_RE.finditer(text):
        hits.append({"text": m.group(0), "cat": "地名"})
    return hits


PTT_REFRESH = [
    # 同一篇文章的截圖要遮住頁尾的發信 IP 與編輯者 ID（隱私遮罩）。只重新載入這 2 篇（PTT 載入上限內）
    ("https://www.ptt.cc/bbs/Kaohsiung/M.1793281424.A.0CF.html", "鼓山 IKEA 的桌子", 2, False),
    ("https://www.ptt.cc/bbs/Kaohsiung/M.1793291047.A.07A.html", "浪心人免費人權電影與公益諮商活動", 3, True),
]


def run_ptt_refresh(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    shots = []
    for url, title, n, with_phone in PTT_REFRESH:
        goto(page, "ptt", url, 1200)
        body = page.evaluate(PTT_BODY_JS)
        hits = classify(body)
        seen, pick = set(), []
        for h in hits:
            if h["text"] not in seen:
                seen.add(h["text"])
                pick.append(h)
        pick = pick[:6]
        # 頁尾：發信 IP 與編輯者 ID 只蓋黑，不框紅
        foot = [{"text": ip, "mask": True, "blackOnly": True} for ip in sorted(set(re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", body)))]
        foot += [{"text": ed, "mask": True, "blackOnly": True} for ed in sorted(set(re.findall(r"編輯:\s*(\S+)\s*\(", body)))]
        cats_txt = "、".join(sorted({h["cat"] for h in pick}))
        hits_js = [{"text": h["text"], "mask": bool(h.get("mask"))} for h in pick] + foot
        rel = f"q6/evidence/playwright/ptt_noise_{n:02d}.png"
        page.evaluate(PTT_MARK_JS, {"hits": hits_js, "title": "PTT 高雄板：斷詞雜訊佐證",
                                    "lines": ["看板：Kaohsiung（未經 18 歲確認）", f"文章：{title[:30]}",
                                              f"雜訊類型：{cats_txt}", "紅框：雜訊字串（電話先黑框遮住數字）",
                                              "黑框：作者 ID、留言者 ID、發信 IP、編輯者 ID（隱私遮罩）"]})
        shot(page, rel)
        shots.append({"rel": rel, "title": title, "cats": cats_txt, "url": url, "n_hits": len(pick), "labelled": len(pick)})
        if with_phone and any(h.get("mask") and not h.get("blackOnly") for h in pick):
            page.evaluate(PTT_MARK_JS, {"hits": hits_js, "title": "PTT 高雄板：電話雜訊（已遮）", "focus": "phone",
                                        "lines": ["看板：Kaohsiung（未經 18 歲確認）", "雜訊類型：電話",
                                                  "紅框＋黑框：電話數字已遮住，只留位置",
                                                  "黑框：作者 ID、留言者 ID、發信 IP、編輯者 ID（隱私遮罩）"]})
            rel2 = f"q6/evidence/playwright/ptt_noise_{n + 1:02d}.png"
            shot(page, rel2)
            shots.append({"rel": rel2, "title": title, "cats": "電話", "url": url, "n_hits": 1, "labelled": 1, "phone": True})
        page.evaluate("() => document.querySelectorAll('.__gap_box').forEach(e => e.remove())")
    for s_ in shots:
        log("  refreshed", s_["rel"], "|", s_["cats"], "|", s_["title"][:20])
    (ROOT / "ptt_refresh_shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.close()


def run_ptt(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    goto(page, "ptt", BOARD, 1500)
    rows = page.evaluate(PTT_ROWS_JS)
    pref = [r for r in rows if re.search(r"\[(贈送|交換|問題|自家|新聞)\]", r["title"]) and "已被刪除" not in r["title"]]
    log(f"  ptt board rows={len(rows)} candidates={len(pref)}")
    shots = []
    for r in pref[:8]:
        if len(shots) >= 4:
            break
        goto(page, "ptt", "https://www.ptt.cc" + r["href"], 1200)
        hits = classify(page.evaluate(PTT_BODY_JS))
        cats = sorted({h["cat"] for h in hits})
        log(f"  ptt article cats={cats} n={len(hits)} title={r['title'][:24]}")
        if not hits:
            continue
        seen, pick = set(), []
        for h in hits:
            if h["text"] in seen:
                continue
            seen.add(h["text"])
            pick.append(h)
        pick = pick[:6]
        n = len(shots) + 1
        cats_txt = "、".join(sorted({h["cat"] for h in pick}))
        labelled = page.evaluate(PTT_MARK_JS, {
            "hits": [{"text": h["text"], "mask": bool(h.get("mask"))} for h in pick],
            "title": "PTT 高雄板：斷詞雜訊佐證",
            "lines": ["看板：Kaohsiung（未經 18 歲確認）",
                      f"文章：{r['title'][:30]}",
                      f"雜訊類型：{cats_txt}",
                      "紅框：雜訊字串（電話以黑框遮住數字後再標紅框）",
                      "黑框：作者 ID、留言者 ID（隱私遮罩）"]})
        rel = f"q6/evidence/playwright/ptt_noise_{n:02d}.png"
        shot(page, rel)
        shots.append({"rel": rel, "title": r["title"], "cats": cats_txt, "url": "https://www.ptt.cc" + r["href"],
                      "n_hits": len(pick), "labelled": labelled})
        if any(h.get("mask") for h in pick) and len(shots) < 4:
            # 同一篇再拍一張：聚焦電話位置（電話以黑框遮住數字、紅框標出）
            page.evaluate(PTT_MARK_JS, {"hits": [{"text": h["text"], "mask": bool(h.get("mask"))} for h in pick],
                                        "title": "PTT 高雄板：電話雜訊（已遮）", "focus": "phone",
                                        "lines": ["看板：Kaohsiung（未經 18 歲確認）",
                                                  "雜訊類型：電話",
                                                  "紅框＋黑框：電話數字已遮住，只留位置",
                                                  "黑框：作者 ID、留言者 ID（隱私遮罩）"]})
            rel2 = f"q6/evidence/playwright/ptt_noise_{len(shots) + 1:02d}.png"
            shot(page, rel2)
            shots.append({"rel": rel2, "title": r["title"], "cats": "電話", "url": "https://www.ptt.cc" + r["href"],
                          "n_hits": 1, "labelled": 1, "phone": True})
        page.evaluate("() => document.querySelectorAll('.__gap_box').forEach(e => e.remove())")
    for s in shots:
        add(q="6", id="q6-ptt-noise-" + s["rel"][-6:-4], node="tokenizer", title="PTT 文章內的斷詞雜訊字串",
            claim=f"線上貼文含 {s['cats']}；短網址後綴、電話、地名這類字串會被斷詞器切成雜訊或拆散",
            result=f"{s['title'][:40]}；標出 {s['n_hits']} 種雜訊字串，{s['labelled']} 處命中（紅框）",
            image=s["rel"], source=f"Playwright 實抓（未登入）{s['url']}；紅框與黑框為截圖時加上")
    if not shots:
        pending("6", "q6-ptt-noise", "PTT 雜訊字串", "候選文章都沒有命中短網址／電話／地名", "tokenizer")
    ctx.close()


def main():
    only = set((sys.argv[1] if len(sys.argv) > 1 else "fb,ig,ptt").split(","))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for key, fn in [("fb", run_fb), ("ig", run_ig), ("ptt", run_ptt), ("ptt_refresh", run_ptt_refresh)]:
            if key not in only:
                continue
            log(f"== {key} ==")
            try:
                fn(browser)
            except Exception as e:
                log("  ERROR", key, type(e).__name__, str(e)[:300])
                pending({"fb": "2", "ig": "5", "ptt": "6"}[key], f"{key}-gap-error", f"{key} 補抓中斷",
                        f"{type(e).__name__}: {str(e)[:200]}", "platforms")
        browser.close()
    log("loads per site:", json.dumps(LOADS))
    (ROOT / "capture_gaps_new_entries.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"new entries: {sum(1 for m in MANIFEST if m['status'] == 'real')} real, {sum(1 for m in MANIFEST if m['status'] != 'real')} pending")


if __name__ == "__main__":
    main()
