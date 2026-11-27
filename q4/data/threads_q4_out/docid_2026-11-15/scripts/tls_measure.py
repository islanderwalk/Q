# 量 TLS 額外開銷：每個請求用全新連線（等同輪換 IP 住宅代理每次都重新握手），用 tshark 在 Wi-Fi 網卡抓封包。
# 用法：python tls_measure.py <pv.json> <輸出pcapng>
import sys, re, json, time, subprocess
from urllib.parse import parse_qs
import requests
from playwright.sync_api import sync_playwright
pv = json.load(open(sys.argv[1])); pcap = sys.argv[2]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
NEED = ["x-csrftoken", "x-fb-lsd", "x-ig-app-id", "x-logged-out-threads-migrated-request"]
ACCTS = ["zuck", "nasa", "nytimes"]
def uid(a):
    h = requests.get(f"https://www.threads.com/@{a}", headers={"User-Agent": UA}, timeout=60).text
    return (re.search(r'"user_id":"(\d+)"', h) or re.search(r'"userID":"(\d+)"', h)).group(1)
uids = [uid(a) for a in ACCTS]
tok = {}
def on_req(req):
    if "/graphql" in req.url and req.method == "POST" and not tok and all(k in req.headers for k in NEED):
        tok.update({k: req.headers[k] for k in NEED}); tok["lsd_body"] = (parse_qs(req.post_data or "").get("lsd") or [""])[0]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); pg = b.new_context(user_agent=UA, is_mobile=True, has_touch=True).new_page(); pg.on("request", on_req)
    pg.goto("https://www.threads.com/@zuck", wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(5000); b.close()
cap = subprocess.Popen([r"C:\Program Files\Wireshark\tshark.exe", "-i", "4", "-f", "tcp port 443 and host 31.13.87.52", "-w", pcap], stderr=subprocess.DEVNULL)
time.sleep(4)
for a, u in zip(ACCTS, uids):
    body = {"lsd": tok["lsd_body"] or tok["x-fb-lsd"], "doc_id": "29427891256864466", "fb_api_req_friendly_name": "BarcelonaProfileThreadsTabRefetchableDirectQuery",
            "fb_api_caller_class": "RelayModern", "server_timestamps": "true",
            "variables": json.dumps({"userID": u, "after": None, "first": 10, "before": None, "last": None, "allow_page_info_for_lox_user": False, **pv}, separators=(",", ":"))}
    H = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Origin": "https://www.threads.com", "Referer": f"https://www.threads.com/@{a}",
         "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "same-origin", "Accept-Encoding": "gzip, deflate", **{k: tok[k] for k in NEED}}
    s = requests.Session(); r = s.post("https://www.threads.com/graphql/query", headers=H, data=body, timeout=60, stream=True)
    raw = r.raw.read(decode_content=False); s.close()
    print(a, r.status_code, "resp body wire", len(raw), "req body", len(r.request.body), flush=True); time.sleep(3)
time.sleep(2); cap.terminate(); cap.wait(); print("done")
