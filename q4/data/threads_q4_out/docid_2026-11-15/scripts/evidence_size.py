# 證據：DevTools Network 列出 12 次「第 1 頁（after=null, first=10）」query 的 Size 與請求的 Content-Length。不遮蔽。
# 用法：python evidence_size.py <html檔> <pv.json> <size_rerun.json> <輸出資料夾>
import sys, re, json, time, urllib.request
from pathlib import Path
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright

html_p, pv_p, rr_p, out = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]); out.mkdir(parents=True, exist_ok=True)
pv = json.load(open(pv_p)); rr = json.load(open(rr_p, encoding="utf-8"))
import requests
NAMED = [r["label"] for r in rr["rows"] if r.get("group") == "named"]
def getuid(a):
    h = requests.get(f"https://www.threads.com/@{a}", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"}, timeout=60).text
    m = re.search(r'"user_id":"(\d+)"', h) or re.search(r'"userID":"(\d+)"', h)
    return m.group(1) if m else None
uids = [u for u in (getuid(a) for a in NAMED) if u]
print("accounts", NAMED, "uids", len(uids), flush=True)
DOC = "29427891256864466"; PORT = 9333
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
NEED = ["x-csrftoken", "x-fb-lsd", "x-ig-app-id", "x-logged-out-threads-migrated-request"]
tok = {}
def on_req(req):
    if "/graphql" in req.url and req.method == "POST" and not tok and all(k in req.headers for k in NEED):
        tok.update({k: req.headers[k] for k in NEED}); tok["lsd_body"] = (parse_qs(req.post_data or "").get("lsd") or [""])[0]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page(); pg.on("request", on_req)
    pg.goto("https://www.threads.com/@zuck", wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(5000); b.close()
    assert tok, "no token"
    tb = p.chromium.launch(channel="chromium", headless=True, args=[f"--remote-debugging-port={PORT}", "--remote-allow-origins=*"])
    tp = tb.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page(); tp.goto("about:blank")
    tgt = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")) if t["type"] == "page"][0]
    db = p.chromium.launch(channel="chromium", headless=True)
    dp = db.new_page(viewport={"width": 1140, "height": 820}, locale="en-US")
    dp.goto(f"http://127.0.0.1:{PORT}/devtools/inspector.html?ws=127.0.0.1:{PORT}/devtools/page/{tgt['id']}&panel=network")
    dp.wait_for_timeout(4000)
    for sel in ['[aria-label="Toggle screencast"]', '[title="Toggle screencast"]']:
        loc = dp.locator(sel)
        if loc.count(): loc.first.click(); dp.wait_for_timeout(800); break
    for sel in ['input[aria-label="Filter"]', 'input[placeholder="Filter"]', '[role="searchbox"]', '[contenteditable="plaintext-only"]']:
        flt = dp.locator(sel); done = False
        for i in range(flt.count()):
            if flt.nth(i).is_visible(): flt.nth(i).click(); dp.keyboard.type("graphql/query"); dp.wait_for_timeout(500); done = True; break
        if done: break
    tp.goto("https://www.threads.com/robots.txt", wait_until="load", timeout=60000)
    H = {"Content-Type": "application/x-www-form-urlencoded", "x-csrftoken": tok["x-csrftoken"], "x-fb-lsd": tok["x-fb-lsd"],
         "x-ig-app-id": tok["x-ig-app-id"], "x-logged-out-threads-migrated-request": tok["x-logged-out-threads-migrated-request"]}
    stat = []
    for uid in uids:
        body = {"lsd": tok.get("lsd_body") or tok["x-fb-lsd"], "doc_id": DOC, "fb_api_req_friendly_name": "BarcelonaProfileThreadsTabRefetchableDirectQuery",
                "fb_api_caller_class": "RelayModern", "server_timestamps": "true",
                "variables": json.dumps({"userID": uid, "after": None, "first": 10, "before": None, "last": None, "allow_page_info_for_lox_user": False, **pv}, separators=(",", ":"))}
        s = tp.evaluate("""async ([b, h]) => { const r = await fetch('/graphql/query', {method:'POST', body:new URLSearchParams(b).toString(), headers:h, credentials:'omit'});
                          const t = await r.arrayBuffer(); return r.status; }""", [body, H])
        stat.append(s); print(uid[-4:], s, flush=True); tp.wait_for_timeout(3000)
    dp.wait_for_timeout(1500)
    dp.screenshot(path=str(out / "size_list_dev.png"))
    rows = dp.locator("tr.data-grid-data-grid-node"); meta = []
    for i in range(rows.count()):
        r = rows.nth(i); meta.append({"text": r.inner_text().replace("\n", " | "), "box": r.bounding_box()})
    (out / "size_list_meta.json").write_text(json.dumps({"rows": meta, "statuses": stat}, indent=1, ensure_ascii=False), encoding="utf-8")
    for m in meta: print(m["text"].encode("ascii", "replace").decode()[:120])
    rows.first.locator("td").first.click(); dp.wait_for_timeout(2000)
    tab = dp.get_by_role("tab", name="Headers")
    if tab.count(): tab.first.click(); dp.wait_for_timeout(1000)
    dp.screenshot(path=str(out / "size_hdr_dev.png"))
    db.close(); tb.close()
