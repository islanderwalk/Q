# -*- coding: utf-8 -*-
"""Q6 六渠道：Facebook、Instagram（含 Reels）公開貼文文字。雲端出口 IP 開 FB／IG 會被導去登入頁，所以在本機（住宅 IP）跑。

用法：python capture_q6_fb_ig.py [輸出資料夾，預設 ./out] [FB 粉專清單，逗號分隔] [IG 帳號清單，逗號分隔；- 表示不跑]
輸出：<out>/q6/channels/facebook.jsonl、instagram.jsonl（欄位同 q6/stack/data/channels/*.jsonl）
      <out>/q6/channels/fb_<page>.png、ig_<user>.png、capture_q6_log.txt
規則（沿用 shared/capture/README.md）：只開未登入公開頁；同站兩次導覽間隔 ≥2 秒；每站載入 ≤30 次；
不存 cookie、token、cursor、整頁 HTML，只存貼文文字與時間；登入視窗照實截下，不點擊；不用代理。
文字從頁面內嵌 JSON（script[type=application/json]）走訪取得：FB 取 message.text，IG 取 caption.text。
IG 未登入的內嵌 JSON 不帶 taken_at：published_at 改由 shortcode 推算（fill_ig_published_at.py 的 shortcode_time），
抄進 data/channels 後再跑 fill_ig_published_at.py，登入爬取的副本有的換成精確時間，逐列來源寫進 _meta。
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from fill_ig_published_at import shortcode_time   # 同一個資料夾

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TW = timezone(timedelta(hours=8))
OUT = (Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent / "out") / "q6" / "channels"
LAST, LOADS, LOG = {}, {"fb": 0, "ig": 0}, []
# 粉專／帳號：和 Threads 樣本同一批媒體（中央社、公視、民視），NASA 是第二輪已驗證可開的對照
FB_PAGES = ["cnanewstaiwan", "PNNPTS", "ftvnews53", "NASA"]
IG_USERS = ["cnanews_tw", "pts.news", "ftvnews", "nasa"]
if len(sys.argv) > 2 and sys.argv[2] not in ("", "-"):
    FB_PAGES = sys.argv[2].split(",")
if len(sys.argv) > 3:
    IG_USERS = [] if sys.argv[3] == "-" else sys.argv[3].split(",")
TAG = sys.argv[4] if len(sys.argv) > 4 else ""   # 第二次跑時加後綴，不覆蓋第一次的輸出


def now_iso():
    return datetime.now(TW).isoformat(timespec="seconds")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def goto(page, site, url, wait_ms):
    d = time.time() - LAST.get(site, 0.0)
    if d < 2.5:
        time.sleep(2.5 - d)
    LAST[site] = time.time()
    LOADS[site] += 1
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(wait_ms)
    return resp


WALK_FB = r"""() => {
  const out = []; const seen = new Set();
  const walk = (n, ctx) => {
    if (Array.isArray(n)) { n.forEach(x => walk(x, ctx)); return; }
    if (!n || typeof n !== 'object') return;
    const c = Object.assign({}, ctx);
    if (typeof n.creation_time === 'number') c.t = n.creation_time;
    if (typeof n.post_id === 'string') c.pid = n.post_id;
    if (typeof n.url === 'string' && /facebook\.com\/.+\/(posts|videos|reel|photos)\//.test(n.url)) c.url = n.url;
    if (typeof n.permalink_url === 'string') c.url = n.permalink_url;
    if (n.message && typeof n.message.text === 'string' && n.message.text.trim().length > 4) {
      const k = n.message.text.slice(0, 80);
      if (!seen.has(k)) { seen.add(k); out.push({text: n.message.text, t: c.t || null, pid: c.pid || null, url: c.url || null}); }
    }
    for (const k in n) walk(n[k], c);
  };
  for (const s of document.querySelectorAll('script[type="application/json"]')) { try { walk(JSON.parse(s.textContent), {}); } catch (e) {} }
  return {posts: out, dialog: !!document.querySelector('[role=dialog]'), login: location.pathname.startsWith('/login'), title: document.title.slice(0, 80)};
}"""

WALK_IG = r"""() => {
  const out = []; const seen = new Set();
  const walk = n => {
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (!n || typeof n !== 'object') return;
    if (typeof n.code === 'string' && n.caption && typeof n.caption.text === 'string' && !seen.has(n.code)) {
      seen.add(n.code);
      out.push({code: n.code, text: n.caption.text, taken_at: n.taken_at || n.caption.created_at || null,
                product_type: n.product_type || null, play_count: typeof n.play_count === 'number' ? n.play_count : null});
    }
    for (const k in n) walk(n[k]);
  };
  for (const s of document.querySelectorAll('script[type="application/json"]')) { try { walk(JSON.parse(s.textContent)); } catch (e) {} }
  return {posts: out, login: location.pathname.startsWith('/accounts/login'), dialog: !!document.querySelector('[role=dialog]'), title: document.title.slice(0, 80)};
}"""


def iso(ts):
    return datetime.fromtimestamp(ts, TW).isoformat(timespec="seconds") if ts else None


def run(browser):
    fb, ig = [], []
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    for slug in FB_PAGES:
        if LOADS["fb"] >= 30:
            break
        resp = goto(page, "fb", f"https://www.facebook.com/{slug}", 6000)
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(2500)
        d = page.evaluate(WALK_FB)
        log(f"fb {slug}: status={resp.status if resp else None} url={page.url.split('?')[0]} posts={len(d['posts'])} dialog={d['dialog']} login={d['login']} title={d['title']}")
        if d["posts"]:
            page.evaluate("() => window.scrollTo(0, 0)")
            page.screenshot(path=str(OUT / f"fb_{slug}.png"))
        for p in d["posts"]:
            fb.append({"post_id": f"fb-{p['pid'] or abs(hash(p['text'])) % 10**12}", "channel": "facebook", "sub_type": "post",
                       "source": slug, "url": p["url"], "published_at": iso(p["t"]), "fetched_at": now_iso(),
                       "text": p["text"], "collect_method": "inline_json"})
    for user in IG_USERS:
        for path, sub in (("", "post"), ("reels/", "reel")):
            if LOADS["ig"] >= 30:
                break
            resp = goto(page, "ig", f"https://www.instagram.com/{user}/{path}", 6000)
            d = page.evaluate(WALK_IG)
            log(f"ig {user}/{path}: status={resp.status if resp else None} url={page.url.split('?')[0]} posts={len(d['posts'])} login={d['login']} dialog={d['dialog']}")
            if d["posts"]:
                page.screenshot(path=str(OUT / f"ig_{user.replace('.', '_')}{'_reels' if path else ''}.png"))
            for p in d["posts"]:
                st = "reel" if (p["product_type"] == "clips" or sub == "reel") else "post"
                ig.append({"post_id": f"ig-{p['code']}", "channel": "instagram", "sub_type": st, "source": f"@{user}",
                           "url": f"https://www.instagram.com/{'reel' if st == 'reel' else 'p'}/{p['code']}/",
                           "published_at": iso(p["taken_at"]) or shortcode_time(p["code"]), "fetched_at": now_iso(), "text": p["text"],
                           "collect_method": "inline_json"})
    ctx.close()
    return fb, ig


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    log("== q6 fb/ig residential", now_iso(), "==")
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            fb, ig = run(b)
        finally:
            b.close()
    for name, rows in (("facebook", fb), ("instagram", ig)):
        uniq = {r["post_id"]: r for r in rows}
        if not uniq:
            continue
        with open(OUT / f"{name}{TAG}.jsonl", "w", encoding="utf-8") as f:
            for r in uniq.values():
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        log(f"{name}: {len(uniq)} posts")
    log("loads:", json.dumps(LOADS))
    (OUT / f"capture_q6_log{TAG}.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
