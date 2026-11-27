# DevTools 證據：Playwright 開 https://api.ipify.org（回傳「伺服器看到的我的公網 IP」），DevTools Network 點該請求 → Response 分頁。不遮蔽。
import sys, json, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
out = Path(sys.argv[1]); label = sys.argv[2]; PORT = 9333
with sync_playwright() as p:
    tb = p.chromium.launch(channel="chromium", headless=True, args=[f"--remote-debugging-port={PORT}", "--remote-allow-origins=*"])
    tp = tb.new_context(viewport={"width": 520, "height": 300}).new_page(); tp.goto("about:blank")
    pg = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")) if t["type"] == "page"][0]
    db = p.chromium.launch(channel="chromium", headless=True)
    dp = db.new_page(viewport={"width": 1140, "height": 600}, locale="en-US")
    dp.goto(f"http://127.0.0.1:{PORT}/devtools/inspector.html?ws=127.0.0.1:{PORT}/devtools/page/{pg['id']}&panel=network")
    dp.wait_for_timeout(4000)
    for sel in ['[aria-label="Toggle screencast"]', '[title="Toggle screencast"]']:
        loc = dp.locator(sel)
        if loc.count(): loc.first.click(); dp.wait_for_timeout(800); break
    tp.goto("https://api.ipify.org/", wait_until="load", timeout=60000); tp.wait_for_timeout(2500)
    ip = tp.inner_text("body").strip()
    rows = dp.locator("tr.data-grid-data-grid-node")
    print("rows", rows.count())
    rows.first.locator("td").first.click(); dp.wait_for_timeout(2000)
    for t in ["Response"]:
        tab = dp.get_by_role("tab", name=t)
        if tab.count(): tab.first.click(); dp.wait_for_timeout(1500)
    tp.screenshot(path=str(out / f"{label}_page.png")); dp.screenshot(path=str(out / f"{label}_dev.png"))
    print("ip", ip)
    db.close(); tb.close()
