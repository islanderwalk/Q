#!/usr/bin/env python3
"""QSearch 第 4 題：Threads 公開 profile 的實測量測（純量測，無規避工具）。

對每個公開帳號各抓一次，兩種抓法做對照：
  (1) HTTP client：requests 帶一組正常瀏覽器 header，抓 profile HTML 一次。
      加 --sec-fetch 會多帶 Sec-Fetch-Mode/Sec-Fetch-Dest 兩個導覽標頭（驗證 SHELL 的成因）。
  (2) 真實瀏覽器：Playwright 無頭 Chromium 開同一頁，記錄整頁所有 request，
      以及網頁自己送出的 /graphql 請求（只記 friendly name、doc_id、狀態、大小、串數）。

每種抓法都量：狀態碼、傳輸位元組（壓縮後）與解壓後大小、頁面內嵌 JSON 裡找到的
貼文（code→URL、內文、發文時間）、貼文數、最上層串數、分頁資訊（has_next_page、cursor 長度），
以及 HTML 預先排好的 query（queryName、queryID、first）；瀏覽器另量渲染後 DOM 裡的貼文連結數、
往下捲會不會載入更多。
貼文數含串內回覆，也含嵌在別篇 share_info 裡被引用（或被轉發）的貼文，後者另記 quoted_posts；
帳號自己的串與回覆是 posts − quoted_posts。

這支程式只做量測與估算，不含任何代理輪替、黑名單規避或指紋偽造。
不登入、不存 cookie/token/cursor。帳號之間固定等待，規模極小（預設 7 個帳號各一次）。
輸出預設寫到 q4/data/threads_q4_out/run_<UTC 時間>/；已經有 results.json 的目錄不會覆蓋
（threads_q4_out/results.json 是 10-27 的舊紀錄）。
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
import zlib
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

BASE = "https://www.threads.com"

# 一組正常的桌面 Chrome header（和一般使用者開網頁時瀏覽器送的相同）
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# 瀏覽器導覽時一定會帶的兩個標頭；verification §1 說缺這兩個才回 SHELL
SEC_FETCH_HEADERS = {
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
}

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")

DEFAULT_ACCOUNTS = ["zuck", "mosseri", "instagram", "nasa", "natgeo", "nytimes", "nba"]

# 預設輸出的上層目錄。每次在底下開新的 run_<UTC 時間>/，不碰這裡 10-27 的舊 results.json
OUT_ROOT = Path(__file__).resolve().parents[1] / "data" / "threads_q4_out"

# 瀏覽器視窗設定。desktop 是舊版量測用的（Playwright 預設 1280×720、桌面 Chrome 130 UA）；
# desktop-1440 對齊 shared/capture/capture_threads.py（1440×900、不改 UA）；mobile 是手機視窗。
DEVICES = {
    "desktop": {"user_agent": BROWSER_HEADERS["User-Agent"]},
    "desktop-1440": {"viewport": {"width": 1440, "height": 900}, "timezone_id": "Asia/Taipei"},
    "mobile": {"user_agent": MOBILE_UA, "viewport": {"width": 390, "height": 844},
               "is_mobile": True, "has_touch": True, "device_scale_factor": 2},
}

# 渲染後 DOM 裡的貼文：數 /@帳號/post/{code} 連結的不重複 code，另看連結裡有沒有 <time>。
# 不管連結有沒有捲進視窗、有沒有被對話框蓋住都算，所以不等於截圖裡看得到的篇數
RENDERED_JS = r"""(acct) => {
  const all = new Set(), own = new Set(), timed = new Set();
  for (const a of document.querySelectorAll('a[href*="/post/"]')) {
    const m = (a.getAttribute('href') || '').match(/\/@([^/]+)\/post\/([^/?#]+)/);
    if (!m) continue;
    all.add(m[2]);
    if (m[1] === acct) own.add(m[2]);
    if (a.querySelector('time')) timed.add(m[2]);
  }
  const text = document.body ? document.body.innerText : '';
  const se = document.scrollingElement || document.documentElement;
  return {post_links: all.size, own_post_links: own.size, links_with_time: timed.size,
          login_wall: /Log in to see more/i.test(text), scroll_y: Math.round(window.scrollY),
          scroll_height: se.scrollHeight, view_height: window.innerHeight,
          dialogs: [...document.querySelectorAll('[role=dialog]')].map(d => (d.innerText || '').trim().slice(0, 60))};
}"""

# 視窗本身捲不動時（桌面版主欄是內層捲動區），把最大的內層捲動區捲到底；沒有就捲視窗
SCROLL_INNER_JS = r"""() => {
  let best = null;
  for (const el of document.querySelectorAll('div, main, section')) {
    if (!/(auto|scroll)/.test(getComputedStyle(el).overflowY)) continue;
    if (el.scrollHeight <= el.clientHeight + 20) continue;
    if (!best || el.scrollHeight > best.scrollHeight) best = el;
  }
  if (!best) {
    window.scrollTo(0, (document.scrollingElement || document.documentElement).scrollHeight);
    return null;
  }
  const before = Math.round(best.scrollTop);
  best.scrollTop = best.scrollHeight;
  return {before: before, after: Math.round(best.scrollTop), height: best.scrollHeight, client: best.clientHeight};
}"""


def posts_in_json(objs):
    """在一或多段 JSON 裡找貼文：同時有 code 與 taken_at 的物件就是一篇。
    另外回傳最上層串數（mediaData.edges 長度），用來分辨「10 篇」是算篇還是算串。
    被引用（或被轉發）的貼文嵌在另一篇的 text_post_app_info.share_info 底下，也會被算成一篇
    （10/30 natgeo 自己的貼文引用了 @natgeotv 那篇），這種貼文標 quoted=True；
    同一個 code 只要在 share_info 以外出現過一次，就不算 quoted。"""
    posts, threads = {}, 0

    def walk(o, quoted=False):
        nonlocal threads
        if isinstance(o, dict):
            if isinstance(o.get("mediaData"), dict) and "edges" in o["mediaData"]:
                threads = max(threads, len(o["mediaData"]["edges"]))
            if "code" in o and "taken_at" in o:
                cap = o.get("caption")
                user = o.get("user") if isinstance(o.get("user"), dict) else {}
                p = posts.setdefault(o["code"], {
                    "code": o["code"],
                    "url": f"{BASE}/@{user.get('username')}/post/{o['code']}" if user.get("username") else None,
                    "username": user.get("username"),
                    "taken_at": o["taken_at"],
                    "has_text": bool(isinstance(cap, dict) and cap.get("text")),
                    "quoted": quoted,
                })
                p["quoted"] = p["quoted"] and quoted
            for k, v in o.items():
                walk(v, quoted or k == "share_info")
        elif isinstance(o, list):
            for v in o:
                walk(v, quoted)

    for o in objs:
        walk(o)
    return list(posts.values()), threads


def _walk(objs):
    """把一或多段 JSON 攤平成所有 dict，給下面幾個小函式共用。"""
    stack = list(objs)
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            yield o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)


def media_page_info(objs):
    """最上層串列（mediaData）的分頁資訊。cursor 只記長度，值不留。沒有就回 None。"""
    best = None
    for o in _walk(objs):
        md = o.get("mediaData")
        if not (isinstance(md, dict) and isinstance(md.get("edges"), list)):
            continue
        pi = md.get("page_info") if isinstance(md.get("page_info"), dict) else {}
        cur = pi.get("end_cursor")
        row = {"threads": len(md["edges"]), "has_next_page": pi.get("has_next_page"),
               "end_cursor_len": len(cur) if isinstance(cur, str) else 0}
        if best is None or row["threads"] > best["threads"]:
            best = row
    return best


def preloaded_queries(objs):
    """HTML 裡預先排好、由伺服器先跑的 query（expectedPreloaders）：只留 queryName、queryID、
    variables 鍵名與 first。userID 等值不留，只看鍵名在不在。"""
    out, seen = [], set()
    for o in _walk(objs):
        if not (isinstance(o.get("queryName"), str) and "queryID" in o):
            continue
        key = (o["queryName"], str(o["queryID"]))
        if key in seen:
            continue
        seen.add(key)
        v = o.get("variables") if isinstance(o.get("variables"), dict) else {}
        out.append({"name": key[0], "query_id": key[1],
                    "variables": sorted(k for k in v if not k.startswith("__relay_internal__")),
                    "first": v.get("first")})
    return sorted(out, key=lambda q: q["name"])


def url_shape(url):
    """網址只留路徑與 query 鍵名，值（可能含 token）不留。"""
    u = urlsplit(url)
    keys = sorted(parse_qs(u.query, keep_blank_values=True))
    return u.path + ("?" + "&".join(keys) if keys else "")


def html_summary(html, account):
    """一份 profile HTML 的量測：串數、貼文統計、分頁資訊、預排 query。"""
    blobs = list(sjs_blobs(html))
    posts, threads = posts_in_json(blobs)
    return {
        "threads": threads,
        **post_stats(posts, account),
        "page_info": media_page_info(blobs),
        "preloaded_queries": preloaded_queries(blobs),
        "login_wall_text": "Log in to see more" in html,
    }


def sjs_blobs(html):
    """profile HTML 內嵌的 data-sjs JSON，一段一段讀出來；壞掉的段落略過。"""
    for blob in re.findall(r'<script type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>', html, re.S):
        try:
            yield json.loads(blob)
        except ValueError:
            continue


def extract_posts(html):
    """從 profile HTML 內嵌的 data-sjs JSON 找貼文，回傳 (貼文清單, 最上層串數)。"""
    return posts_in_json(sjs_blobs(html))


def post_stats(posts, account):
    """貼文數、本人貼文數、被引用的貼文數、三個欄位（URL、內文、時間）都抽得到的篇數。
    posts 含被引用的貼文；帳號自己的串與回覆是 posts − quoted_posts。"""
    return {
        "posts": len(posts),
        "own_posts": sum(1 for p in posts if p["username"] == account),
        "quoted_posts": sum(1 for p in posts if p["quoted"]),
        "posts_with_all_3_fields": sum(1 for p in posts if p["url"] and p["has_text"] and p["taken_at"]),
    }


def json_docs(body):
    """GraphQL 回應轉成 JSON：可能有 for (;;); 前綴，也可能一行一段。解不開就回空清單。"""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    text = text.removeprefix("for (;;);").strip()
    try:
        return [json.loads(text)]
    except ValueError:
        out = []
        for line in text.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


def graphql_summary(post_data, status, body, header_name=None):
    """整理一個 /graphql 回應。只留 friendly name、doc_id、狀態、大小、串數、貼文欄位統計，
    variables 只留鍵名與 first、after 是否為空；lsd、csrftoken、cursor 等值一律不留。"""
    form = parse_qs(post_data or "")
    out = {
        "name": (form.get("fb_api_req_friendly_name") or [header_name or ""])[0],
        "doc_id": (form.get("doc_id") or [""])[0],
        "status": status,
        "bytes": len(body),
    }
    try:
        variables = json.loads((form.get("variables") or ["{}"])[0])
    except ValueError:
        variables = {}
    if isinstance(variables, dict) and variables:
        out["variables"] = sorted(k for k in variables if not k.startswith("__relay_internal__"))
        out["first"] = variables.get("first")
        out["after_is_null"] = variables.get("after") is None
    docs = json_docs(body)
    posts, threads = posts_in_json(docs)
    out["threads"] = threads
    out["posts"] = len(posts)
    out["quoted_posts"] = sum(1 for p in posts if p["quoted"])
    out["posts_with_all_3_fields"] = sum(1 for p in posts if p["url"] and p["has_text"] and p["taken_at"])
    pi = media_page_info(docs)
    if pi:
        out["has_next_page"] = pi["has_next_page"]
    return out


def decode_body(raw, encoding):
    """依 Content-Encoding 解壓；只處理 http_fetch 會要求的 gzip、deflate。"""
    enc = (encoding or "identity").lower().strip()
    if enc in ("gzip", "x-gzip"):
        return gzip.decompress(raw)
    if enc == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    if enc == "identity":
        return raw
    raise ValueError(f"unexpected Content-Encoding: {encoding}")


def http_fetch(account, out_dir, sec_fetch=False):
    """用 requests 抓一次，回傳量測結果。"""
    url = f"{BASE}/@{account}"
    # 固定只收 gzip/deflate，才能自己解壓；Chrome 會多收 br、zstd，壓縮後大小會略有不同
    headers = dict(BROWSER_HEADERS, **{"Accept-Encoding": "gzip, deflate"}, **(SEC_FETCH_HEADERS if sec_fetch else {}))
    t0 = time.perf_counter()
    r = requests.get(url, headers=headers, timeout=30, stream=True)
    # 先讀未解壓的原始位元組才量得到壓縮後大小（chunked 回應時 r.raw.tell() 會是 0）
    raw = r.raw.read(decode_content=False)
    dt = time.perf_counter() - t0
    body = decode_body(raw, r.headers.get("Content-Encoding"))
    html = body.decode(r.encoding or "utf-8", "replace")
    tag = "http_secfetch" if sec_fetch else "http"
    (out_dir / f"{tag}_{account}.html").write_text(html, encoding="utf-8")
    summary = html_summary(html, account)
    return {
        "sec_fetch": sec_fetch,
        "status": r.status_code,
        "redirects": len(r.history),              # requests 自動跟隨的轉址次數；有轉址代表實際請求數不只 1
        "content_encoding": r.headers.get("Content-Encoding"),
        "wire_bytes": len(raw),                   # 壓縮後實際下載（不含標頭）
        "html_bytes": len(body),                  # 解壓後
        "seconds": round(dt, 3),
        **summary,
        "verdict": "OK" if summary["posts"] else ("SHELL" if "BarcelonaProfile" in html else f"HTTP {r.status_code}"),
    }


def browser_fetch(account, out_dir, scrolls, device="desktop", executable_path=None):
    """用 Playwright 真實 Chromium 開同一頁，記錄整頁所有 request 與網頁自己送的 GraphQL。"""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    url = f"{BASE}/@{account}"
    reqs, gql, docs, navs = [], [], [], []
    phase = {"now": "load"}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=executable_path or None)
        ctx = browser.new_context(locale="en-US", **DEVICES[device])
        page = ctx.new_page()

        def on_done(req):
            try:
                res = req.response()
                sizes = req.sizes()
                reqs.append({
                    "rtype": req.resource_type,
                    "status": res.status if res else None,
                    "wire": (sizes.get("responseBodySize", 0) or 0) + (sizes.get("responseHeadersSize", 0) or 0),
                    "is_doc": req.resource_type == "document",
                })
            except Exception:
                pass

        def on_response(res):
            req = res.request
            if req.resource_type == "document" and res.frame == page.main_frame:
                # 導覽回應的 body 要當下讀：頁面再導覽一次（手機視窗會）之後就讀不到了
                row = {"status": res.status, "url": url_shape(res.url), "phase": phase["now"]}
                try:
                    row["body"] = res.body()
                    row["wire_bytes"] = req.sizes().get("responseBodySize")   # 壓縮後
                except Exception as e:
                    row["error"] = type(e).__name__
                docs.append(row)
                return
            if req.method != "POST" or "/graphql" not in req.url:
                return
            try:
                row = graphql_summary(req.post_data, res.status, res.body(), req.headers.get("x-fb-friendly-name"))
                row["wire_bytes"] = req.sizes().get("responseBodySize")   # 壓縮後；bytes 是解壓後
            except Exception as e:
                row = {"status": res.status, "error": type(e).__name__}
            row["phase"] = phase["now"]
            gql.append(row)

        page.on("requestfinished", on_done)
        page.on("response", on_response)
        # framenavigated：主框架每換一次網址記一筆。同一份文件用 history API 改網址也會記，302 的中間網址不會記，
        # 所以筆數不是導覽次數（10/30 桌面每次 2 筆、手機 5 筆）；文件看 documents
        page.on("framenavigated", lambda f: navs.append(url_shape(f.url)) if f == page.main_frame else None)
        t0 = time.perf_counter()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(2000)
        # 瀏覽器收到的第一份原始 HTML（還沒被 JS 改寫），在 on_response 當下讀好的
        first_doc = next((d for d in docs if "body" in d), None)
        doc = first_doc["body"].decode("utf-8", "replace") if first_doc else ""
        rendered0 = page.evaluate(RENDERED_JS, account)
        posts0, threads0 = extract_posts(page.content())
        phase["now"] = "scroll"
        vp = page.viewport_size or {"width": 1280, "height": 720}
        page.mouse.move(vp["width"] / 2, vp["height"] / 2)   # 滑鼠移到畫面中間，wheel 才捲得到主欄
        steps = []
        for _ in range(scrolls):
            y0 = page.evaluate("() => window.scrollY")
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(1000)
            step = {"how": "wheel"}
            if page.evaluate("() => window.scrollY") == y0:   # 視窗沒動：改捲內層捲動區
                inner = page.evaluate(SCROLL_INNER_JS)
                step = {"how": "inner", "inner": inner} if inner else {"how": "scrollTo"}
            page.wait_for_timeout(2000)
            r = page.evaluate(RENDERED_JS, account)
            step.update(scroll_y=r["scroll_y"], scroll_height=r["scroll_height"],
                        post_links=r["post_links"], graphql_so_far=len(gql))
            steps.append(step)
        rendered1 = page.evaluate(RENDERED_JS, account)
        html1 = page.content()
        posts1, threads1 = extract_posts(html1)
        dt = time.perf_counter() - t0
        (out_dir / f"browser_{device}_{account}.html").write_text(html1, encoding="utf-8")
        ctx.close()   # 不呼叫 storage_state，cookie 跟著 context 一起丟掉
        browser.close()

    doc_wire = sum(r["wire"] for r in reqs if r["is_doc"])
    total_wire = sum(r["wire"] for r in reqs)
    by_type = Counter(r["rtype"] for r in reqs)
    return {
        "device": device,
        "status": resp.status if resp else None,
        "navigations": navs,
        "documents": [{k: v for k, v in d.items() if k != "body"} for d in docs],
        "document": {
            "wire_bytes": first_doc.get("wire_bytes") if first_doc else None,   # 壓縮後
            "html_bytes": len(first_doc["body"]) if first_doc else 0,           # 解壓後
            **html_summary(doc, account),
        },
        "rendered_after_load": rendered0,
        "rendered_after_scroll": rendered1,
        "scroll_steps": steps,
        "graphql": gql,
        "total_requests": len(reqs),
        "requests_by_type": dict(by_type),
        "total_wire_bytes": total_wire,
        "document_wire_bytes": doc_wire,   # 所有 document request（含回應標頭；手機視窗有 200、302、200 三份）
        "seconds": round(dt, 1),
        # inline_*：page.content() 裡內嵌 JSON 的篇數與串數，不是渲染後的貼文連結。網頁用 GraphQL 載入的下一頁
        # 不會寫回內嵌 JSON（10/30 natgeo 手機 DOM 裡的貼文連結 5 個變 22 個，這裡仍是 5），有沒有載入更多要看
        # rendered_* 與 scroll_loaded_more。10/30 重測的 JSON 和 10-27 的舊 results.json 用舊名
        # posts_before_scroll 等，意思相同。
        "inline_posts_before_scroll": len(posts0),
        "inline_threads_before_scroll": threads0,
        "inline_posts_after_scroll": len(posts1),
        "inline_threads_after_scroll": threads1,
        "scroll_loaded_more": rendered1["post_links"] > rendered0["post_links"],
    }


def fmt_kb(n):
    return f"{(n or 0) / 1024:,.1f} KB"


def main(argv=None):
    ap = argparse.ArgumentParser(description="QSearch 第 4 題：Threads 公開頁實測")
    ap.add_argument("--accounts", help="帳號清單檔（一行一個），預設用內建 7 個知名公開帳號")
    ap.add_argument("--delay", type=float, default=6.0, help="每個帳號之間等幾秒")
    ap.add_argument("--scroll-check", type=int, default=5, help="瀏覽器往下捲幾次，看會不會載入更多")
    ap.add_argument("--mode", choices=["both", "http", "browser"], default="both")
    ap.add_argument("--sec-fetch", action="store_true", help="HTTP 模式多帶 Sec-Fetch-Mode/Sec-Fetch-Dest")
    ap.add_argument("--device", choices=list(DEVICES), default="desktop", help="瀏覽器視窗設定，見 DEVICES")
    ap.add_argument("--chromium", default=os.environ.get("Q4_CHROMIUM"),
                    help="Chromium 執行檔路徑（預設讀環境變數 Q4_CHROMIUM；都沒給就用 Playwright 自己下載的）")
    ap.add_argument("--out", help="輸出目錄；預設在 q4/data/threads_q4_out/ 底下開新的 run_<UTC 時間>/。"
                                  "已經有 results.json 的目錄不會覆蓋")
    args = ap.parse_args(argv)

    accounts = (Path(args.accounts).read_text().split() if args.accounts else DEFAULT_ACCOUNTS)
    out_dir = Path(args.out) if args.out else OUT_ROOT / time.strftime("run_%Y%m%dT%H%M%SZ", time.gmtime())
    if (out_dir / "results.json").exists():
        ap.error(f"{out_dir / 'results.json'} 已經存在，不覆蓋；請用 --out 指定新目錄")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for i, acc in enumerate(accounts):
        row = {"account": acc}
        print(f"[{i + 1}/{len(accounts)}] @{acc}", file=sys.stderr)
        if args.mode in ("both", "http"):
            try:
                row["http"] = http_fetch(acc, out_dir, args.sec_fetch)
                print(f"    HTTP   {row['http']['verdict']:6} "
                      f"posts={row['http']['posts']:2} threads={row['http']['threads']:2} "
                      f"wire={fmt_kb(row['http']['wire_bytes'])} html={fmt_kb(row['http']['html_bytes'])}",
                      file=sys.stderr)
            except Exception as e:
                row["http"] = {"error": f"{type(e).__name__}: {e}"}
                print(f"    HTTP   ERROR {row['http']['error'][:80]}", file=sys.stderr)
            time.sleep(args.delay)
        if args.mode in ("both", "browser"):
            try:
                row["browser"] = browser_fetch(acc, out_dir, args.scroll_check, args.device, args.chromium)
                b = row["browser"]
                names = Counter(g.get("name") or "?" for g in b["graphql"])
                print(f"    BROWSER {b['device']} doc threads={b['document']['threads']} posts={b['document']['posts']} "
                      f"rendered {b['rendered_after_load']['post_links']}→{b['rendered_after_scroll']['post_links']} "
                      f"graphql={dict(names)} reqs={b['total_requests']} total={fmt_kb(b['total_wire_bytes'])}",
                      file=sys.stderr)
            except Exception as e:
                row["browser"] = {"error": f"{type(e).__name__}: {e}"}
                print(f"    BROWSER ERROR {row['browser']['error'][:80]}", file=sys.stderr)
            time.sleep(args.delay)
        rows.append(row)

    (out_dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
