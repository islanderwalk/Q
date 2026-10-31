# -*- coding: utf-8 -*-
"""Threads（Q4、Q5）第二版截圖。

10/30 實測：手機視窗未登入捲到底會出現「Log in to see more」，不再載入下一頁；
桌面個人頁載入時，網頁自己會送 BarcelonaProfileThreadsTabRefetchableDirectQuery。
所以改成：桌面頁觀察分頁 query、手機頁記錄登入牆；貼文頁的互動數用 svg <title> 找。
只記錄請求名稱、doc_id、狀態、大小與串數，不存任何 token。
"""
import json
from urllib.parse import parse_qs

from playwright.sync_api import sync_playwright

import capture as c

MARK_TEXT = r"""(phrase) => {
  let best = null;
  for (const el of document.querySelectorAll('body *')) {
    if (!el.innerText || !el.innerText.includes(phrase)) continue;
    if ([...el.children].some(ch => ch.innerText && ch.innerText.includes(phrase))) continue;
    best = el; break;
  }
  document.querySelectorAll('[data-ev-target]').forEach(e => e.removeAttribute('data-ev-target'));
  if (best) best.setAttribute('data-ev-target', '1');
  return best ? best.innerText.trim().slice(0, 60) : null;
}"""

ACTIONS = r"""() => {
  const want = ['Like', 'Reply', 'Repost', 'Share']; const out = {};
  for (const s of document.querySelectorAll('svg')) {
    const t = s.querySelector('title'); const l = (t && t.textContent) || s.getAttribute('aria-label');
    if (!want.includes(l) || l in out) continue;
    const btn = s.closest('[role=button]') || (s.parentElement && s.parentElement.parentElement);
    if (btn) btn.setAttribute('data-ev-act', l);
    out[l] = btn ? btn.innerText.trim() : '';
  }
  return out;
}"""

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


def listener(seen):
    def on_resp(r):
        try:
            req = r.request
            if req.method != "POST" or "/graphql" not in req.url:
                return
            form = parse_qs(req.post_data or "")
            name = (form.get("fb_api_req_friendly_name") or [""])[0]
            doc = (form.get("doc_id") or [""])[0]
            body = r.body()
            edges, sample = None, []
            try:
                j = json.loads(body)
                edges = c.edges_count(j)
                sample = c.find_posts(j)[:2]
            except Exception:
                pass
            seen.append({"name": name, "doc_id": doc, "status": r.status, "bytes": len(body), "edges": edges, "sample": sample})
        except Exception as e:
            c.log("  resp error:", e)
    return on_resp


def run(browser):
    site = "threads"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Taipei")
    page = ctx.new_page()
    seen = []
    page.on("response", listener(seen))
    nasa_posts = []
    for acc in ("nasa", "zuck"):
        c.polite(site, 2.0)
        url = f"https://www.threads.com/@{acc}"
        before = len(seen)
        resp = page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        body = resp.text() if resp else ""
        n_threads = max([c.edges_count(b) for b in c.sjs_blobs(body)] or [0])
        posts = []
        for b in c.sjs_blobs(body):
            posts += c.find_posts(b)
        if acc == "nasa":
            nasa_posts = [p for p in posts if p["username"] == "nasa"] or posts
        c.overlay(page, [{"sel": "a[href*='/post/'] time", "all": True, "max": 6, "closest": "a",
                          "label": "發文時間（連結＝/@帳號/post/{code}）"}],
                  card={"title": f"@{acc} 個人頁（未登入）",
                        "lines": [f"HTTP {resp.status if resp else '?'}；HTML 內嵌 {n_threads} 串、{len(posts)} 篇",
                                  "要湊滿 10 篇得靠 GraphQL 分頁 query"]})
        rel = c.shot(page, 4, f"threads_profile_{acc}")
        c.add(q="4", id=f"q4-threads-profile-{acc}", node="platforms", title=f"@{acc} 個人頁：HTML 只內嵌 {n_threads} 串",
              claim="未登入時個人頁 HTML 只內嵌最上面幾串，要湊滿 10 篇得靠分頁 query",
              result=f"內嵌 {n_threads} 串、{len(posts)} 篇", image=rel,
              source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
        if acc == "nasa":
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(2500)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(700)
            mine = seen[before:]
            pag = [s for s in mine if "ProfileThreads" in (s["name"] or "")]
            lines = [f"{s['name']}\ndoc_id {s['doc_id']}｜HTTP {s['status']}｜{s['bytes'] / 1024:.0f} KB（解壓後）｜{s['edges']} 串"
                     for s in pag[:2]] or ["這次沒有看到分頁 query"]
            lines.append("repo 10/29 紀錄的 doc_id 是 39551525044493602：doc_id 會換，不能寫死")
            c.overlay(page, [], card={"title": "網頁自己送出的 POST /graphql/query（桌面、未登入）", "lines": lines, "width": 470})
            rel = c.shot(page, 4, "threads_graphql_desktop")
            if pag:
                c.add(q="4", id="q4-threads-graphql-observed", node="crawl", title="個人頁載入時網頁用 GraphQL 分頁",
                      claim="個人頁載入時，網頁自己送 BarcelonaProfileThreadsTabRefetchableDirectQuery；doc_id 和 10/29 不同，代表要從網頁動態取得，不能寫死",
                      result="；".join(f"doc_id {s['doc_id']}、HTTP {s['status']}、{s['edges']} 串、{s['bytes'] / 1024:.0f} KB" for s in pag[:2]),
                      image=rel, source=f"Playwright 實抓（未登入、桌面 1440×900）{url}；只記錄請求名稱、doc_id、狀態、大小，不存任何 token")
                sample = next((s["sample"] for s in pag if s["sample"]), [])
                (c.OUT / "fixtures" / "threads_graphql_sample.json").write_text(json.dumps({
                    "source": "Threads 個人頁載入時網頁自己送出的 GraphQL 回應片段（@nasa），只留前 2 串的 code、帳號、taken_at、內文",
                    "captured_at": c.now_iso(), "friendly_name": pag[0]["name"], "doc_id": pag[0]["doc_id"],
                    "edges": pag[0]["edges"], "items": sample}, ensure_ascii=False, indent=1), encoding="utf-8")
            else:
                c.pending("4", "q4-threads-graphql-observed", "個人頁載入時網頁用 GraphQL 分頁", "這次沒觀察到分頁 query", node="crawl")

    # 手機視窗：記錄未登入的登入牆
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                               device_scale_factor=2, locale="en-US", timezone_id="Asia/Taipei", user_agent=MOBILE_UA)
    mp = mctx.new_page()
    mseen = []
    mp.on("response", listener(mseen))
    c.polite(site, 2.0)
    murl = "https://www.threads.com/@nasa"
    mp.goto(murl, wait_until="domcontentloaded")
    mp.wait_for_timeout(3500)
    for _ in range(4):
        mp.mouse.wheel(0, 2000)
        mp.wait_for_timeout(1500)
    found = mp.evaluate(MARK_TEXT, "Log in to see more")
    mp.evaluate("() => { const e = document.querySelector('[data-ev-target]'); if (e) e.scrollIntoView({block: 'center'}); }")
    mp.wait_for_timeout(500)
    names = sorted({s["name"] for s in mseen if s["name"]})
    got_posts = sum(1 for s in mseen if s["edges"])
    c.overlay(mp, [{"sel": "[data-ev-target]", "label": "未登入只給看前幾串"}],
              card={"title": "手機視窗（未登入）",
                    "lines": [f"捲動期間的 GraphQL：{', '.join(names) or '無'}", f"帶貼文的回應：{got_posts} 個",
                              "10/28 實測手機視窗捲動還會觸發分頁 query"], "width": 330})
    rel = c.shot(mp, 4, "threads_mobile_wall")
    c.add(q="4", id="q4-threads-mobile-wall", node="crawl", title="手機版未登入：捲動不再載入下一頁",
          claim="Threads 對未登入的限制會變：10/28 手機視窗捲動還會觸發分頁 query，10/30 改成出現登入牆，所以要監控每次回傳的串數",
          result=(f"看到「{found}」" if found else "沒有找到登入提示") + f"；帶貼文的 GraphQL 回應 {got_posts} 個",
          image=rel, source=f"Playwright 實抓（未登入、手機視窗 390×844）{murl}；標註為截圖時加上")
    mctx.close()

    # 貼文頁：Q4 看網址，Q5 看互動數
    if nasa_posts:
        code = nasa_posts[0]["code"]
        url = f"https://www.threads.com/@nasa/post/{code}"
        c.polite(site, 2.0)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        c.overlay(page, [{"sel": "time", "label": "發文時間 taken_at"}],
                  card={"title": "貼文頁網址就是 URL 欄位", "lines": [url, "格式：/@帳號/post/{code}，code 來自回應"]})
        rel = c.shot(page, 4, "threads_post_url")
        c.add(q="4", id="q4-threads-post-url", node="parser", title="貼文網址＝/@帳號/post/{code}",
              claim="題目要的 URL 欄位可以直接用回應裡的 code 組出來，不必再抓一次貼文頁",
              result=url, image=rel, source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
        counts = page.evaluate(ACTIONS)
        views = page.evaluate(MARK_TEXT, " views")
        c.overlay(page, [
            {"sel": "[data-ev-act='Repost']", "label": "轉發數（Q5 的 repost_count）"},
            {"sel": "[data-ev-act='Like']", "label": "讚"},
            {"sel": "[data-ev-act='Reply']", "label": "回覆"},
            {"sel": "[data-ev-act='Share']", "label": "分享"},
            {"sel": "[data-ev-target]", "label": "瀏覽數"},
        ], card={"title": "未登入也看得到互動數",
                 "lines": [f"讚 {counts.get('Like') or '—'}｜回覆 {counts.get('Reply') or '—'}｜轉發 {counts.get('Repost') or '—'}｜分享 {counts.get('Share') or '—'}",
                           f"瀏覽：{views or '—'}"]})
        rel = c.shot(page, 5, "threads_post_repost")
        c.add(q="5", id="q5-threads-repost-visible", node="platforms", title="Threads 貼文頁看得到轉發數",
              claim="repost_count 在公開貼文頁就有，爬蟲端拿得到；Q5 缺的是寫進 ES 這一段",
              result=f"讚 {counts.get('Like') or '—'}、回覆 {counts.get('Reply') or '—'}、轉發 {counts.get('Repost') or '—'}、分享 {counts.get('Share') or '—'}；{views or ''}",
              image=rel, source=f"Playwright 實抓（未登入）{url}；標註為截圖時加上")
    ctx.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        b = p.chromium.launch()
        c.log("== q4 / q5 threads (v2) ==")
        run(b)
        b.close()
    path = c.OUT / "capture_manifest.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    new_ids = {m["id"] for m in c.MANIFEST}
    merged = [m for m in old if not (m["id"].startswith("q4-threads") or m["id"].startswith("q5-threads")) and m["id"] not in new_ids] + c.MANIFEST
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(c.OUT / "capture_log.txt", "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(c.LOG))
    real = sum(1 for m in merged if m["status"] == "real")
    c.log(f"merged manifest: {real} real, {len(merged) - real} pending")
