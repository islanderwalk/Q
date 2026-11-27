# 2026-11-15 重測 GraphQL 第 1 頁（after=null, first=10）的傳輸量。
# token 只在記憶體裡，不印不存；小帳號不記名稱。每次請求間隔 3 秒。
import sys, re, json, time, gzip
from pathlib import Path
from urllib.parse import parse_qs, urlencode
import requests
from playwright.sync_api import sync_playwright

out = Path(sys.argv[1]); pv = json.load(open(sys.argv[2])); doc_id = sys.argv[3]
N_EXTRA = int(sys.argv[4]) if len(sys.argv) > 4 else 13
DEFAULT = ["zuck", "mosseri", "instagram", "nasa", "natgeo", "nytimes", "nba"]
UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
UA_D = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36")
NEED = ["x-csrftoken", "x-fb-lsd", "x-ig-app-id", "x-logged-out-threads-migrated-request"]
GAP = 3.0

tok = {}
def on_req(req):
    if "/graphql" in req.url and req.method == "POST" and not tok and all(k in req.headers for k in NEED):
        tok.update({k: req.headers[k] for k in NEED})
        tok["lsd_body"] = (parse_qs(req.post_data or "").get("lsd") or [""])[0]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=UA_M, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    pg = ctx.new_page(); pg.on("request", on_req)
    pg.goto("https://www.threads.com/@zuck", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(6000); b.close()
assert tok, "no token"

HH = {"User-Agent": UA_D, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
GH = {"User-Agent": UA_M, "Content-Type": "application/x-www-form-urlencoded", "Accept": "*/*",
      "Origin": "https://www.threads.com", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
      "Sec-Fetch-Site": "same-origin", **{k: tok[k] for k in NEED}}

def gql(uid, ref, flags):
    body = {"lsd": tok["lsd_body"] or tok["x-fb-lsd"], "doc_id": doc_id,
            "fb_api_req_friendly_name": "BarcelonaProfileThreadsTabRefetchableDirectQuery",
            "fb_api_caller_class": "RelayModern", "server_timestamps": "true",
            "variables": json.dumps({"userID": uid, "after": None, "first": 10, "before": None, "last": None,
                                     "allow_page_info_for_lox_user": False, **flags}, separators=(",", ":"))}
    enc_body = urlencode(body)
    r = requests.post("https://www.threads.com/graphql/query", headers={**GH, "Referer": ref}, data=enc_body,
                      timeout=60, stream=True)
    raw = r.raw.read(decode_content=False)
    ce = r.headers.get("content-encoding", "")
    txt = gzip.decompress(raw).decode() if ce == "gzip" else raw.decode("utf-8", "replace") if not ce else None
    if txt is None:  # br / zstd 等：再取一次解壓後內容（只用來數串數）
        txt = requests.post("https://www.threads.com/graphql/query", headers={**GH, "Referer": ref}, data=enc_body, timeout=60).text
    row = {"status": r.status_code, "wire": len(raw), "ce": ce, "decoded": len(txt.encode()), "up_body": len(enc_body.encode()),
           "threads": None, "posts": 0, "complete": 0, "others": []}
    try:
        edges = json.loads(txt)["data"]["mediaData"]["edges"]
        row["threads"] = len(edges)
        for e in edges:
            for it in e["node"]["thread_items"]:
                po = it["post"]; row["posts"] += 1
                if po.get("code") and po.get("taken_at") and (po.get("caption") or {}).get("text"):
                    row["complete"] += 1
                u = po.get("user") or {}
                if u.get("pk") and u.get("username"):
                    row["others"].append((str(u["pk"]), u["username"]))
                for k in ("quoted_post", "reposted_post"):
                    q = ((po.get("text_post_app_info") or {}).get("share_info") or {}).get(k) or {}
                    qu = q.get("user") or {}
                    if qu.get("pk") and qu.get("username"):
                        row["others"].append((str(qu["pk"]), qu["username"]))
    except Exception:
        row["err"] = txt[:120]
    return row

rows = []
for i, acct in enumerate(DEFAULT):
    h = requests.get(f"https://www.threads.com/@{acct}", headers=HH, timeout=30).text
    m = re.search(r'"userID":"(\d+)"', h)
    time.sleep(GAP)
    if not m:
        rows.append({"label": acct, "group": "named", "err": "no userID"}); continue
    r = gql(m.group(1), f"https://www.threads.com/@{acct}", pv)
    r.update(label=acct, group="named", uid=m.group(1)); rows.append(r)
    print(acct, r["status"], r["wire"], r["threads"], r["posts"], r.get("err", ""), flush=True)
    time.sleep(GAP)

seen = {r.get("uid") for r in rows}
cand = []
for r in rows:
    for pk, un in r.get("others", []):
        if pk not in seen and un not in DEFAULT:
            seen.add(pk); cand.append((pk, un))
extra_names = {}
for j, (pk, un) in enumerate(cand[:N_EXTRA]):
    r = gql(pk, f"https://www.threads.com/@{un}", pv)
    lab = f"other_{j+1:02d}"; extra_names[lab] = un
    r.update(label=lab, group="other", uid=None); rows.append(r)
    print(lab, r["status"], r["wire"], r["threads"], r["posts"], r.get("err", ""), flush=True)
    time.sleep(GAP)

# 成因：zuck 同一刻、旗標全部 false 與照 HTML 值各一次
zuid = next(r["uid"] for r in rows if r["label"] == "zuck")
flag_cmp = {}
for name, flags in [("html_values", pv), ("all_false", {k: False for k in pv}), ("no_flags", {})]:
    r = gql(zuid, "https://www.threads.com/@zuck", flags)
    flag_cmp[name] = {k: r[k] for k in ("status", "wire", "decoded", "up_body", "threads", "posts")}
    flag_cmp[name]["err"] = r.get("err")
    print("zuck", name, flag_cmp[name], flush=True)
    time.sleep(GAP)

for r in rows:
    r.pop("others", None); r.pop("uid", None)
(out / "size_rerun.json").write_text(json.dumps({"date": "2026-11-15", "doc_id": doc_id, "gap_s": GAP,
                                                "rows": rows, "zuck_flag_compare": flag_cmp}, indent=1, ensure_ascii=False))
(out / "size_rerun_names_PRIVATE.json").write_text(json.dumps(extra_names, indent=1))
