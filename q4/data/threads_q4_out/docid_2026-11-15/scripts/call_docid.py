# 用 JS 找到的 doc_id 實打 1 次 GraphQL（另比對 10-29 的舊 doc_id 1 次）。
# token 只在記憶體裡用，不印、不存；只記狀態、大小、串數。
import sys, re, json, gzip
from pathlib import Path
from urllib.parse import parse_qs
import requests
from playwright.sync_api import sync_playwright

acct, html_path, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
pv = json.load(open(sys.argv[4]))
doc_ids = sys.argv[5:]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
NEED = ["x-csrftoken", "x-fb-lsd", "x-ig-app-id", "x-logged-out-threads-migrated-request"]

html = html_path.read_text(encoding="utf-8")
m = re.search(r'"user_id":"(\d+)"', html) or re.search(r'"userID":"(\d+)"', html)
uid = m.group(1)

tok = {}
def on_req(req):
    if "/graphql" in req.url and req.method == "POST" and not tok:
        h = req.headers
        if all(k in h for k in NEED):
            tok.update({k: h[k] for k in NEED})
            tok["lsd_body"] = (parse_qs(req.post_data or "").get("lsd") or [""])[0]

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, is_mobile=True,
                        has_touch=True, device_scale_factor=2)
    pg = ctx.new_page(); pg.on("request", on_req)
    pg.goto(f"https://www.threads.com/@{acct}", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(6000)
    b.close()
print("token headers captured:", sorted(k for k in tok if k != "lsd_body"), "lsd in body:", bool(tok.get("lsd_body")))

H = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Accept": "*/*",
     "Origin": "https://www.threads.com", "Referer": f"https://www.threads.com/@{acct}",
     "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "same-origin"}
H.update({k: tok[k] for k in NEED})
rows = []
for d in doc_ids:
    body = {"lsd": tok.get("lsd_body") or tok["x-fb-lsd"], "doc_id": d,
            "fb_api_req_friendly_name": "BarcelonaProfileThreadsTabRefetchableDirectQuery",
            "fb_api_caller_class": "RelayModern", "server_timestamps": "true",
            "variables": json.dumps({"userID": uid, "after": None, "first": 10, "before": None, "last": None,
                                     "allow_page_info_for_lox_user": False, **pv})}
    r = requests.post("https://www.threads.com/graphql/query", headers=H, data=body, timeout=60, stream=True)
    raw = r.raw.read(decode_content=False); wire = len(raw)
    enc = r.headers.get("content-encoding", "")
    try:
        txt = gzip.decompress(raw).decode() if enc == "gzip" else (raw.decode() if not enc else None)
    except Exception:
        txt = None
    if txt is None:
        r2 = requests.post("https://www.threads.com/graphql/query", headers=H, data=body, timeout=60); txt = r2.text
    threads, codes, err = None, [], None
    try:
        j = json.loads(txt)
        edges = j["data"]["mediaData"]["edges"]
        threads = len(edges)
        for e in edges:
            for it in e["node"]["thread_items"]:
                po = it["post"]; codes.append((po.get("code"), po.get("taken_at"), bool((po.get("caption") or {}).get("text"))))
    except Exception as e:
        err = (txt or "")[:160]
    row = {"doc_id": d, "status": r.status_code, "wire_bytes": wire, "content_encoding": enc,
           "decoded_bytes": len((txt or "").encode()), "threads": threads,
           "posts_with_code_time_text": sum(1 for c in codes if c[0] and c[1] and c[2]),
           "first_code": codes[0][0] if codes else None, "error_head": err}
    rows.append(row); print(json.dumps(row, ensure_ascii=False))
(out / f"call_{acct}.json").write_text(json.dumps({"account": acct, "userID": uid, "rows": rows}, indent=1, ensure_ascii=False))
