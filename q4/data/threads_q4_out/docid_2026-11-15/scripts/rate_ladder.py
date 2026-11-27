# 單一 IP 打 GraphQL：由慢到快逐級加速，找出第一個被封的速率；封了之後每隔一段時間探 1 次，量解封時間。
# 用法：python rate_ladder.py <帳號> <html檔> <pv.json> <輸出jsonl>
# token 只在記憶體裡用，不印、不存；只記時間、狀態碼、位元組數。
import sys, re, json, time, threading
from pathlib import Path
from urllib.parse import parse_qs
from concurrent.futures import ThreadPoolExecutor
import requests
from playwright.sync_api import sync_playwright

acct, html_path, pv_path, out = sys.argv[1], Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
pv = json.load(open(pv_path))
DOC = "29427891256864466"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
NEED = ["x-csrftoken", "x-fb-lsd", "x-ig-app-id", "x-logged-out-threads-migrated-request"]
html = html_path.read_text(encoding="utf-8")
uid = (re.search(r'"user_id":"(\d+)"', html) or re.search(r'"userID":"(\d+)"', html)).group(1)
T0 = time.time()
lock = threading.Lock()


def log(**kw):
    kw["t"] = round(time.time() - T0, 2); kw["wall"] = time.strftime("%H:%M:%S")
    with lock, out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def fresh_token():
    tok = {}
    def on_req(req):
        if "/graphql" in req.url and req.method == "POST" and not tok:
            h = req.headers
            if all(k in h for k in NEED):
                tok.update({k: h[k] for k in NEED})
                tok["lsd_body"] = (parse_qs(req.post_data or "").get("lsd") or [""])[0]
    html_status = None
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, is_mobile=True,
                            has_touch=True, device_scale_factor=2)
        pg = ctx.new_page(); pg.on("request", on_req)
        resp = pg.goto(f"https://www.threads.com/@{acct}", wait_until="domcontentloaded", timeout=60000)
        html_status = resp.status if resp else None
        pg.wait_for_timeout(6000)
        posts = pg.evaluate("document.querySelectorAll('a[href*=\"/post/\"]').length")
        b.close()
    return tok, html_status, posts


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
    t = time.time()
    try:
        r = requests.post("https://www.threads.com/graphql/query", headers=H, data=body, timeout=60)
        return r.status_code, len(r.content), round(time.time() - t, 2)
    except Exception as e:
        return "ERR", str(e)[:60], round(time.time() - t, 2)


def stage(rate, n, H, body):
    """依固定間隔發 n 次（每次在獨立執行緒，不等上一次回來），連續 8 次 401 就視為被封，停止再發。"""
    res = []
    blocked = threading.Event()
    def job(i):
        if blocked.is_set(): return
        s, size, dt = one(H, body)
        with lock: res.append((i, s, size, dt, round(time.time() - T0, 2)))
        tail = [x[1] for x in sorted(res)[-8:]]
        if len(tail) == 8 and all(x == 401 for x in tail): blocked.set()
    start = time.time()
    with ThreadPoolExecutor(max_workers=16) as ex:
        for i in range(n):
            if blocked.is_set(): break
            wait = start + i / rate - time.time()
            if wait > 0: time.sleep(wait)
            ex.submit(job, i)
    res.sort()
    ok = sum(1 for x in res if x[1] == 200); bad = [x for x in res if x[1] != 200]
    first_bad = bad[0][0] + 1 if bad else None   # 第幾次請求開始出現非 200
    log(ev="stage", rate=rate, sent=len(res), ok=ok, non200=len(bad), first_non200_at_request=first_bad,
        statuses={str(k): sum(1 for x in res if x[1] == k) for k in sorted({x[1] for x in res}, key=str)},
        bytes_401=sorted({x[2] for x in res if x[1] == 401}), secs=round(time.time() - start, 1), blocked=blocked.is_set())
    return blocked.is_set()


# ---- 階段 0：確認目前沒被封 ----
tok, hs, posts = fresh_token()
H, body = make(tok)
s0 = one(H, body)
log(ev="baseline", html_status=hs, posts_on_page=posts, graphql=s0)
if s0[0] != 200:
    log(ev="abort", reason="開始前就不是 200，IP 可能還在封鎖中"); sys.exit(1)

# ---- 階段 1：由慢到快 ----
LADDER = [(1, 150), (2, 200), (3, 240), (4, 240), (5, 240)]
blocked_rate = None
for rate, n in LADDER:
    time.sleep(10)
    tok, hs, posts = fresh_token(); H, body = make(tok)
    log(ev="stage_start", rate=rate, planned=n, html_status=hs, posts_on_page=posts)
    if stage(rate, n, H, body):
        blocked_rate = rate; break
if blocked_rate is None:
    log(ev="done", result="全部速率都沒被封"); sys.exit(0)
blocked_at = time.time()
log(ev="blocked", rate=blocked_rate)

# ---- 階段 2：量解封時間（每次 1 個請求）----
last_tok = time.time(); cap = 100 * 60
while time.time() - blocked_at < cap:
    gap = 20 if time.time() - blocked_at < 600 else 60
    time.sleep(gap)
    if time.time() - last_tok > 300:
        tok, hs, posts = fresh_token(); H, body = make(tok); last_tok = time.time()
        log(ev="html_check", html_status=hs, posts_on_page=posts)
    s = one(H, body)
    log(ev="poll", since_block_s=round(time.time() - blocked_at), graphql=s)
    if s[0] == 200:
        # 再確認 3 次（1 req/s）
        conf = []
        for _ in range(3):
            time.sleep(1); conf.append(one(H, body)[0])
        log(ev="unblocked", since_block_s=round(time.time() - blocked_at), confirm=conf)
        break
else:
    log(ev="still_blocked", since_block_s=round(time.time() - blocked_at))
