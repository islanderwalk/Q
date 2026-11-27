# 解封探測：被封後每 GAP 秒只發 1 次請求（極低負載），記錄狀態碼與對外 IP，直到連續 3 次 200 或超過 CAP 分鐘。
# 回復曲線：被封後完全不發請求休息 T 秒，再以固定速率連續發，數「連續 200 幾次後又變 401」。
# N(T) 隨 T 增加的斜率 ≈ 補充速率 ≈ 單 IP 長期可撐的速率。
# 用法：python rate_curve.py <帳號> <html檔> <pv.json> <輸出jsonl> <T秒,逗號分隔> [burst速率]
import sys, re, json, time, threading
from pathlib import Path
from urllib.parse import parse_qs
from concurrent.futures import ThreadPoolExecutor
import requests
from playwright.sync_api import sync_playwright

acct, html_path, pv_path, out = sys.argv[1], Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
GAP = int(sys.argv[5])
CAP = int(sys.argv[6])
pv = json.load(open(pv_path))
DOC = "29427891256864466"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
NEED = ["x-csrftoken", "x-fb-lsd", "x-ig-app-id", "x-logged-out-threads-migrated-request"]
html = html_path.read_text(encoding="utf-8")
uid = (re.search(r'"user_id":"(\d+)"', html) or re.search(r'"userID":"(\d+)"', html)).group(1)
T0 = time.time(); lock = threading.Lock()


def log(**kw):
    kw["t"] = round(time.time() - T0, 1); kw["wall"] = time.strftime("%H:%M:%S")
    with lock, out.open("a", encoding="utf-8") as f: f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def fresh_token():
    tok = {}
    def on_req(req):
        if "/graphql" in req.url and req.method == "POST" and not tok:
            h = req.headers
            if all(k in h for k in NEED):
                tok.update({k: h[k] for k in NEED}); tok["lsd_body"] = (parse_qs(req.post_data or "").get("lsd") or [""])[0]
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
        pg = ctx.new_page(); pg.on("request", on_req)
        pg.goto(f"https://www.threads.com/@{acct}", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(5000); b.close()
    return tok


def make(tok):
    H = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Accept": "*/*",
         "Origin": "https://www.threads.com", "Referer": f"https://www.threads.com/@{acct}",
         "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "same-origin"}
    H.update({k: tok[k] for k in NEED})
    body = {"lsd": tok.get("lsd_body") or tok["x-fb-lsd"], "doc_id": DOC,
            "fb_api_req_friendly_name": "BarcelonaProfileThreadsTabRefetchableDirectQuery",
            "fb_api_caller_class": "RelayModern", "server_timestamps": "true",
            "variables": json.dumps({"userID": uid, "after": None, "first": 10, "before": None, "last": None,
                                     "allow_page_info_for_lox_user": False, **pv})}
    return H, body


def one(H, body):
    try:
        r = requests.post("https://www.threads.com/graphql/query", headers=H, data=body, timeout=60)
        return r.status_code
    except Exception as e:
        return "ERR"


def burst(H, body, rate, max_n=400):
    res = []; blocked = threading.Event()
    def job(i):
        if blocked.is_set(): return
        s = one(H, body)
        with lock: res.append((i, s))
        tail = [x[1] for x in sorted(res)[-6:]]
        if len(tail) == 6 and all(x == 401 for x in tail): blocked.set()
    start = time.time()
    with ThreadPoolExecutor(max_workers=16) as ex:
        for i in range(max_n):
            if blocked.is_set(): break
            w = start + i / rate - time.time()
            if w > 0: time.sleep(w)
            ex.submit(job, i)
    res.sort(); seq = "".join("1" if s == 200 else ("4" if s == 401 else "?") for _, s in res)
    return seq, round(time.time() - start, 1)



def pubip():
    try: return requests.get("https://api.ipify.org", timeout=15).text.strip()
    except Exception as e: return "ERR"

blocked_at_wall = time.time(); tok = None; t_tok = 0; consec = 0
log(ev="start", gap_s=GAP, ip=pubip())
while time.time() - T0 < CAP * 60:
    time.sleep(GAP)
    if tok is None or time.time() - t_tok > 600:
        tok = fresh_token(); H, body = make(tok); t_tok = time.time()
    s = one(H, body)
    log(ev="probe", status=s, ip=pubip())
    consec = consec + 1 if s == 200 else 0
    if consec >= 3: log(ev="recovered"); break
else:
    log(ev="still_blocked")
log(ev="done")
