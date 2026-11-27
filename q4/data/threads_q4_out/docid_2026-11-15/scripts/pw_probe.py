import sys, re, json, time
from pathlib import Path
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright
out = Path(sys.argv[1]); out.mkdir(exist_ok=True); acct = sys.argv[2]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
NAME = "BarcelonaProfileThreadsTabRefetchableDirectQuery"
js_hits, gql, js_urls = [], [], []
def on_resp(resp):
    u = resp.url
    try:
        if "rsrc.php" in u and u.split("?")[0].endswith(".js"):
            js_urls.append(u)
            body = resp.text()
            if NAME in body:
                m = re.search(r'__d\("' + NAME + r'_threadsRelayOperation",\[\],\(function\([a-z,]+\)\{[a-z]\.exports="(\d+)"', body)
                js_hits.append({"url": u, "relayop_doc_id": m.group(1) if m else None,
                                "mentions": body.count(NAME)})
                (out / f"hit_{len(js_hits)}.js").write_text(body, encoding="utf-8")
        elif "/graphql" in u and resp.request.method == "POST":
            q = parse_qs(resp.request.post_data or "")
            gql.append({"t": round(time.time(), 1), "friendly": (q.get("fb_api_req_friendly_name") or [""])[0],
                        "doc_id": (q.get("doc_id") or [""])[0], "status": resp.status, "bytes": len(resp.body())})
    except Exception as e:
        pass
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    pg = ctx.new_page(); pg.on("response", on_resp)
    pg.goto(f"https://www.threads.com/@{acct}", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(6000)
    loaded_before = len(js_urls); hits_before = len(js_hits)
    req_val = pg.evaluate("""(n)=>{try{return String(require(n+'_threadsRelayOperation'))}catch(e){return 'ERR '+e.message}}""", NAME)
    for i in range(8):
        pg.mouse.wheel(0, 2500); pg.wait_for_timeout(1800)
    req_val2 = pg.evaluate("""(n)=>{try{return String(require(n+'_threadsRelayOperation'))}catch(e){return 'ERR '+e.message}}""", NAME)
    b.close()
res = {"account": acct, "js_loaded_before_scroll": loaded_before, "js_loaded_total": len(js_urls),
       "js_hits_before_scroll": hits_before, "js_hits": js_hits, "require_before_scroll": req_val,
       "require_after_scroll": req_val2, "graphql": gql}
(out / f"probe_{acct}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
print(json.dumps(res, indent=1, ensure_ascii=False))
