# -*- coding: utf-8 -*-
"""Threads 歷史爬蟲（未登入、可續跑）。

範圍：公開個人頁 HTML（www.threads.com/@帳號）內嵌的貼文，加上其中回覆最多的貼文頁（回覆串）。
做不到的部分（個人頁 HTML 只內嵌最近幾篇）照實量化在 progress.json 的 coverage 欄位。

規則：
- 只抓未登入的公開 HTML；不登入、不帶 cookie 進檔案、不存整頁 HTML、不存 token/cursor。
- 同一主機兩次請求間隔 >= 5 秒（加 0–2 秒抖動）。429/503 指數退避：60 秒起、上限 30 分鐘；連續 3 次暫停該站並寫進 progress.json。
- 帳號名以 sha256 前 12 碼存 author_hash（只對非官方帳號的留言作者）。
- 輸出：<OUT>/raw/threads/<yyyy-mm>.jsonl，欄位同 stack 原文湖。
- 續跑：讀取既有 jsonl 的 post_id 去重；progress.json 記錄已完成的 (帳號, 貼文頁) 組合。

用法：python3 crawl_threads.py <OUT_DIR> [max_requests]
"""
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TW = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
NAV = {"User-Agent": UA,
       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
       "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
BASE = "https://www.threads.com"
# 已確認的公開新聞帳號（來源：q6 stack）。高雄相關帳號尚未指定，待補。
ACCOUNTS = ["pts.news", "cnanews_tw", "ftvnews"]
START = datetime(2023, 1, 1, tzinfo=TW)
TOP_POST_PAGES = 6          # 每帳號最多開幾個貼文頁抓回覆
SJS_RE = re.compile(r'<script type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>', re.S)
FIELDS = ("post_id", "channel", "sub_type", "source", "url", "published_at",
          "fetched_at", "text", "collect_method", "parent_id", "author_hash", "time_basis")


class Polite:
    def __init__(self, out):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.meta = self.out / "meta"
        self.meta.mkdir(exist_ok=True)
        self.prog_path = self.out / "progress.json"
        self.log = (self.out / "crawl.log").open("a", encoding="utf-8")
        self.last = 0.0
        self.consec_block = 0
        self.paused = False
        self.s = requests.Session()
        self.s.headers.update(NAV)
        self.counts = {"requests": 0, "ok": 0, "blocked": 0, "errors": 0}
        self.prog = self._load_prog()

    def _load_prog(self):
        if self.prog_path.exists():
            return json.loads(self.prog_path.read_text(encoding="utf-8"))
        return {"started_at": now(), "done_post_pages": [], "accounts": {}, "errors": [],
                "paused_sites": [], "last_request_at": None}

    def save(self, extra=None):
        self.prog.update({"updated_at": now(), "counts": self.counts})
        if extra:
            self.prog.update(extra)
        tmp = self.prog_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.prog, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.prog_path)

    def get(self, url, extra=None):
        """回傳 (status, text)。429/503 退避重試；連續 3 次封鎖即暫停（回 (None, None)）。"""
        backoff = 60
        for attempt in range(6):
            if self.paused:
                return None, None
            gap = 5.0 + random.uniform(0, 2) - (time.time() - self.last)
            if gap > 0:
                time.sleep(gap)
            self.counts["requests"] += 1
            try:
                r = self.s.get(url, headers=extra or {}, timeout=40)
                self.last = time.time()
                self.s.cookies.clear()  # 不留 cookie
                self.prog["last_request_at"] = now()
            except requests.RequestException as e:
                self.last = time.time()
                self.counts["errors"] += 1
                self.prog["errors"].append({"t": now(), "url": url, "err": type(e).__name__})
                self.log_line(f"ERR {type(e).__name__} {url}")
                return None, None
            if r.status_code in (429, 503):
                self.counts["blocked"] += 1
                self.consec_block += 1
                self.log_line(f"BLOCK {r.status_code} {url} wait {backoff}s (consec {self.consec_block})")
                if self.consec_block >= 3:
                    self.paused = True
                    self.prog["paused_sites"].append({"t": now(), "host": "www.threads.com", "reason": f"{self.consec_block} consecutive {r.status_code}"})
                    self.save()
                    return None, None
                time.sleep(backoff)
                backoff = min(backoff * 2, 1800)
                continue
            self.consec_block = 0
            self.counts["ok"] += 1
            self.log_line(f"{r.status_code} {len(r.content)}B {url}")
            return r.status_code, r.text
        return None, None

    def log_line(self, msg):
        self.log.write(f"{now()} {msg}\n")
        self.log.flush()


def now():
    return datetime.now(TW).isoformat(timespec="seconds")


def walk(o):
    st = [o]
    while st:
        x = st.pop()
        if isinstance(x, dict):
            yield x
            st.extend(x.values())
        elif isinstance(x, list):
            st.extend(x)


def extract(html):
    out = {}
    for b in SJS_RE.findall(html or ""):
        try:
            d = json.loads(b)
        except ValueError:
            continue
        for o in walk(d):
            if "code" in o and isinstance(o.get("taken_at"), int):
                user = o.get("user") if isinstance(o.get("user"), dict) else {}
                cap = o.get("caption") if isinstance(o.get("caption"), dict) else {}
                info = o.get("text_post_app_info") if isinstance(o.get("text_post_app_info"), dict) else {}
                row = {"code": o["code"], "username": user.get("username"), "taken_at": o["taken_at"],
                       "text": cap.get("text") or "", "is_reply": bool(info.get("is_reply")),
                       "direct_reply_count": info.get("direct_reply_count") or 0}
                prev = out.get(o["code"])
                if prev is None or (not prev["text"] and row["text"]):
                    out[o["code"]] = row
    return out


def ahash(name):
    return hashlib.sha256((name or "").encode("utf-8")).hexdigest()[:12]


def rec(p, sub_type, source, parent_id, fetched_at, hash_author):
    dt = datetime.fromtimestamp(p["taken_at"], TW)
    return {"post_id": p["code"], "channel": "threads", "sub_type": sub_type, "source": source,
            "url": f"{BASE}/@{p['username']}/post/{p['code']}", "published_at": dt.isoformat(timespec="seconds"),
            "fetched_at": fetched_at, "text": p["text"], "collect_method": "inline_json",
            "parent_id": parent_id, "author_hash": ahash(p["username"]) if hash_author else None,
            "time_basis": "real"}


def load_existing(out):
    seen = set()
    for f in (Path(out) / "raw" / "threads").glob("*.jsonl"):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                seen.add(json.loads(ln)["post_id"])
    return seen


def main():
    out = sys.argv[1]
    max_req = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    P = Polite(out)
    seen = load_existing(out)
    raw = Path(out) / "raw" / "threads"
    raw.mkdir(parents=True, exist_ok=True)
    new_rows = []
    queue = []
    for acc in ACCOUNTS:
        if P.counts["requests"] >= max_req or P.paused:
            break
        t = now()
        status, html = P.get(f"{BASE}/@{acc}")
        if status != 200 or not html:
            P.prog["accounts"][acc] = {"profile_status": status, "t": t}
            continue
        posts = extract(html)
        own = [p for p in posts.values() if p["username"] == acc and p["text"]]
        oldest = min((p["taken_at"] for p in posts.values()), default=None)
        P.prog["accounts"][acc] = {
            "profile_status": status, "posts_in_html": len(posts), "own_with_text": len(own),
            "oldest_in_html": datetime.fromtimestamp(oldest, TW).isoformat(timespec="seconds") if oldest else None,
            "t": t}
        for p in own:
            if p["code"] not in seen and START.timestamp() <= p["taken_at"]:
                new_rows.append(rec(p, "post", f"@{acc}", None, now(), False))
                seen.add(p["code"])
        mains = sorted([p for p in own if not p["is_reply"] and p["direct_reply_count"] > 0],
                       key=lambda p: -p["direct_reply_count"])[:TOP_POST_PAGES]
        for m in mains:
            if P.counts["requests"] >= max_req or P.paused:
                break
            key = f"{acc}/{m['code']}"
            if key in P.prog["done_post_pages"]:
                continue
            status, html = P.get(f"{BASE}/@{acc}/post/{m['code']}")
            if status != 200 or not html:
                continue
            pp = extract(html)
            n = 0
            for q in pp.values():
                if not q["text"] or q["code"] in seen:
                    continue
                if q["username"] == acc:
                    sub = "post"
                elif q["is_reply"]:
                    sub = "comment"
                else:
                    continue
                if sub == "post" and q["taken_at"] < START.timestamp():
                    continue
                new_rows.append(rec(q, sub, f"@{acc}", m["code"] if sub == "comment" else None, now(),
                                    hash_author=(sub == "comment")))
                seen.add(q["code"])
                n += 1
            P.prog["done_post_pages"].append(key)
            P.log_line(f"postpage {key} new {n}")
            P.save()
    # 寫入：依月份分檔，append
    by_month = {}
    for r in new_rows:
        by_month.setdefault(r["published_at"][:7], []).append(r)
    for ym, rows in by_month.items():
        with (raw / f"{ym}.jsonl").open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({k: r[k] for k in FIELDS}, ensure_ascii=False) + "\n")
    months = sorted(p.stem for p in raw.glob("*.jsonl"))
    total = sum(1 for f in raw.glob("*.jsonl") for _ in f.open(encoding="utf-8"))
    last = max((json.loads(l)["published_at"] for f in raw.glob("*.jsonl") for l in f.read_text(encoding="utf-8").splitlines() if l.strip()), default=None)
    P.save({"months_present": months, "rows_total": total, "last_published_at": last,
            "new_rows_this_run": len(new_rows), "accounts_list": ACCOUNTS,
            "coverage": "未登入個人頁 HTML 只內嵌最近幾篇（見 accounts[*].oldest_in_html）；"
                        "分頁 XHR 未能還原（見 coverage_notes）",
            "coverage_notes": ["doc_id 無法從個人頁 HTML 取得，需從 510 個 JS 套件中搜尋，未完成"]})
    P.log.close()
    print(json.dumps({"new": len(new_rows), "total": total, "requests": P.counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
