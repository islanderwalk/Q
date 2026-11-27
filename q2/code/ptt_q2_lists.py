#!/usr/bin/env python3
"""PTT 看板文章清單收集器（Q2 用）：翻看板目錄頁（index<N>.html），把文章網址寫成清單，給 ptt_q2_crawl.py 抓內文。

用法（WSL，以 root 身分執行）：
  /root/q2ptt-venv/bin/python ptt_q2_lists.py                 # 預設看板、預設目標筆數
  /root/q2ptt-venv/bin/python ptt_q2_lists.py --max-pages 2   # 測試用：只翻 2 頁就停
控制腳本見同目錄的 ptt_q2_crawl_ctl.sh（lists-start / lists-stop / status）。

- 輸出：/root/q2ptt-lists/<board>.jsonl，一行一篇，欄位與 q6 清單相同
  （board, url, title, author, date_mmdd, published_at, push, fetched_at）。不寫入、不讀取 q6dl。
- 看板：預設只挑 q6 歷史爬蟲沒在抓的看板，和 q6 清單不重疊。
- 每個看板從最新一頁往回翻到第 1 頁；--boards 給多個看板時輪流翻（round-robin）。
  控制腳本每個看板各開一支（--boards 只給一個板），平行翻。
  所有看板（含其他支）合計寫到 --target 筆就停；合計數取自各看板狀態檔的 rows。
- 續跑：每個看板各存一個狀態檔 /root/q2ptt-lists/state_<board>.json（下一頁要翻第幾頁），
  每頁寫完就存；重跑會接著翻。各支只寫自己看板的狀態檔與清單檔，不互相覆寫。
  舊版共用的 state.json 若還在，第一次啟動時會把該看板的狀態搬到 state_<board>.json（state.json 本身不動）。
  網址以清單檔裡已有的去重。
- 速率：單一連線，每次請求前等 --gap 秒 ±--jitter 秒（預設 3.5 ±1）。失敗時退避（起始 30 秒、連續失敗加倍、上限 600 秒），
  同一頁重試；目錄頁 404 就跳過那一頁。
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT_DIR = Path("/root/q2ptt-lists")
LEGACY_STATE = OUT_DIR / "state.json"   # 舊版：所有看板共用一個狀態檔
BASE = "https://www.ptt.cc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
TW = timezone(timedelta(hours=8))
EPOCH_RE = re.compile(r"/M\.(\d{9,10})\.A\.")
PREV_RE = re.compile(r"index(\d+)\.html")

BOARDS = ["C_Chat", "movie", "joke", "Japan_Travel", "NBA", "BabyMother"]
TARGET = 800_000
GAP_BASE = 3.5
GAP_JITTER = 1.0
BACKOFF_START = 30.0
BACKOFF_CAP = 600.0
PROGRESS_EVERY = 50   # 每翻幾頁印一行進度


TAG = "lists"


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{TAG}] {msg}", flush=True)


def state_path(board):
    return OUT_DIR / f"state_{board}.json"


def load_board_state(board):
    """讀該看板的狀態檔；沒有就從舊版 state.json 搬；都沒有就從最新一頁開始。"""
    try:
        return json.loads(state_path(board).read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    try:
        legacy = json.loads(LEGACY_STATE.read_text(encoding="utf-8"))
        if board in legacy:
            log(f"{board}：從舊版 state.json 搬狀態（下一頁 p{legacy[board].get('next_page')}）")
            return legacy[board]
    except FileNotFoundError:
        pass
    return {"next_page": None, "newest_page": None, "pages": 0, "rows": 0,
            "earliest": None, "done": False}


def save_board_state(board, bs):
    p = state_path(board)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(bs, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def total_rows():
    """所有看板（含其他支收集器）合計已寫幾筆：加總各 state_<board>.json 的 rows。"""
    n = 0
    for p in OUT_DIR.glob("state_*.json"):
        try:
            n += int(json.loads(p.read_text(encoding="utf-8")).get("rows", 0))
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    return n


def known_urls(board):
    f = OUT_DIR / f"{board}.jsonl"
    urls = set()
    if f.exists():
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    urls.add(json.loads(line)["url"])
                except (json.JSONDecodeError, KeyError):
                    pass
    return urls


def parse_index(html, board):
    """回傳 (rows, prev_page)。被刪的文章（沒有連結）跳過。"""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    now = datetime.now(TW).isoformat(timespec="seconds")
    for ent in soup.select("div.r-ent"):
        a = ent.select_one("div.title a")
        if not a or not a.get("href"):
            continue
        url = BASE + a["href"]
        m = EPOCH_RE.search(url)
        if not m:
            continue
        nrec = ent.select_one("div.nrec")
        author = ent.select_one("div.author")
        date = ent.select_one("div.date")
        rows.append({
            "board": board,
            "url": url,
            "title": a.get_text(strip=True),
            "author": author.get_text(strip=True) if author else "",
            "date_mmdd": date.get_text(strip=True) if date else "",
            "published_at": datetime.fromtimestamp(int(m.group(1)), TW).isoformat(),
            "push": nrec.get_text(strip=True) if nrec else "",
            "fetched_at": now,
        })
    prev = None
    for a in soup.select("div.btn-group-paging a"):
        if "上頁" in a.get_text() and a.get("href"):
            mm = PREV_RE.search(a["href"])
            if mm:
                prev = int(mm.group(1))
    return rows, prev


def main():
    global GAP_BASE, GAP_JITTER, TAG
    ap = argparse.ArgumentParser(description="PTT 看板文章清單收集器（Q2）")
    ap.add_argument("--boards", default=",".join(BOARDS), help="逗號分隔的看板名")
    ap.add_argument("--target", type=int, default=TARGET, help="所有看板合計寫到幾筆就停")
    ap.add_argument("--max-pages", type=int, default=0, help="本次最多翻幾頁（0 = 不限，測試用）")
    ap.add_argument("--gap", type=float, default=GAP_BASE, help=f"每次請求前等幾秒（預設 {GAP_BASE}）")
    ap.add_argument("--jitter", type=float, default=GAP_JITTER, help=f"等待時間 ± 隨機秒數（預設 {GAP_JITTER}）")
    args = ap.parse_args()
    GAP_BASE, GAP_JITTER = args.gap, min(args.jitter, args.gap)
    boards = [b for b in args.boards.split(",") if b]
    if len(boards) == 1:
        TAG = f"lists:{boards[0]}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    st = {b: load_board_state(b) for b in boards}
    for b in boards:
        save_board_state(b, st[b])   # 先落地，讓其他支的 total_rows() 算得到
    seen = {b: known_urls(b) for b in boards}
    total = total_rows()
    log(f"看板：{', '.join(boards)}；本支清單已有 {sum(len(v) for v in seen.values())} 筆；"
        f"所有看板合計 {total} 筆；目標 {args.target} 筆")

    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.headers["Accept-Language"] = "zh-TW,zh;q=0.9"
    s.cookies.set("over18", "1", domain=".ptt.cc")

    pages = 0
    streak = 0
    try:
        while total < args.target and not (args.max_pages and pages >= args.max_pages):
            active = [b for b in boards if not st[b]["done"]]
            if not active:
                log("所有看板都翻到第 1 頁了")
                break
            for b in active:
                if total >= args.target or (args.max_pages and pages >= args.max_pages):
                    break
                bs = st[b]
                page = bs["next_page"]
                url = f"{BASE}/bbs/{b}/index{page if page else ''}.html"
                time.sleep(GAP_BASE + random.uniform(-GAP_JITTER, GAP_JITTER))
                err = None
                try:
                    r = s.get(url, timeout=30)
                    if r.status_code == 404:
                        log(f"404 跳過 {url}")
                        bs["next_page"] = (page or 1) - 1
                        bs["done"] = bs["next_page"] < 1
                        save_board_state(b, bs)
                        streak = 0
                        continue
                    if r.status_code != 200:
                        err = f"http_{r.status_code}"
                    else:
                        r.encoding = "utf-8"
                        if "r-ent" not in r.text:
                            err = "over18 擋頁" if "over18" in r.text else "頁面沒有文章列"
                except Exception as e:  # 連線失敗、逾時
                    err = f"連線失敗 {type(e).__name__}"
                if err:
                    streak += 1
                    backoff = min(BACKOFF_CAP, BACKOFF_START * 2 ** (streak - 1))
                    log(f"失敗 {url} ({err})；退避 {backoff:.0f}s（連續 {streak} 次），稍後重試同一頁")
                    time.sleep(backoff)
                    continue
                streak = 0
                rows, prev = parse_index(r.text, b)
                if page is None:  # 第一次：index.html 是最新一頁
                    bs["newest_page"] = (prev or 0) + 1
                    page = bs["newest_page"]
                new = [x for x in rows if x["url"] not in seen[b]]
                if new:
                    with open(OUT_DIR / f"{b}.jsonl", "a", encoding="utf-8") as fh:
                        for x in new:
                            fh.write(json.dumps(x, ensure_ascii=False) + "\n")
                    seen[b].update(x["url"] for x in new)
                    total += len(new)
                    bs["rows"] += len(new)
                    bs["earliest"] = min(x["published_at"] for x in new) if bs["earliest"] is None \
                        else min(bs["earliest"], min(x["published_at"] for x in new))
                bs["pages"] += 1
                bs["next_page"] = page - 1
                bs["done"] = bs["next_page"] < 1
                bs["updated"] = datetime.now(TW).isoformat(timespec="seconds")
                save_board_state(b, bs)
                pages += 1
                if args.max_pages:
                    log(f"[test] {b} index{page} +{len(new)} 筆（頁上 {len(rows)} 篇）")
                if pages % PROGRESS_EVERY == 0:
                    total = total_rows()   # 重新加總，算進其他支收集器寫的
                    log(f"本次翻 {pages} 頁；清單合計 {total} 筆 | " + " ".join(
                        f"{x}=p{st[x]['next_page']}/{st[x]['rows']}" for x in boards))
    except KeyboardInterrupt:
        log("中斷（狀態每頁都已存，重跑會接著翻）")
        return 130
    log(f"結束：本次翻 {pages} 頁；清單合計 {total} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(main())
