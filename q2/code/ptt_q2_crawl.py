#!/usr/bin/env python3
"""PTT 文章內文爬蟲（Q2）：把 q6 歷史爬蟲留下的文章清單裡的內文抓下來，寫進 PostgreSQL q2_ptt.ptt_articles。

用法（WSL，以 root 身分執行；DB 走 unix socket peer 認證，不需密碼）：
  /root/q2ptt-venv/bin/python ptt_q2_crawl.py --shard 0 --shards 3
  /root/q2ptt-venv/bin/python ptt_q2_crawl.py --shard 0 --shards 3 --limit 5   # 測試用
控制腳本見同目錄的 ptt_q2_crawl_ctl.sh（start / stop / status）。

- 來源（唯讀）：兩個清單資料夾的 <board>.jsonl，只讀 url；同一看板兩邊都有就合併、網址去重。
    1. /mnt/c/q6dl/crawl/ptt/lists（q6 歷史爬蟲的清單；不寫入、不改動 q6dl）
    2. /root/q2ptt-lists（ptt_q2_lists.py 收集的清單）
- 分片：依網址算 crc32 % shards，每篇固定落在一個 shard，清單變大或多了看板也不會重疊。--shard 從 0 開始。
- 每個 shard 內看板輪流抓（round-robin），每個看板由新到舊（依網址裡的 epoch）。
- 清單會持續長大：每 30 分鐘、或手上抓完時，重讀清單，把新出現的網址補進隊列；
  沒有新網址就等 10 分鐘再看（--limit 測試模式則直接結束）。
- 續跑：開始時讀 ptt_articles 已有的 url 並跳過；寫入用 INSERT ... ON CONFLICT (url) DO NOTHING，每篇 commit。
- 404 存成 status=404 的列（其餘欄位 NULL）。其他錯誤（5xx、429、連線失敗、回應太短、over18 擋頁、解析失敗）
  不寫入：先退避再重試，連續失敗時退避加倍（起始 30 秒，上限 600 秒）；同一篇失敗 5 次，本次執行先放棄，
  下次重跑會再試。
- 速率：每個 shard 每次請求前等 --gap 秒 ±--jitter 秒（預設 3 ±1）。shard 數由控制腳本決定
  （實際間隔還要加上回應時間）。
- 進度每 100 篇印一行到 stdout（shard、已抓、剩餘、目前間隔、各看板已抓數）。
"""
import argparse
import json
import random
import re
import sys
import time
import traceback
import zlib
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

LISTS_DIRS = [Path("/mnt/c/q6dl/crawl/ptt/lists"), Path("/root/q2ptt-lists")]
DSN = "dbname=q2_ptt"
BASE = "https://www.ptt.cc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
TW = timezone(timedelta(hours=8))
EPOCH_RE = re.compile(r"/M\.(\d{9,10})\.A\.")

GAP_BASE = 3.0        # 每次請求前等待（秒）
GAP_JITTER = 1.0      # ± 隨機秒數
BACKOFF_START = 30.0  # 第一次失敗後退避秒數，之後每次連續失敗加倍
BACKOFF_CAP = 600.0   # 退避上限 10 分鐘
MAX_TRIES = 5         # 同一篇失敗這麼多次，本次執行先放棄
PROGRESS_EVERY = 100
RELOAD_SEC = 1800     # 每隔多久重讀清單補新網址
IDLE_SEC = 600        # 抓完又沒有新網址時，等多久再重讀
MIN_BODY_LEN = 500    # 比這短的 200 回應視為異常（擋頁、錯誤頁）

INSERT_OK = """
INSERT INTO ptt_articles (url, board, status, title, author, published_at, time_text, body,
                          push_up, push_down, push_arrow, push_count, pushes)
VALUES (%s, %s, 200, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (url) DO NOTHING
"""
INSERT_404 = "INSERT INTO ptt_articles (url, board, status) VALUES (%s, %s, 404) ON CONFLICT (url) DO NOTHING"


def log(tag, msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{tag}] {msg}", flush=True)


def epoch_of(url):
    m = EPOCH_RE.search(url)
    return int(m.group(1)) if m else None


def all_boards():
    """兩個清單資料夾裡所有看板名（排序後）。"""
    return sorted({f.stem for d in LISTS_DIRS if d.is_dir() for f in d.glob("*.jsonl")})


def shard_of(url, shards):
    return zlib.crc32(url.encode()) % shards


def load_board_rows(board, shard, shards):
    """讀兩個資料夾的清單檔（只讀），回傳該看板屬於這個 shard 的文章（url 去重、由新到舊）。"""
    seen, rows = set(), []
    for d in LISTS_DIRS:
        f = d / f"{board}.jsonl"
        if not f.exists():
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = r.get("url")
                if not url or url in seen or epoch_of(url) is None or shard_of(url, shards) != shard:
                    continue
                seen.add(url)
                rows.append({"url": url, "title": r.get("title"), "author": r.get("author")})
    rows.sort(key=lambda r: epoch_of(r["url"]), reverse=True)
    return rows


class Fetcher:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.s.headers["Accept-Language"] = "zh-TW,zh;q=0.9"
        self.s.cookies.set("over18", "1", domain=".ptt.cc")

    def get(self, url):
        """回傳 (status, soup, err, retry_after)。soup 只在 200 且內容正常時有值。"""
        try:
            r = self.s.get(url, timeout=30)
        except Exception as e:  # 連線失敗、逾時等
            return None, None, f"連線失敗 {type(e).__name__}", None
        ra = r.headers.get("Retry-After", "")
        retry_after = float(ra) if ra.strip().isdigit() else None
        if r.status_code == 404:
            return 404, None, None, None
        if r.status_code != 200:
            return r.status_code, None, f"http_{r.status_code}", retry_after
        r.encoding = "utf-8"
        html = r.text
        if len(html) < MIN_BODY_LEN:
            return 200, None, "回應太短", None
        soup = BeautifulSoup(html, "html.parser")
        if soup.select_one("#main-content") is None:
            return 200, None, ("over18 擋頁" if "over18" in html else "缺少 #main-content"), None
        return 200, soup, None, None


def parse_article(soup, url, row):
    """解析文章頁。內文做法與 q6 crawl_ptt.py 的 crawl_articles() 相同：去掉 metaline 與推文後的 #main-content。"""
    main = soup.select_one("#main-content")
    meta = {}
    for m in soup.select("div.article-metaline, div.article-metaline-right"):
        tag, val = m.select_one(".article-meta-tag"), m.select_one(".article-meta-value")
        if tag and val:
            meta[tag.get_text(strip=True)] = val.get_text(strip=True)
    pushes = []
    for p in main.select("div.push"):
        user = p.select_one(".push-userid")
        if not user:
            continue
        tag = p.select_one(".push-tag")
        content = p.select_one(".push-content")
        ip = p.select_one(".push-ipdatetime")
        pushes.append({
            "tag": tag.get_text(strip=True) if tag else "",
            "user": user.get_text(strip=True),
            "content": re.sub(r"^:\s*", "", content.get_text(strip=True)) if content else "",
            "time": ip.get_text(strip=True) if ip else "",
        })
    for x in main.select("div.article-metaline, div.article-metaline-right, div.push"):
        x.extract()
    body = main.get_text("\n", strip=True)
    tags = [p["tag"] for p in pushes]
    return {
        "url": url,
        "title": meta.get("標題") or row.get("title"),
        "author": meta.get("作者") or row.get("author"),
        "published_at": datetime.fromtimestamp(epoch_of(url), TW),
        "time_text": meta.get("時間"),
        "body": body,
        "push_up": tags.count("推"),
        "push_down": tags.count("噓"),
        "push_arrow": tags.count("→"),
        "push_count": len(pushes),
        "pushes": Jsonb(pushes),
    }


class Store:
    def __init__(self, tag):
        self.tag = tag
        self.conn = None
        self.connect()

    def connect(self):
        while True:
            try:
                self.conn = psycopg.connect(DSN)
                return
            except psycopg.Error as e:
                log(self.tag, f"DB 連線失敗：{e}；30 秒後重試")
                time.sleep(30)

    def existing_urls(self, shard, shards):
        with self.conn.cursor() as cur:
            cur.execute("SELECT url FROM ptt_articles")
            found = {r[0] for r in cur if shard_of(r[0], shards) == shard}
        self.conn.commit()
        return found

    def write_ok(self, rec, board):
        params = (rec["url"], board, rec["title"], rec["author"], rec["published_at"], rec["time_text"],
                  rec["body"], rec["push_up"], rec["push_down"], rec["push_arrow"], rec["push_count"], rec["pushes"])
        self._exec(INSERT_OK, params)

    def write_404(self, url, board):
        self._exec(INSERT_404, (url, board))

    def _exec(self, sql, params):
        while True:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql, params)
                self.conn.commit()
                return
            except psycopg.Error as e:
                log(self.tag, f"DB 寫入失敗：{e}；30 秒後重連重試")
                try:
                    self.conn.close()
                except Exception:
                    pass
                time.sleep(30)
                self.connect()


def run(args):
    tag = f"shard{args.shard}/{args.shards}"
    store = Store(tag)
    have = store.existing_urls(args.shard, args.shards)
    known = set(have)   # 已在 DB 或已進過隊列的網址，重讀清單時不重複加
    queues = {}
    per_board = {}

    def reload_lists(first=False):
        added = {}
        for b in all_boards():
            q = queues.setdefault(b, deque())
            per_board.setdefault(b, 0)
            n = 0
            for r in load_board_rows(b, args.shard, args.shards):
                if r["url"] not in known:
                    known.add(r["url"])
                    q.append(r)
                    n += 1
            if n:
                added[b] = n
        total = sum(added.values())
        if first:
            log(tag, f"看板：{', '.join(queues)}；已有 {len(have)} 筆（跳過）；待抓 {total} 篇；"
                     f"預估約 {total * GAP_BASE / 3600:.1f} 小時（未計回應時間）")
            for b in queues:
                log(tag, f"  {b}: 待抓 {len(queues[b])}")
        elif total:
            log(tag, f"重讀清單：新增 {total} 篇（" + " ".join(f"{b}+{n}" for b, n in added.items()) + "）")
        return total

    reload_lists(first=True)
    last_reload = time.time()

    fetcher = Fetcher()
    tries = {}
    stats = {200: 0, 404: 0}
    streak = 0      # 連續失敗次數
    written = 0     # 本次執行寫入（200 或 404）的篇數

    def remaining():
        return sum(len(q) for q in queues.values())

    def progress():
        cur_gap = min(BACKOFF_CAP, BACKOFF_START * 2 ** (streak - 1)) if streak else GAP_BASE
        boards_txt = " ".join(f"{b}={n}" for b, n in per_board.items() if n)
        log(tag, f"已抓 {written}（200:{stats[200]} 404:{stats[404]}） 剩餘 {remaining()} "
                 f"間隔 {cur_gap:.1f}s 連續失敗 {streak} | {boards_txt}")

    while not (args.limit and written >= args.limit):
        if time.time() - last_reload > RELOAD_SEC or not remaining():
            reload_lists()
            last_reload = time.time()
            if not remaining():
                if args.limit:
                    break
                log(tag, f"清單都抓完了（本次寫入 {written} 篇）；{IDLE_SEC // 60} 分鐘後再看清單有沒有新網址")
                time.sleep(IDLE_SEC)
                continue
        for b in list(queues):
            q = queues[b]
            if not q or (args.limit and written >= args.limit):
                continue
            row = q.popleft()
            url = row["url"]
            rec = None
            time.sleep(GAP_BASE + random.uniform(-GAP_JITTER, GAP_JITTER))
            st, soup, err, retry_after = fetcher.get(url)
            if err is None and soup is not None:
                try:
                    rec = parse_article(soup, url, row)
                except Exception as e:  # 頁面結構異常：當成失敗，重試，不讓整個 shard 停掉
                    st, err = 200, f"解析失敗 {type(e).__name__}: {e}"[:200]
                    rec = None
                    log(tag, f"{err} {url}\n{traceback.format_exc()[-400:]}")
            if err is None and st == 404:
                store.write_404(url, b)
            elif err is None and soup is not None and rec is not None:
                store.write_ok(rec, b)
            else:
                if err is None:
                    err = f"http_{st}"
                streak += 1
                n = tries.get(url, 0) + 1
                tries[url] = n
                backoff = min(BACKOFF_CAP, BACKOFF_START * 2 ** (streak - 1))
                if retry_after:
                    backoff = min(BACKOFF_CAP, max(backoff, retry_after))
                if n < MAX_TRIES:
                    q.append(row)  # 放回該看板隊尾，稍後再試
                    action = "稍後重試"
                else:
                    action = f"已失敗 {n} 次，本次放棄（下次重跑會再試）"
                log(tag, f"失敗 {url} ({err})；{action}；退避 {backoff:.0f}s（連續 {streak} 次）")
                time.sleep(backoff)
                continue

            streak = 0
            tries.pop(url, None)
            stats[st] = stats.get(st, 0) + 1
            per_board[b] += 1
            written += 1
            if args.limit:
                title = (rec or {}).get("title") if rec else None
                log(tag, f"[test] {st} {b} {url} | {title}")
            if written % PROGRESS_EVERY == 0:
                progress()
            if not q:
                log(tag, f"看板 {b} 目前清單抓完（本次 {per_board[b]} 篇）")
            if args.limit and written >= args.limit:
                break

    if remaining() == 0:
        log(tag, f"全部完成：本次寫入 {written} 篇（200:{stats[200]} 404:{stats[404]}）")
    else:
        log(tag, f"停止（--limit 達到）：本次寫入 {written} 篇，尚餘 {remaining()} 篇")
    store.conn.close()
    return 0


def main():
    global GAP_BASE, GAP_JITTER
    ap = argparse.ArgumentParser(description="PTT 文章內文爬蟲（Q2），寫入 q2_ptt.ptt_articles")
    ap.add_argument("--shard", type=int, required=True, help="第幾個 shard（從 0 開始）")
    ap.add_argument("--shards", type=int, default=3, help="shard 總數（預設 3）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多寫入幾篇（0 = 不限，測試用）")
    ap.add_argument("--gap", type=float, default=GAP_BASE, help=f"每次請求前等幾秒（預設 {GAP_BASE}）")
    ap.add_argument("--jitter", type=float, default=GAP_JITTER, help=f"等待時間 ± 隨機秒數（預設 {GAP_JITTER}）")
    args = ap.parse_args()
    GAP_BASE, GAP_JITTER = args.gap, min(args.jitter, args.gap)
    if not 0 <= args.shard < args.shards:
        sys.exit("--shard 必須介於 0 與 --shards-1 之間")
    try:
        return run(args)
    except KeyboardInterrupt:
        log(f"shard{args.shard}/{args.shards}", "中斷（已寫入的篇數都已 commit，可直接重跑續抓）")
        return 130
    except Exception:
        log(f"shard{args.shard}/{args.shards}", "異常結束：\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
