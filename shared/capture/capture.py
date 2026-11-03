# -*- coding: utf-8 -*-
"""QSearch 筆試 Q2–Q5 公開網頁證據截圖（Playwright）。

只抓未登入看得到的公開頁面；每個網站兩次導覽至少間隔 1.5 秒，總量很小；
不存 cookie／token 的值；不用代理輪替、不偽裝指紋。
Threads／IG／FB 不存整頁 HTML（裡面有 token），只有 PTT 存 HTML 當測試 fixture。
用法：python capture.py [q2,q3,q4,q5]
"""
import json
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TW = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]  # 截圖直接寫到各題 q{N}/evidence/playwright/
OUT = ROOT / "out"
for sub in ("html", "fixtures"):
    (OUT / sub).mkdir(parents=True, exist_ok=True)
for q in (2, 3, 4, 5):
    (REPO / f"q{q}" / "evidence" / "playwright").mkdir(parents=True, exist_ok=True)

MANIFEST, LOG, LAST = [], [], {}


def now_iso():
    return datetime.now(TW).isoformat(timespec="seconds")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def polite(site, gap=1.5):
    d = time.time() - LAST.get(site, 0.0)
    if d < gap:
        time.sleep(gap - d)
    LAST[site] = time.time()


OVERLAY = r"""
(cfg) => {
  const old = document.getElementById('__ev'); if (old) old.remove();
  document.querySelectorAll('.__ev_fixed').forEach(e => e.remove());
  const root = document.createElement('div'); root.id = '__ev';
  root.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none';
  document.body.appendChild(root);
  const FONT = '"Microsoft JhengHei","PingFang TC","Noto Sans TC",system-ui,sans-serif';
  const COLORS = ['#f43f5e', '#f59e0b', '#22c55e', '#3b82f6', '#a855f7', '#06b6d4'];
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
        t.style.cssText = `position:absolute;left:${Math.max(4, r.left + scrollX - 3)}px;top:${top < 2 ? r.bottom + scrollY + 6 : top}px;background:${color};color:#fff;font:600 14px/1.7 ${FONT};padding:0 9px;border-radius:6px;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,.35)`;
        root.appendChild(t);
      }
    });
  });
  if (cfg.card) {
    const c = document.createElement('div'); c.className = '__ev_fixed';
    c.style.cssText = `position:fixed;right:16px;top:16px;max-width:${cfg.card.width || 440}px;background:rgba(17,24,39,.95);color:#f9fafb;font:14px/1.65 ${FONT};padding:12px 16px;border-radius:10px;border:1px solid rgba(255,255,255,.18);z-index:2147483647;box-shadow:0 6px 24px rgba(0,0,0,.4)`;
    const h = document.createElement('div'); h.textContent = cfg.card.title; h.style.cssText = 'font-weight:700;font-size:15px;margin-bottom:4px';
    c.appendChild(h);
    (cfg.card.lines || []).forEach(s => { const p = document.createElement('div'); p.textContent = s; p.style.whiteSpace = 'pre-wrap'; p.style.wordBreak = 'break-all'; c.appendChild(p); });
    document.body.appendChild(c);
  }
  const tag = document.createElement('div'); tag.className = '__ev_fixed';
  tag.textContent = '標註為截圖時加上 · Playwright 實抓（未登入） · ' + cfg.stamp;
  tag.style.cssText = `position:fixed;left:12px;bottom:12px;background:rgba(17,24,39,.9);color:#e5e7eb;font:12px/1.6 ${FONT};padding:3px 10px;border-radius:6px;z-index:2147483647`;
  document.body.appendChild(tag);
  return found;
}
"""


def overlay(page, items, card=None):
    try:
        return page.evaluate(OVERLAY, {"items": items, "card": card, "stamp": now_iso()})
    except Exception as e:
        log("  overlay error:", e)
        return []


def shot(page, q, name, full=False):
    rel = f"q{q}/evidence/playwright/{name}.png"
    page.screenshot(path=str(REPO / rel), full_page=full)
    return rel


def add(**kw):
    kw.setdefault("kind", "screenshot")
    kw.setdefault("status", "real")
    kw.setdefault("captured_at", now_iso())
    MANIFEST.append(kw)
    log("  +", kw["id"], "|", kw.get("result", ""))


def pending(q, id_, title, why, node="platforms"):
    MANIFEST.append({"q": q, "id": id_, "node": node, "kind": "screenshot", "title": title,
                     "claim": "", "status": "pending", "todo": why, "captured_at": now_iso()})
    log("  ! pending", id_, why)


# ----------------------------------------------------------------- PTT（Q3）
AID = re.compile(r"/bbs/Gossiping/M\.(\d+)\.A\.([0-9A-F]+)\.html")

PTT_LIST_JS = r"""() => {
  const rows = []; const c = document.querySelector('div.r-list-container');
  if (!c) return {rows, prev: null};
  for (const d of c.children) {
    if (d.classList.contains('r-list-sep')) break;
    if (!d.classList.contains('r-ent')) continue;
    const a = d.querySelector('div.title a');
    rows.push({href: a ? a.getAttribute('href') : null,
               title: (a ? a.textContent : d.querySelector('div.title').textContent).trim(),
               nrec: (d.querySelector('div.nrec').textContent || '').trim()});
  }
  let prev = null;
  for (const b of document.querySelectorAll('div.btn-group-paging a'))
    if (b.textContent.includes('上頁') && b.getAttribute('href')) prev = b.getAttribute('href');
  return {rows, prev};
}"""

REPORT = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><style>
body{margin:0;background:#f6f7f9;color:#1f2937;font:15px/1.6 "Microsoft JhengHei","Segoe UI",system-ui,sans-serif}
.wrap{padding:28px 32px;width:1100px}
h1{font-size:21px;margin:0 0 4px}
.sub{color:#5b6472;margin:0 0 18px;font-size:13px}
.kpis{display:flex;gap:12px;margin:0 0 18px}
.k{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 16px;min-width:180px}
.k b{display:block;font-size:24px;font-variant-numeric:tabular-nums}
.k.ok b{color:#047857}.k.bad b{color:#b91c1c}
table{border-collapse:separate;border-spacing:0;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;width:100%}
th,td{padding:7px 14px;border-bottom:1px solid #eef0f3;text-align:left;font-size:14px}
tr:last-child td{border-bottom:0}
th{background:#f3f4f6;color:#5b6472;font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:#047857;font-weight:700}.bad{color:#b91c1c;font-weight:700}
.foot{color:#5b6472;font-size:12.5px;margin-top:12px}
</style></head><body><div class="wrap">
<h1>PTT 八卦板：連續 10 頁列表即時驗證</h1>
<p class="sub">__WHEN__｜index__LO__–index__HI__｜Playwright 實抓（未登入），每頁間隔 1.5 秒</p>
<div class="kpis">
<div class="k __C1__"><b>__BAD__ 頁</b>篇數不是 20 的頁</div>
<div class="k __C2__"><b>__INV__ 次</b>時間戳倒退</div>
<div class="k"><b>__N__ 篇</b>有文章 ID 的列</div>
<div class="k"><b>__DEL__ 列</b>已刪除（沒有連結）</div>
</div>
<table><thead><tr><th>頁</th><th>篇數</th><th>已刪除</th><th>每頁 20 篇</th><th>文章時間範圍（UTC+8）</th></tr></thead><tbody>__ROWS__</tbody></table>
<p class="foot">不變量 1：除了最新頁，每個列表頁固定 20 篇。不變量 2：文章 ID 是 M.〈unix 秒〉.A.〈hex〉，依頁碼與列序排列時，時間戳不倒退。</p>
</div></body></html>"""


def nrec_val(t):
    t = (t or "").strip()
    if not t:
        return 0
    if t == "爆":
        return 100
    if t == "XX":
        return -100
    if t.startswith("X") and t[1:].isdigit():
        return -10 * int(t[1:])
    return int(t) if t.isdigit() else 0


def run_ptt(browser):
    site = "ptt"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    polite(site)
    page.goto("https://www.ptt.cc/bbs/Gossiping/index.html", wait_until="domcontentloaded")
    if "over18" in page.url:
        overlay(page, [{"sel": "button[name=yes]", "label": "按「我同意」才進得了八卦板（等於帶 over18=1 cookie）"}])
        rel = shot(page, 3, "ptt_over18_gate")
        add(q="3", id="q3-ptt-over18", node="platforms", title="八卦板要先通過滿 18 歲確認",
            claim="爬蟲要帶 over18=1 cookie，不然拿到的是確認頁，不是文章列表",
            result="沒帶 cookie 時被導到 " + page.url.split("?")[0], image=rel,
            source="Playwright 實抓（未登入）https://www.ptt.cc/bbs/Gossiping/index.html；標註為截圖時加上")
        polite(site)
        page.click("button[name=yes]")
        page.wait_for_load_state("domcontentloaded")
    (OUT / "html" / "ptt_index_latest.html").write_text(page.content(), encoding="utf-8")
    info = page.evaluate(PTT_LIST_JS)
    newest = int(re.search(r"index(\d+)\.html", info["prev"]).group(1)) + 1
    rows_newest = info["rows"]
    overlay(page, [
        {"sel": "div.r-ent", "all": True, "max": 40, "label": f"每列一篇（div.r-ent）：置底線以上 {len(rows_newest)} 篇"},
        {"sel": "div.nrec", "all": True, "max": 40, "label": "推文數（爆＝100、X1＝-10）"},
        {"sel": "div.btn-group-paging a", "text": "上頁", "label": f"上頁 → index{newest - 1}.html（最新頁是 index{newest}）"},
        {"sel": "div.r-list-sep", "label": "置底分隔線：以下是公告，不算文章"},
    ])
    rel = shot(page, 3, "ptt_index_latest", full=True)
    add(q="3", id="q3-ptt-index-latest", node="platforms", title="最新列表頁的結構",
        claim="列表頁結構固定：每列一篇、推文數、上頁頁碼、置底分隔線；爬蟲用頁碼往回翻，不靠日期",
        result=f"最新頁 index{newest}，置底線以上 {len(rows_newest)} 篇", image=rel,
        source="Playwright 實抓（未登入）https://www.ptt.cc/bbs/Gossiping/index.html；標註為截圖時加上")

    pages = []
    for p in range(newest - 1, newest - 11, -1):
        polite(site)
        url = f"https://www.ptt.cc/bbs/Gossiping/index{p}.html"
        page.goto(url, wait_until="domcontentloaded")
        rows = page.evaluate(PTT_LIST_JS)["rows"]
        ts = [int(AID.search(r["href"]).group(1)) for r in rows if r["href"] and AID.search(r["href"])]
        pages.append({"page": p, "rows": len(rows), "deleted": sum(1 for r in rows if not r["href"]), "ts": ts, "entries": rows})
        if p == newest - 5:
            (OUT / "html" / f"ptt_index_{p}.html").write_text(page.content(), encoding="utf-8")
            overlay(page, [{"sel": "div.r-ent", "all": True, "max": 40, "label": f"這一頁 {len(rows)} 列"}],
                    card={"title": f"index{p}.html（往前第 5 頁）",
                          "lines": [f"div.r-ent：{len(rows)} 列", f"其中已刪除：{pages[-1]['deleted']} 列", "完整列表頁固定 20 篇，可以當「沒漏抓」的不變量"]})
            rel = shot(page, 3, "ptt_index_older", full=True)
            add(q="3", id="q3-ptt-index-20", node="parser", title="完整列表頁剛好 20 篇",
                claim="除了最新頁，每個列表頁固定 20 篇，可以當自動檢查的不變量",
                result=f"index{p}：{len(rows)} 篇", image=rel,
                source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")

    bad = [x["page"] for x in pages if x["rows"] != 20]
    seq = [t for x in sorted(pages, key=lambda x: x["page"]) for t in x["ts"]]
    inversions = sum(1 for a, b in zip(seq, seq[1:]) if b < a)
    rows_html = ""
    for x in sorted(pages, key=lambda x: -x["page"]):
        rng = "—"
        if x["ts"]:
            rng = f"{datetime.fromtimestamp(min(x['ts']), TW):%m/%d %H:%M} – {datetime.fromtimestamp(max(x['ts']), TW):%m/%d %H:%M}"
        mark = '<span class="ok">✓</span>' if x["rows"] == 20 else '<span class="bad">✗</span>'
        rows_html += f"<tr><td>index{x['page']}</td><td class='n'>{x['rows']}</td><td class='n'>{x['deleted']}</td><td>{mark}</td><td>{rng}</td></tr>"
    html = (REPORT.replace("__WHEN__", f"{datetime.now(TW):%Y-%m-%d %H:%M} (UTC+8)")
            .replace("__LO__", str(newest - 10)).replace("__HI__", str(newest - 1))
            .replace("__BAD__", str(len(bad))).replace("__INV__", str(inversions))
            .replace("__C1__", "ok" if not bad else "bad").replace("__C2__", "ok" if not inversions else "bad")
            .replace("__N__", f"{len(seq):,}").replace("__DEL__", str(sum(x["deleted"] for x in pages)))
            .replace("__ROWS__", rows_html))
    rp = ctx.new_page()
    rp.set_viewport_size({"width": 1164, "height": 700})
    rp.set_content(html)
    rel = "q3/evidence/playwright/ptt_live_invariants.png"
    rp.screenshot(path=str(REPO / rel), full_page=True)
    rp.close()
    (OUT / "html" / "ptt_live_invariants.json").write_text(json.dumps(
        [{k: v for k, v in x.items() if k != "entries"} for x in pages], ensure_ascii=False), encoding="utf-8")
    add(q="3", id="q3-ptt-live-invariants", node="parser", kind="test", title="連續 10 頁即時驗證不變量",
        claim="抓下來的列表頁每頁 20 篇、文章 ID 的時間戳沿頁碼不倒退，兩條都能自動檢查",
        result=f"10 頁中篇數不是 20 的有 {len(bad)} 頁；時間戳倒退 {inversions} 次（共 {len(seq)} 篇）",
        image=rel, source=f"Playwright 實抓（未登入）index{newest - 10}–index{newest - 1}；結果在本機產生報告頁後截圖")

    cands = sorted(((nrec_val(r["nrec"]), r["href"], r["title"]) for x in pages for r in x["entries"] if r["href"]),
                   key=lambda c: -c[0])
    href = cands[0][1]
    polite(site)
    url = "https://www.ptt.cc" + href
    page.goto(url, wait_until="domcontentloaded")
    (OUT / "html" / "ptt_article.html").write_text(page.content(), encoding="utf-8")
    stats = page.evaluate(r"""() => {
      const t = {}; let n = 0, warn = 0;
      for (const p of document.querySelectorAll('#main-content div.push')) {
        const tag = p.querySelector('span.push-tag'); if (!tag) { warn++; continue; }
        n++; const k = tag.textContent.trim(); t[k] = (t[k] || 0) + 1;
      }
      return {n, t, warn};
    }""")
    t = stats["t"]
    overlay(page, [{"sel": "div.article-metaline", "all": True, "max": 3, "label": "作者、標題、發文時間"}],
            card={"title": "文章頁：內文和留言在同一頁",
                  "lines": [f"留言 {stats['n']} 則（推 {t.get('推', 0)}／噓 {t.get('噓', 0)}／→ {t.get('→', 0)}）",
                            "所以「每天更新留言」＝每天重抓整篇文章"]})
    rel = shot(page, 3, "ptt_article_top")
    add(q="3", id="q3-ptt-article-top", node="parser", title="文章頁：發文資訊與內文",
        claim="文章網址帶發文時間（M.〈unix〉.A.〈hex〉），留言和內文在同一頁，更新留言就是重抓整頁",
        result=f"這篇留言 {stats['n']} 則", image=rel, source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
    page.evaluate(r"""() => {
      const f2 = [...document.querySelectorAll('#main-content span.f2')].find(s => s.textContent.includes('發信站'));
      const el = f2 || document.querySelector('#main-content div.push');
      if (el) { el.scrollIntoView({block: 'start'}); window.scrollBy(0, -140); }
    }""")
    overlay(page, [
        {"sel": "#main-content span.f2", "text": "發信站", "label": "「※ 發信站」以下不算內文"},
        {"sel": "#main-content div.push", "all": True, "max": 14, "label": "留言列：推噓、帳號、內容、IP 與時間"},
    ])
    rel = shot(page, 3, "ptt_article_comments")
    add(q="3", id="q3-ptt-article-comments", node="parser", title="留言列的欄位",
        claim="每則留言都有推噓、帳號、內容、IP 與時間，parser 依這些欄位拆成資料列",
        result=f"推 {t.get('推', 0)}、噓 {t.get('噓', 0)}、→ {t.get('→', 0)}；截斷提示列 {stats['warn']}",
        image=rel, source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
    ctx.close()


# ----------------------------------------------------------------- Threads（Q4、Q5）
def sjs_blobs(html):
    for blob in re.findall(r'<script type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>', html, re.S):
        try:
            yield json.loads(blob)
        except ValueError:
            continue


def edges_count(obj):
    best, stack = 0, [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            md = o.get("mediaData")
            if isinstance(md, dict) and isinstance(md.get("edges"), list):
                best = max(best, len(md["edges"]))
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return best


def find_posts(obj):
    out, stack = [], [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if "code" in o and "taken_at" in o:
                cap = o.get("caption") if isinstance(o.get("caption"), dict) else {}
                usr = o.get("user") if isinstance(o.get("user"), dict) else {}
                out.append({"code": o.get("code"), "username": usr.get("username"),
                            "taken_at": o.get("taken_at"), "text": (cap or {}).get("text")})
            stack.extend(reversed(list(o.values())))
        elif isinstance(o, list):
            stack.extend(reversed(o))
    seen, res = set(), []
    for x in out:
        if x["code"] not in seen:
            seen.add(x["code"])
            res.append(x)
    return res


def run_threads(browser):
    site = "threads"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    nasa_posts = []
    for acc in ("nasa", "zuck"):
        polite(site, 2.0)
        url = f"https://www.threads.com/@{acc}"
        resp = page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        body = resp.text() if resp else ""
        n_threads = max([edges_count(b) for b in sjs_blobs(body)] or [0])
        posts = []
        for b in sjs_blobs(body):
            posts += find_posts(b)
        if acc == "nasa":
            nasa_posts = [p for p in posts if p["username"] == "nasa"] or posts
        overlay(page, [{"sel": "a[href*='/post/'] time", "all": True, "max": 6, "closest": "a",
                        "label": "發文時間（連結＝/@帳號/post/{code}）"}],
                card={"title": f"@{acc} 個人頁（未登入）",
                      "lines": [f"HTTP {resp.status if resp else '?'}；HTML 內嵌 {n_threads} 串、{len(posts)} 篇",
                                "要拿到 10 篇得靠捲動後的 GraphQL 分頁"]})
        rel = shot(page, 4, f"threads_profile_{acc}")
        add(q="4", id=f"q4-threads-profile-{acc}", node="platforms", title=f"@{acc} 個人頁：HTML 只內嵌 {n_threads} 串",
            claim="未登入時個人頁 HTML 只內嵌最上面幾串，要湊滿 10 篇得靠分頁 query",
            result=f"內嵌 {n_threads} 串、{len(posts)} 篇", image=rel,
            source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")

    # 手機視窗捲動：只觀察網頁自己送出的 GraphQL 請求
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                               device_scale_factor=2, locale="en-US", timezone_id="Asia/Taipei",
                               user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
    mp = mctx.new_page()
    seen = []

    def on_resp(r):
        try:
            req = r.request
            if req.method != "POST" or "/graphql/query" not in req.url:
                return
            form = parse_qs(req.post_data or "")
            name = (form.get("fb_api_req_friendly_name") or [""])[0]
            doc = (form.get("doc_id") or [""])[0]
            body = r.body()
            edges, sample = None, []
            try:
                j = json.loads(body)
                edges = edges_count(j)
                sample = find_posts(j)[:2]
            except Exception:
                pass
            seen.append({"name": name, "doc_id": doc, "status": r.status, "bytes": len(body), "edges": edges, "sample": sample})
        except Exception as e:
            log("  resp error:", e)

    mp.on("response", on_resp)
    polite(site, 2.0)
    mp.goto("https://www.threads.com/@nasa", wait_until="domcontentloaded")
    mp.wait_for_timeout(3500)
    for _ in range(5):
        mp.mouse.wheel(0, 2400)
        mp.evaluate("window.scrollBy(0, 2400)")
        mp.wait_for_timeout(2000)
    pag = [s for s in seen if s["edges"]]
    lines = [f"{s['name'] or '(無名稱)'}\ndoc_id {s['doc_id']}｜HTTP {s['status']}｜{s['bytes'] / 1024:.0f} KB（解壓後）｜{s['edges']} 串"
             for s in pag[:3]] or ["捲動後沒有看到帶貼文的 GraphQL 回應"]
    mp.evaluate("window.scrollTo(0, 0)")
    mp.wait_for_timeout(800)
    overlay(mp, [], card={"title": "網頁自己送出的 POST /graphql/query", "lines": lines, "width": 340})
    rel = shot(mp, 4, "threads_mobile_graphql")
    if pag:
        add(q="4", id="q4-threads-graphql-observed", node="crawl", title="捲動時網頁用 GraphQL 分頁",
            claim="手機視窗捲動時，網頁自己送 POST /graphql/query 取下一頁貼文；一次請求就回一批完整貼文",
            result="；".join(f"{s['name']} doc_id {s['doc_id']} 回 {s['edges']} 串" for s in pag[:2]),
            image=rel, source="Playwright 實抓（未登入、手機視窗 390×844）https://www.threads.com/@nasa；只記錄請求名稱、doc_id、狀態、大小，不存任何 token")
        sample = next((s["sample"] for s in pag if s["sample"]), [])
        (OUT / "fixtures" / "threads_graphql_sample.json").write_text(json.dumps({
            "source": "Threads 網頁捲動時自己送出的 GraphQL 回應片段（@nasa），只留前 2 串的 code、帳號、taken_at、內文",
            "captured_at": now_iso(), "friendly_name": pag[0]["name"], "doc_id": pag[0]["doc_id"],
            "items": sample}, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        pending("4", "q4-threads-graphql-observed", "捲動時網頁用 GraphQL 分頁", "這次捲動沒觀察到帶貼文的 GraphQL 回應", node="crawl")
    mctx.close()

    # 貼文頁：Q4 看網址格式，Q5 看轉發數
    if nasa_posts:
        code = nasa_posts[0]["code"]
        url = f"https://www.threads.com/@nasa/post/{code}"
        polite(site, 2.0)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        counts = page.evaluate(r"""() => {
          const want = ['Like', 'Reply', 'Repost', 'Share']; const out = {};
          for (const s of document.querySelectorAll('svg[aria-label]')) {
            const l = s.getAttribute('aria-label'); if (!want.includes(l) || l in out) continue;
            const btn = s.closest('[role=button]') || s.parentElement;
            out[l] = (btn ? btn.textContent : '').trim();
          }
          return out;
        }""")
        overlay(page, [{"sel": "time", "label": "發文時間 taken_at"}],
                card={"title": "貼文頁網址就是 URL 欄位", "lines": [url, "格式：/@帳號/post/{code}，code 來自 GraphQL 回應"]})
        rel = shot(page, 4, "threads_post_url")
        add(q="4", id="q4-threads-post-url", node="parser", title="貼文網址＝/@帳號/post/{code}",
            claim="題目要的 URL 欄位可以直接用回應裡的 code 組出來，不必再抓一次貼文頁",
            result=url, image=rel, source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
        overlay(page, [
            {"sel": "svg[aria-label='Repost']", "closest": "[role=button]", "label": "轉發數（Q5 的 repost_count）"},
            {"sel": "svg[aria-label='Like']", "closest": "[role=button]", "label": "讚"},
            {"sel": "svg[aria-label='Reply']", "closest": "[role=button]", "label": "回覆"},
            {"sel": "svg[aria-label='Share']", "closest": "[role=button]", "label": "分享"},
        ], card={"title": "未登入也看得到互動數",
                 "lines": [f"讚 {counts.get('Like') or '—'}｜回覆 {counts.get('Reply') or '—'}｜轉發 {counts.get('Repost') or '—'}｜分享 {counts.get('Share') or '—'}"]})
        rel = shot(page, 5, "threads_post_repost")
        add(q="5", id="q5-threads-repost-visible", node="platforms", title="Threads 貼文頁看得到轉發數",
            claim="repost_count 是公開頁面就有的欄位，爬蟲端拿得到，Q5 缺的是寫進 ES 這一段",
            result=f"讚 {counts.get('Like') or '—'}、回覆 {counts.get('Reply') or '—'}、轉發 {counts.get('Repost') or '—'}、分享 {counts.get('Share') or '—'}",
            image=rel, source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
    else:
        pending("4", "q4-threads-post-url", "貼文網址＝/@帳號/post/{code}", "個人頁沒解析到貼文 code", node="parser")
        pending("5", "q5-threads-repost-visible", "Threads 貼文頁看得到轉發數", "個人頁沒解析到貼文 code")
    ctx.close()


# ----------------------------------------------------------------- Instagram（Q5）
def run_ig(browser):
    site = "ig"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    polite(site, 2.0)
    url = "https://www.instagram.com/nasa/reels/"
    resp = page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    tiles = page.evaluate(r"""() => [...document.querySelectorAll('a[href*="/reel/"]')].slice(0, 8)
        .map(a => ({href: a.getAttribute('href'), text: a.innerText.replace(/\s+/g, ' ').trim()}))""")
    wall = page.evaluate(r"""() => !!document.querySelector('input[name=username]') || location.pathname.startsWith('/accounts/login')""")
    texts = [t["text"] for t in tiles if t["text"]]
    overlay(page, [{"sel": "a[href*='/reel/']", "all": True, "max": 8, "label": "Reels 縮圖：觀看數印在縮圖上"}],
            card={"title": "@nasa Reels 分頁（未登入）",
                  "lines": [f"HTTP {resp.status if resp else '?'}；看到 {len(tiles)} 支 Reels",
                            ("縮圖上的數字：" + "、".join(texts[:6])) if texts else "縮圖上沒有讀到數字",
                            "被導到登入頁" if wall else "沒有被導到登入頁"]})
    rel = shot(page, 5, "ig_reels_grid")
    if tiles:
        add(q="5", id="q5-ig-reels-grid", node="platforms", title="IG Reels 分頁看得到觀看數",
            claim="video_view_count 在公開的 Reels 分頁就印在縮圖上，爬蟲端拿得到",
            result=("縮圖數字：" + "、".join(texts[:4])) if texts else f"看到 {len(tiles)} 支 Reels，縮圖沒有讀到數字",
            image=rel, source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
        polite(site, 2.0)
        url2 = "https://www.instagram.com" + tiles[0]["href"]
        page.goto(url2, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        views = page.evaluate(r"""() => { const m = document.body.innerText.match(/([\d.,]+\s*[KMB]?)\s+(views|plays)/i); return m ? m[0] : null; }""")
        wall2 = page.evaluate(r"""() => !!document.querySelector('input[name=username]') || location.pathname.startsWith('/accounts/login')""")
        overlay(page, [], card={"title": "單支 Reel 頁（未登入）",
                                "lines": [url2, f"頁面文字裡的觀看數：{views or '沒有找到'}", "被導到登入頁" if wall2 else "沒有被導到登入頁"]})
        rel = shot(page, 5, "ig_reel_page")
        add(q="5", id="q5-ig-reel-page", node="platforms", title="單支 Reel 頁的觀看數",
            claim="確認單支 Reel 頁未登入時能不能看到觀看數",
            result=views or ("被導到登入頁" if wall2 else "頁面沒有顯示觀看數文字"),
            image=rel, source=f"Playwright 實抓（未登入）{url2}；標註為截圖時加上")
    else:
        add(q="5", id="q5-ig-reels-grid", node="platforms", title="IG Reels 分頁（未登入）",
            claim="確認未登入時 Reels 分頁能不能看到觀看數",
            result="被導到登入頁，看不到 Reels" if wall else "沒有載入任何 Reels",
            image=rel, source=f"Playwright 實抓（未登入）{url}")
    ctx.close()


# ----------------------------------------------------------------- Facebook（Q2）
def run_fb(browser):
    site = "fb"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    polite(site, 2.0)
    url = "https://www.facebook.com/NASA"
    resp = page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    info = page.evaluate(r"""() => ({
      h1: (document.querySelector('h1') || {}).innerText || null,
      dialog: !!document.querySelector('[role=dialog]'),
      login: !!document.querySelector('input[name=email]'),
      reactions: [...document.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label')).filter(l => /reaction/i.test(l)).slice(0, 3)
    })""")
    overlay(page, [
        {"sel": "h1", "label": "粉專名稱"},
        {"sel": "[aria-label*='reaction' i]", "all": True, "max": 3, "label": "心情數"},
        {"sel": "[role=dialog]", "label": "未登入時跳出的登入視窗"},
    ], card={"title": "FB 粉專頁（未登入）",
             "lines": [f"HTTP {resp.status if resp else '?'}；粉專名稱：{info['h1'] or '沒有讀到'}",
                       ("心情：" + "；".join(info["reactions"])) if info["reactions"] else "沒有讀到心情數",
                       "有登入視窗" if (info["dialog"] or info["login"]) else "沒有登入視窗",
                       "正式做法走 Graph API（需 Page Public Content Access 審核）"]})
    rel = shot(page, 2, "fb_page_nasa")
    add(q="2", id="q2-fb-page", node="platforms", title="FB 粉專頁的欄位（未登入）",
        claim="Q2 的欄位（粉專名稱、發文時間、心情數）在粉專頁上看得到；量大時走 Graph API，不靠網頁",
        result=f"粉專名稱：{info['h1'] or '—'}；心情數 {'有' if info['reactions'] else '沒讀到'}；{'有' if (info['dialog'] or info['login']) else '沒有'}登入視窗",
        image=rel, source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
    ctx.close()


def main():
    only = set((sys.argv[1] if len(sys.argv) > 1 else "q2,q3,q4,q5").split(","))
    steps = [("q3", run_ptt), ("q4", run_threads), ("q5", run_ig), ("q2", run_fb)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for key, fn in steps:
            if key not in only:
                continue
            log(f"== {key} ==")
            try:
                fn(browser)
            except Exception as e:
                log("  ERROR", key, type(e).__name__, str(e)[:300])
                traceback.print_exc()
                pending(key[1:], f"{key}-error", f"{key} 截圖中斷", f"{type(e).__name__}: {str(e)[:200]}")
        browser.close()
    (OUT / "capture_manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "capture_log.txt").write_text("\n".join(LOG), encoding="utf-8")
    real = sum(1 for m in MANIFEST if m["status"] == "real")
    log(f"done: {real} real, {len(MANIFEST) - real} pending")


if __name__ == "__main__":
    main()
