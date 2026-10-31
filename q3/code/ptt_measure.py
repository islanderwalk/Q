#!/usr/bin/env python3
"""PTT 看板爬蟲與量測。

量兩件事：
1. 連線：每個請求的狀態碼、回應時間、下載大小。
2. 內容：看板列表（篇數、分類、作者、推文數、發文速率），
   並抽樣打開文章計算 推/噓/→ 與內文長度。

用法：
    pip install requests beautifulsoup4
    python ptt_measure.py                      # 預設 Gossiping、5 頁、抽 30 篇、間隔 1.5 秒
    python ptt_measure.py --board Stock --pages 3 --articles 20
    python ptt_measure.py --out-dir /tmp/ptt_run --max-requests 40   # 輸出到別處、限制請求數

每次執行都在 CSV 旁邊的 <CSV 檔名>_summary.jsonl 加一行（請求數、狀態碼、最短請求間隔、
是否碰到上限、怎麼結束的），中斷或出錯時也會寫。
"""
import argparse
import csv
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ptt.cc"
TW = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (X11; Linux x86_64) ptt_measure/1.0"


class Fetcher:
    """包一層 requests，順便記錄每次請求的量測數據。

    每次嘗試（含重試）都算一個請求；設了 max_requests 就在送出第 max_requests + 1 個之前停下。
    """

    def __init__(self, delay, max_requests=None):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        # 八卦板等需要滿 18 歲確認；cookie 只放在這個 session 的記憶體裡，不寫檔
        self.s.cookies.set("over18", "1", domain="www.ptt.cc")
        self.delay = delay
        self.max_requests = max_requests
        self.capped = False  # 是否因為碰到請求上限而停下
        self.log = []  # (url, status, 秒數, bytes)
        self.starts = []  # 每個請求送出的時間（perf_counter），用來算請求間隔；比 log 多的就是送出後沒等到回應

    def get(self, url):
        for attempt in range(2):
            if self.max_requests is not None and len(self.log) >= self.max_requests:
                self.capped = True
                return None
            t0 = time.perf_counter()
            self.starts.append(t0)
            try:
                r = self.s.get(url, timeout=15)
                dt = time.perf_counter() - t0
                self.log.append((url, r.status_code, dt, len(r.content)))
                time.sleep(self.delay)
                if r.status_code == 200:
                    return r.text
                print(f"  ! {r.status_code} {url}", file=sys.stderr)
            except requests.RequestException as e:
                dt = time.perf_counter() - t0
                self.log.append((url, f"ERR {type(e).__name__}", dt, 0))
                print(f"  ! {type(e).__name__}: {e}", file=sys.stderr)
                time.sleep(self.delay)
        return None

    def summary(self):
        gaps = [b - a for a, b in zip(self.starts, self.starts[1:])]
        status = dict(Counter(str(r[1]) for r in self.log))
        if len(self.starts) > len(self.log):
            status["no_response"] = len(self.starts) - len(self.log)
        return {
            "requests": len(self.starts),
            "status": status,
            "min_interval_s": round(min(gaps), 3) if gaps else None,
            "max_requests": self.max_requests,
            "capped": self.capped,
        }


def exit_reason(e):
    """把中途結束的例外寫成 summary 的 exit 欄位。"""
    if isinstance(e, KeyboardInterrupt):
        return "interrupted"
    if isinstance(e, SystemExit):
        return f"exit: {e.code}"
    return f"error: {type(e).__name__}"


def append_run(path, run):
    """在 summary.jsonl 加一行本次的紀錄，回傳（執行次數, 累計請求數）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(run, ensure_ascii=False) + "\n")
    runs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return len(runs), sum(r["requests"] for r in runs)


def parse_nrec(text):
    """推文數欄位：爆=100、X1..X9=-10..-90、XX=-100、空白=0。"""
    text = text.strip()
    if not text:
        return 0
    if text == "爆":
        return 100
    if text == "XX":
        return -100
    if text.startswith("X") and text[1:].isdigit():
        return -10 * int(text[1:])
    return int(text) if text.isdigit() else 0


def parse_index(html):
    """回傳 (文章列表, 上一頁網址)。置底文（r-list-sep 之後）不算。"""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("div.r-list-container")
    entries = []
    for div in container.find_all("div", recursive=False):
        classes = div.get("class", [])
        if "r-list-sep" in classes:
            break
        if "r-ent" not in classes:
            continue
        a = div.select_one("div.title a")
        title = (a.get_text() if a else div.select_one("div.title").get_text()).strip()
        href = a["href"] if a else None
        m = re.search(r"M\.(\d+)\.A", href or "")
        entries.append({
            "title": title,
            "url": BASE + href if href else None,
            "author": div.select_one("div.author").get_text().strip(),
            "date": div.select_one("div.date").get_text().strip(),
            "nrec": parse_nrec(div.select_one("div.nrec").get_text()),
            "ts": int(m.group(1)) if m else None,
            "deleted": a is None,
        })
    prev = None
    for btn in soup.select("div.btn-group-paging a"):
        if "上頁" in btn.get_text() and btn.get("href"):
            prev = BASE + btn["href"]
    return entries, prev


def parse_article(html):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content")
    if main is None:
        return None
    pushes = Counter()
    users = set()
    for p in main.select("div.push"):
        tag = p.select_one("span.push-tag")
        uid = p.select_one("span.push-userid")
        if tag:
            pushes[tag.get_text().strip()] += 1
        if uid:
            users.add(uid.get_text().strip())
    for sel in ("div.article-metaline", "div.article-metaline-right", "div.push"):
        for el in main.select(sel):
            el.decompose()
    body = main.get_text().split("※ 發信站")[0].rstrip("-\n ")
    return {
        "push": pushes.get("推", 0),
        "boo": pushes.get("噓", 0),
        "arrow": pushes.get("→", 0),
        "users": users,
        "body_len": len(body.strip()),
    }


def fmt_stats(xs, unit="", digits=3):
    if not xs:
        return "（無資料）"
    xs = sorted(xs)
    p95 = statistics.quantiles(xs, n=20)[-1] if len(xs) >= 2 else xs[0]
    f = f"{{:.{digits}f}}{unit}"
    return (f"平均 {f.format(statistics.mean(xs))}｜中位數 {f.format(statistics.median(xs))}｜"
            f"最小 {f.format(xs[0])}｜最大 {f.format(xs[-1])}｜P95 {f.format(p95)}")


def main():
    ap = argparse.ArgumentParser(description="PTT 看板爬蟲與量測")
    ap.add_argument("--board", default="Gossiping")
    ap.add_argument("--pages", type=int, default=5, help="往回爬幾頁列表")
    ap.add_argument("--articles", type=int, default=30, help="抽樣打開幾篇文章（0 = 不開）")
    ap.add_argument("--delay", type=float, default=1.5, help="每個請求之間等幾秒（預設 1.5）")
    ap.add_argument("--csv", default=None, help="文章列表輸出檔（預設 <out-dir>/ptt_<board>.csv）")
    ap.add_argument("--out-dir", default=".", help="輸出資料夾（預設目前資料夾）")
    ap.add_argument("--max-requests", type=int, default=None, help="請求數上限（含重試；預設不設限）")
    args = ap.parse_args()

    f = Fetcher(args.delay, args.max_requests)
    started = datetime.now(TW)
    t_start = time.perf_counter()
    csv_path = Path(args.csv or Path(args.out_dir) / f"ptt_{args.board}.csv")
    summary_path = csv_path.with_name(csv_path.stem + "_summary.jsonl")
    run = {"started": started.isoformat(timespec="seconds"), "board": args.board,
           "pages": args.pages, "articles": args.articles, "delay_s": args.delay, "exit": "ok"}
    try:
        measure(args, f, started, t_start, csv_path, summary_path)
        if f.capped:
            run["exit"] = "capped"
    except BaseException as e:
        run["exit"] = exit_reason(e)
        raise
    finally:
        # 中斷、出錯也要留下本次的請求數；加在最後一行，跑幾次就有幾行
        run = {**run, "elapsed_s": round(time.perf_counter() - t_start, 1), **f.summary()}
        n_runs, total = append_run(summary_path, run)
        print(f"[請求] 共 {run['requests']} 次（{run['exit']}），最短間隔 {run['min_interval_s']} 秒，"
              f"上限 {args.max_requests}{'（已達上限）' if f.capped else ''}；"
              f"{summary_path.name} 累計 {n_runs} 次執行、{total:,} 個請求", file=sys.stderr)


def measure(args, f, started, t_start, csv_path, summary_path):
    """爬列表、抽樣文章、寫 CSV、印出統計；請求紀錄由 main() 在結束時寫。"""
    # 1. 列表頁
    url = f"{BASE}/bbs/{args.board}/index.html"
    entries, pages_ok = [], 0
    for i in range(args.pages):
        print(f"[列表 {i + 1}/{args.pages}] {url}", file=sys.stderr)
        html = f.get(url)
        if html is None:
            break
        page_entries, prev = parse_index(html)
        entries.extend(page_entries)
        pages_ok += 1
        if not prev:
            break
        url = prev

    alive = [e for e in entries if not e["deleted"]]
    alive.sort(key=lambda e: e["ts"] or 0, reverse=True)

    # 2. 抽樣文章（最新的 N 篇）
    sample = []
    for i, e in enumerate(alive[: args.articles]):
        if f.capped:
            break
        print(f"[文章 {i + 1}/{min(args.articles, len(alive))}] {e['title'][:30]}", file=sys.stderr)
        html = f.get(e["url"])
        if html:
            info = parse_article(html)
            if info:
                sample.append({**e, **info})

    elapsed = time.perf_counter() - t_start

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=["date", "nrec", "author", "title", "url", "deleted"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(entries)
    run = f.summary()

    # 3. 統計
    ok = [r for r in f.log if r[1] == 200]
    statuses = Counter(str(r[1]) for r in f.log)
    tags = Counter((re.match(r"(Re: |Fw: )?\[([^\]]+)\]", e["title"]) or [None, None, "（無分類）"])[2]
                   for e in alive)
    authors = Counter(e["author"] for e in alive)
    nrecs = [e["nrec"] for e in alive]
    ts = [e["ts"] for e in alive if e["ts"]]
    replies = sum(e["title"].startswith("Re:") for e in alive)

    out = []
    p = out.append
    p("== 結果（請整段貼回來）==")
    p(f"看板：{args.board}")
    p(f"執行時間：{started:%Y-%m-%d %H:%M:%S} (UTC+8)，總耗時 {elapsed:.1f} 秒")
    p(f"參數：pages={args.pages} articles={args.articles} delay={args.delay}s")
    p("")
    p("[連線量測]")
    p(f"請求總數：{len(f.log)}，狀態碼分布：{dict(statuses)}")
    p(f"成功率：{len(ok) / len(f.log) * 100:.1f}%" if f.log else "成功率：N/A")
    p(f"回應時間（秒）：{fmt_stats([r[2] for r in ok])}")
    p(f"頁面大小（KB）：{fmt_stats([r[3] / 1024 for r in ok], digits=1)}")
    p(f"下載總量：{sum(r[3] for r in f.log) / 1024:.1f} KB")
    p(f"最短請求間隔：{run['min_interval_s']} 秒；請求上限：{'不設限' if args.max_requests is None else args.max_requests}"
      + ("（已達上限，以下結果不完整）" if f.capped else ""))
    p("")
    p("[看板列表]")
    p(f"成功爬取列表頁：{pages_ok}/{args.pages}")
    p(f"文章數：{len(entries)}（現存 {len(alive)}，已刪除 {len(entries) - len(alive)}）")
    if ts:
        t0, t1 = datetime.fromtimestamp(min(ts), TW), datetime.fromtimestamp(max(ts), TW)
        hours = (max(ts) - min(ts)) / 3600
        p(f"發文時間範圍：{t0:%m/%d %H:%M} ～ {t1:%m/%d %H:%M}（{hours:.2f} 小時）")
        if hours > 0:
            p(f"發文速率：約 {len(ts) / hours:.1f} 篇/小時")
    p(f"回覆文（Re:）比例：{replies / len(alive) * 100:.1f}%" if alive else "回覆文比例：N/A")
    p("分類前 5：" + "、".join(f"{k} {v}" for k, v in tags.most_common(5)))
    p("發文最多作者前 5：" + "、".join(f"{k} {v}" for k, v in authors.most_common(5)))
    p(f"列表推文數（爆=100、X=負）：{fmt_stats(nrecs, digits=1)}")
    p(f"爆文數：{sum(n >= 100 for n in nrecs)}，X 文數：{sum(n < 0 for n in nrecs)}")
    top = sorted(alive, key=lambda e: e["nrec"], reverse=True)[:3]
    for e in top:
        p(f"  熱門：[{e['nrec']}] {e['title']}  ({e['url']})")
    p("")
    p(f"[文章抽樣：最新 {len(sample)} 篇]")
    if sample:
        tp, tb, ta = (sum(s[k] for s in sample) for k in ("push", "boo", "arrow"))
        total = tp + tb + ta
        p(f"推 {tp}、噓 {tb}、→ {ta}，合計 {total} 則留言")
        if total:
            p(f"推噓比：推 {tp / total * 100:.1f}%、噓 {tb / total * 100:.1f}%、→ {ta / total * 100:.1f}%")
        p(f"每篇留言數：{fmt_stats([s['push'] + s['boo'] + s['arrow'] for s in sample], digits=1)}")
        p(f"每篇內文字數：{fmt_stats([s['body_len'] for s in sample], digits=0)}")
        p(f"不重複留言帳號數：{len(set().union(*(s['users'] for s in sample)))}")
        best = max(sample, key=lambda s: s["push"] + s["boo"] + s["arrow"])
        p(f"留言最多：{best['title']}（推 {best['push']}／噓 {best['boo']}／→ {best['arrow']}）")
    p("")
    p(f"文章列表已存成：{csv_path}；請求紀錄：{summary_path}")
    p("== 結果結束 ==")
    print("\n".join(out))


if __name__ == "__main__":
    main()
