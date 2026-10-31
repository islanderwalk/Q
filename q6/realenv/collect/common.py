# -*- coding: utf-8 -*-
"""Q6 真實渠道樣本收集：共用工具。

規則（每支 collect_*.py 都遵守）：
- 只抓未登入看得到的公開頁面／公開 RSS；不登入、不存 cookie／token／cursor。
- 同一個網站兩次載入至少間隔 2 秒（polite），每個網站最多 30 次載入（Budget 會擋）。
- 正常桌面 Chrome UA。
- 文字保持原樣（網址、電話、表情符號、簽名檔都留著），清理交給 Q6 pipeline。
- 每次請求記一行到 data/channels/_meta/fetch_log.jsonl（網址、狀態碼、位元組、時間；不記標頭與 cookie）。
"""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

TW = timezone(timedelta(hours=8))
HERE = Path(__file__).resolve().parent
REALENV = HERE.parent
OUT_DIR = REALENV / "data" / "channels"
OUT_DIR.mkdir(parents=True, exist_ok=True)
META_DIR = OUT_DIR / "_meta"  # 抓取紀錄與統計放子目錄，channels/*.jsonl 只有 4 個渠道檔
META_DIR.mkdir(parents=True, exist_ok=True)
FETCH_LOG = META_DIR / "fetch_log.jsonl"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
FIELDS = ("post_id", "channel", "sub_type", "source", "url", "published_at",
          "fetched_at", "text", "collect_method")
GAP_SECONDS = 2.5
MAX_LOADS_PER_SITE = 30


def now_iso():
    return datetime.now(TW).isoformat(timespec="seconds")


def epoch_to_iso(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), TW).isoformat(timespec="seconds")


def to_tw_iso(dt):
    """任何有時區的 datetime → +08:00 ISO8601；None 照回 None。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TW)
    return dt.astimezone(TW).isoformat(timespec="seconds")


class Fetcher:
    """每個網站（hostname）各自計時與計數；超過上限就拒絕再抓。"""

    def __init__(self, channel, max_loads=MAX_LOADS_PER_SITE, gap=GAP_SECONDS, already_used=None, cache_dir=None):
        """cache_dir：開發時重跑解析用的暫存（放在 repo 外，例如 scratchpad），有快取就不再連線、不計次。
        Threads 不用快取（不留整頁 HTML）。"""
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.channel = channel
        self.max_loads = max_loads
        self.gap = gap
        self.last = {}
        self.count = dict(already_used or {})  # 含這次之前已用掉的次數，用來擋 30 次上限
        self.network = {}  # 這次真的連線的次數
        self.cache_hits = 0
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def site(self, url):
        return urlsplit(url).hostname

    def get(self, url, extra_headers=None, timeout=30):
        """回傳 (status, text, fetched_at)；被上限擋下或連線失敗時回 (None, None, None)。
        cookie 一律不留（每次清空）；extra_headers 只放在這一次請求。"""
        host = self.site(url)
        cpath = None
        if self.cache_dir:
            cpath = self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")
            if cpath.exists():
                c = json.loads(cpath.read_text(encoding="utf-8"))
                print(f"[{self.channel}] cache {c['status']} {url}", flush=True)
                self.cache_hits += 1
                return c["status"], c["text"], c["fetched_at"]
        if self.count.get(host, 0) >= self.max_loads:
            self._log(url, None, 0, note=f"skipped: {host} load budget {self.max_loads} reached")
            return None, None, None
        wait = self.gap - (time.time() - self.last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self.count[host] = self.count.get(host, 0) + 1
        self.network[host] = self.network.get(host, 0) + 1
        try:
            r = self.session.get(url, headers=extra_headers or {}, timeout=timeout)
            status, body = r.status_code, r.content
            r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
            text = r.text
            final = r.url
        except requests.RequestException as e:
            status, body, text, final = None, b"", None, url
            self._log(url, None, 0, note=f"error: {type(e).__name__}: {e}")
            self.last[host] = time.time()
            self.session.cookies.clear()
            return None, None, None
        finally:
            self.last[host] = time.time()
        self.session.cookies.clear()  # 不保留伺服器設的 cookie
        fetched_at = now_iso()
        self._log(url, status, len(body), final_url=final if final != url else None)
        if cpath:
            cpath.write_text(json.dumps({"url": url, "status": status, "text": text, "fetched_at": fetched_at},
                                        ensure_ascii=False), encoding="utf-8")
        return status, text, fetched_at

    def _log(self, url, status, nbytes, note=None, final_url=None):
        row = {"t": now_iso(), "channel": self.channel, "url": url, "status": status, "bytes": nbytes}
        if final_url:
            row["final_url"] = final_url
        if note:
            row["note"] = note
        with FETCH_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{self.channel}] {status} {nbytes:>8} {url}" + (f"  ({note})" if note else ""), flush=True)


def record(**kw):
    missing = [k for k in FIELDS if k not in kw]
    if missing:
        raise ValueError(f"missing fields {missing}")
    return {k: kw[k] for k in FIELDS}


def write_jsonl(channel, rows):
    path = OUT_DIR / f"{channel}.jsonl"
    seen, out = set(), []
    for r in rows:
        if r["post_id"] in seen:
            continue
        seen.add(r["post_id"])
        out.append(r)
    with path.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(out)} rows -> {path}")
    return path
