# 證據截圖：Playwright 開未登入手機版 threads.com/@<帳號>，另一個 Chromium 跑 DevTools(inspector.html) 連到它的 Network。
# 頁面自己載入時會自己發 graphql/query；DevTools 直接顯示它的狀態碼。不遮蔽任何東西。
# 用法：python evidence.py <帳號> <label> <輸出資料夾>
import sys, json, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

acct, label, out = sys.argv[1], sys.argv[2], Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
PORT = 9333
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
with sync_playwright() as p:
    tb = p.chromium.launch(channel="chromium", headless=True, args=[f"--remote-debugging-port={PORT}", "--remote-allow-origins=*"])
    tctx = tb.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                          device_scale_factor=2, locale="zh-TW")
    tp = tctx.new_page(); tp.goto("about:blank")
    pg = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")) if t["type"] == "page"][0]
    db = p.chromium.launch(channel="chromium", headless=True)
    dp = db.new_page(viewport={"width": 1140, "height": 1000}, locale="en-US")
    dp.goto(f"http://127.0.0.1:{PORT}/devtools/inspector.html?ws=127.0.0.1:{PORT}/devtools/page/{pg['id']}&panel=network")
    dp.wait_for_timeout(4000)
    for sel in ['[aria-label="Toggle screencast"]', '[title="Toggle screencast"]']:
        loc = dp.locator(sel)
        if loc.count(): loc.first.click(); dp.wait_for_timeout(800); break
    for sel in ['input[aria-label="Filter"]', 'input[placeholder="Filter"]', '[role="searchbox"]', '[contenteditable="plaintext-only"]']:
        flt = dp.locator(sel); done = False
        for i in range(flt.count()):
            if flt.nth(i).is_visible():
                flt.nth(i).click(); dp.keyboard.type(f"/@{acct}|graphql\\/query/"); dp.wait_for_timeout(500); done = True; break
        if done: break
    tp.goto(f"https://www.threads.com/@{acct}", wait_until="domcontentloaded", timeout=60000)
    for _ in range(4):
        try: tp.wait_for_load_state("load"); tp.wait_for_timeout(7000); tp.evaluate("1"); break
        except Exception as e: print("retry", str(e)[:60])
    try:
        tp.keyboard.press("Escape")
    except Exception: pass
    tp.wait_for_timeout(1500)
    tp.screenshot(path=str(out / f"{label}_page.png"))
    dp.screenshot(path=str(out / f"{label}_dev.png"))
    rows = dp.locator("tr.data-grid-data-grid-node"); meta = []
    for i in range(rows.count()):
        r = rows.nth(i); b = r.bounding_box()
        meta.append({"text": r.inner_text().replace("\n", " | "), "box": b})
    posts = tp.evaluate("document.querySelectorAll('a[href*=\"/post/\"]').length")
    (out / f"{label}_meta.json").write_text(json.dumps({"rows": meta, "posts_links": posts}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(label, "rows", len(meta), "post links", posts)
    for m in meta: print(m["text"][:110].encode("ascii","replace").decode())
    db.close(); tb.close()
