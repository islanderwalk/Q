# -*- coding: utf-8 -*-
"""Q6 Threads 渠道真實樣本（未登入、只讀 HTML 內嵌 JSON）。

做法和 q4/code/threads_q4_measure.py 相同：抓公開個人頁 https://www.threads.com/@<帳號>，
讀 <script type="application/json" data-sjs> 裡同時有 code 與 taken_at 的物件＝一篇貼文。
未登入時個人頁只內嵌最上面幾串（約 5–8 篇），所以再開其中有回覆的貼文頁
https://www.threads.com/@<帳號>/post/<code>，貼文頁內嵌 JSON 會帶本串（self_thread）與部分直接回覆（direct_replies）。

收錄規則：
- 帳號本人的貼文（含本人在串裡的接續回覆）→ sub_type="post"
- 其他使用者對該串的公開回覆（text_post_app_info.is_reply 為真）→ sub_type="comment_thread"，source 記被回覆的帳號
- 貼文頁下方「相關貼文」（relatedPosts，is_reply 為假且不是本人）不收
- 沒有文字的貼文（純圖／影片）不收

只留抽出的欄位（code、帳號、taken_at、caption.text），不存整頁 HTML、不存 cookie／token／cursor。
帶 Sec-Fetch-Mode/Sec-Fetch-Dest 兩個瀏覽器導覽標頭（Q4 實測：缺這兩個會拿到不含貼文的空殼頁）。

用法：python3 collect_threads.py [--used-before N] [--post-pages 13]
"""
import argparse
import json
import re

import common as C

BASE = "https://www.threads.com"
ACCOUNTS = ["cnanews_tw", "pts.news", "ftvnews"]  # 中央社、公視新聞網、民視新聞
NAV_HEADERS = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
SJS_RE = re.compile(r'<script type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>', re.S)


def sjs_blobs(html):
    for blob in SJS_RE.findall(html or ""):
        try:
            yield json.loads(blob)
        except ValueError:
            continue


def walk(objs):
    stack = list(objs)
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            yield o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)


def extract(html):
    """回傳 {code: 貼文欄位}；只留需要的欄位。"""
    out = {}
    for o in walk(sjs_blobs(html)):
        if not ("code" in o and "taken_at" in o):
            continue
        user = o.get("user") if isinstance(o.get("user"), dict) else {}
        cap = o.get("caption") if isinstance(o.get("caption"), dict) else {}
        info = o.get("text_post_app_info") if isinstance(o.get("text_post_app_info"), dict) else {}
        rta = info.get("reply_to_author") if isinstance(info.get("reply_to_author"), dict) else {}
        prev = out.get(o["code"])
        row = {
            "code": o["code"],
            "username": user.get("username"),
            "taken_at": o.get("taken_at"),
            "text": cap.get("text") or "",
            "is_reply": bool(info.get("is_reply")),
            "reply_to": rta.get("username"),
            "direct_reply_count": info.get("direct_reply_count") or 0,
        }
        # 同一篇可能出現多次，留有文字的那份
        if prev is None or (not prev["text"] and row["text"]):
            out[o["code"]] = row
    return out


def to_row(p, account, sub_type, fetched_at):
    return C.record(
        post_id=p["code"], channel="threads", sub_type=sub_type, source=f"@{account}",
        url=f"{BASE}/@{p['username']}/post/{p['code']}", published_at=C.epoch_to_iso(p["taken_at"]),
        fetched_at=fetched_at, text=p["text"], collect_method="inline_json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--used-before", type=int, default=0, help="這次之前已對 www.threads.com 載入的次數（算進 30 次上限）")
    ap.add_argument("--post-pages", type=int, default=13, help="最多開幾個貼文頁補回覆")
    args = ap.parse_args()
    f = C.Fetcher("threads", already_used={"www.threads.com": args.used_before})
    rows, summary, failures = [], [], []
    candidates = []  # (account, post) 之後要開貼文頁的

    for acc in ACCOUNTS:
        url = f"{BASE}/@{acc}"
        status, html, fetched_at = f.get(url, extra_headers=NAV_HEADERS)
        if status != 200 or not html:
            failures.append({"url": url, "status": status})
            continue
        posts = extract(html)
        own = [p for p in posts.values() if p["username"] == acc and p["text"]]
        summary.append({"url": url, "status": status, "posts_in_json": len(posts), "own_with_text": len(own)})
        for p in own:
            rows.append(to_row(p, acc, "post", fetched_at))
        # 有回覆的本人「主貼文」（非接續回覆）留給第二步
        mains = sorted([p for p in own if not p["is_reply"] and p["direct_reply_count"] > 0],
                       key=lambda p: -p["direct_reply_count"])
        candidates.append((acc, mains))

    # 第二步：輪流從各帳號挑回覆最多的貼文開貼文頁
    queue = []
    for i in range(max((len(m) for _, m in candidates), default=0)):
        for acc, mains in candidates:
            if i < len(mains):
                queue.append((acc, mains[i]))
    for acc, p in queue[:args.post_pages]:
        url = f"{BASE}/@{acc}/post/{p['code']}"
        status, html, fetched_at = f.get(url, extra_headers=NAV_HEADERS)
        if status != 200 or not html:
            failures.append({"url": url, "status": status})
            continue
        posts = extract(html)
        n_own = n_rep = 0
        for q in posts.values():
            if not q["text"]:
                continue
            if q["username"] == acc:
                rows.append(to_row(q, acc, "post", fetched_at))
                n_own += 1
            elif q["is_reply"]:
                rows.append(to_row(q, acc, "comment_thread", fetched_at))
                n_rep += 1
        summary.append({"url": url, "status": status, "posts_in_json": len(posts), "own_with_text": n_own,
                        "replies_with_text": n_rep, "direct_reply_count": p["direct_reply_count"]})

    C.write_jsonl("threads", rows)
    (C.META_DIR / "threads_pages.json").write_text(json.dumps(
        {"pages": summary, "failures": failures, "network_loads_this_run": f.network, "cache_hits": f.cache_hits,
         "note": "整體載入次數以 _meta/fetch_log.jsonl＋manual_probes.json 為準"}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("loads:", f.count, "failures:", failures)


if __name__ == "__main__":
    main()
