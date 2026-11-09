#!/usr/bin/env python3
"""QSearch 筆試第 3 題：PTT 八卦板全文搜尋 ETL 的規模量測。

量測項目（對應題目第 1 小題「先估規模」）：
1. 列表頁：往回爬滿 N 天，算每日／每小時發文量、刪文率、每頁篇數。
2. 文章抽樣：依文章年齡（0–1 天、1–2 天…）分層抽樣，量
   留言數、內文與留言字數、HTML 大小、留言在發文後多久出現。
3. 推算：7 天文章數／留言數／文字量，以及兩條資料規格各自的每日請求數與倍數。

用法：
    pip install requests beautifulsoup4
    python ptt_q3_measure.py --out /tmp/q3_run                     # 預設 7 天、每天抽 30 篇、間隔 1.5 秒
    python ptt_q3_measure.py --out /tmp/q3_run --days 7 --per-day 50 --delay 2.0
    python ptt_q3_measure.py --out /tmp/q3_run --max-requests 600  # 限制請求數

--out 一定要給：預設的 q3/data/ptt_q3_out 是 10/26 的列表，普查、重算、圖表與測試都讀它，
要覆蓋得另外加 --overwrite-repo-data。
每次執行都在輸出資料夾的 run_summary.jsonl 加一行（請求數、狀態碼、最短請求間隔、是否碰到上限、
怎麼結束的），中斷或出錯時也會寫。
"""
import argparse
import csv
import json
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ptt.cc"
TW = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (X11; Linux x86_64) ptt_q3_measure/1.0"
PER_PAGE = 20  # PTT 每個列表頁固定 20 筆（最新頁除外）
REPO_OUT = Path(__file__).resolve().parents[1] / "data" / "ptt_q3_out"  # 10/26 的列表，其他程式與測試都讀它


class Fetcher:
    """包一層 requests，記錄每次請求的狀態碼、耗時、大小。

    每次嘗試（含重試）都算一個請求；設了 max_requests 就在送出第 max_requests + 1 個之前停下。
    """

    def __init__(self, delay, max_requests=None):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        # 八卦板的滿 18 歲確認頁就是設這個 cookie；只放在這個 session 的記憶體裡，不寫檔
        self.s.cookies.set("over18", "1", domain="www.ptt.cc")
        self.delay = delay
        self.max_requests = max_requests
        self.capped = False  # 是否因為碰到請求上限而停下
        self.log = []  # (kind, status, 秒數, bytes)
        self.starts = []  # 每個請求送出的時間（perf_counter），用來算請求間隔；比 log 多的就是送出後沒等到回應

    def get(self, url, kind):
        for attempt in range(3):
            if self.max_requests is not None and len(self.log) >= self.max_requests:
                self.capped = True
                return None
            t0 = time.perf_counter()
            self.starts.append(t0)
            try:
                r = self.s.get(url, timeout=20)
                self.log.append((kind, r.status_code, time.perf_counter() - t0, len(r.content)))
                time.sleep(self.delay)
                if r.status_code == 200:
                    return r
                if r.status_code == 404:
                    return None
                print(f"  ! {r.status_code} {url}", file=sys.stderr)
            except requests.RequestException as e:
                self.log.append((kind, f"ERR {type(e).__name__}", time.perf_counter() - t0, 0))
                print(f"  ! {type(e).__name__}: {e}", file=sys.stderr)
                time.sleep(self.delay)
            time.sleep(5 * (attempt + 1))  # 失敗就退避，不硬打
        return None

    def summary(self):
        gaps = [b - a for a, b in zip(self.starts, self.starts[1:])]
        status = dict(Counter(str(x[1]) for x in self.log))
        if len(self.starts) > len(self.log):
            status["no_response"] = len(self.starts) - len(self.log)
        return {
            "requests": len(self.starts),
            "by_kind": dict(Counter(x[0] for x in self.log)),
            "status": status,
            "min_interval_s": round(min(gaps), 3) if gaps else None,
            "max_requests": self.max_requests,
            "capped": self.capped,
        }


def exit_reason(e):
    """把中途結束的例外寫成 run_summary 的 exit 欄位。"""
    if isinstance(e, KeyboardInterrupt):
        return "interrupted"
    if isinstance(e, SystemExit):
        return f"exit: {e.code}"
    return f"error: {type(e).__name__}"


def append_run(path, run):
    """在 run_summary.jsonl 加一行本次的紀錄，回傳（執行次數, 累計請求數）。"""
    with open(path, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(run, ensure_ascii=False) + "\n")
    runs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return len(runs), sum(r["requests"] for r in runs)


def short_window_note(hours):
    """視窗不到一天時，日均會被時段放大或縮小（每小時 4–64 篇），不能外推。"""
    if hours < 24:
        return f"（注意：只涵蓋 {hours:.1f} 小時，日均受時段影響，不可外推）"
    return ""


def parse_nrec(text):
    """列表推文數：爆=100、X1..X9=-10..-90、XX=-100、空白=0。"""
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


def parse_index(html, newest):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("div.r-list-container")
    entries = []
    for div in container.find_all("div", recursive=False):
        classes = div.get("class", [])
        if "r-list-sep" in classes and newest:
            break  # 最新頁底下是置底文
        if "r-ent" not in classes:
            continue
        a = div.select_one("div.title a")
        href = a["href"] if a else None
        m = re.search(r"M\.(\d+)\.A\.([0-9A-F]+)", href or "")
        entries.append({
            "aid": f"M.{m.group(1)}.A.{m.group(2)}" if m else None,
            "ts": int(m.group(1)) if m else None,
            "title": (a.get_text() if a else div.select_one("div.title").get_text()).strip(),
            "author": div.select_one("div.author").get_text().strip(),
            "nrec": parse_nrec(div.select_one("div.nrec").get_text()),
            "deleted": a is None,
            "url": BASE + href if href else None,
        })
    prev = None
    for btn in soup.select("div.btn-group-paging a"):
        if "上頁" in btn.get_text() and btn.get("href"):
            prev = btn["href"]
    return entries, prev


def parse_article(html, post_ts):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content")
    if main is None:
        return None
    post_dt = datetime.fromtimestamp(post_ts, TW)
    tags = Counter()
    comment_chars = 0
    delays_h = []
    for p in main.select("div.push"):
        tag = p.select_one("span.push-tag")
        content = p.select_one("span.push-content")
        when = p.select_one("span.push-ipdatetime")
        if tag is None:
            continue  # 「檔案過大！部分文章無法顯示」這類提示列
        tags[tag.get_text().strip()] += 1
        if content:
            comment_chars += len(content.get_text().lstrip(": ").strip())
        m = re.search(r"(\d{2})/(\d{2})\s+(\d{2}):(\d{2})", when.get_text() if when else "")
        if m:
            mo, d, hh, mm = map(int, m.groups())
            year = post_dt.year + (1 if mo < post_dt.month else 0)  # 跨年
            try:
                cdt = datetime(year, mo, d, hh, mm, tzinfo=TW)
                delays_h.append(max(0.0, (cdt - post_dt).total_seconds() / 3600))
            except ValueError:
                pass
    for sel in ("div.article-metaline", "div.article-metaline-right", "div.push"):
        for el in main.select(sel):
            el.decompose()
    body = main.get_text().split("※ 發信站")[0].rstrip("-\n ").strip()
    return {
        "push": tags.get("推", 0),
        "boo": tags.get("噓", 0),
        "arrow": tags.get("→", 0),
        "comments": sum(tags.values()),
        "body_chars": len(body),
        "body_bytes": len(body.encode("utf-8")),
        "comment_chars": comment_chars,
        "delays_h": delays_h,
    }


def stats(xs, digits=1):
    if not xs:
        return "（無資料）"
    xs = sorted(xs)
    q = statistics.quantiles(xs, n=20) if len(xs) >= 2 else [xs[0]] * 19
    f = f"{{:,.{digits}f}}"
    return (f"平均 {f.format(statistics.mean(xs))}｜中位數 {f.format(statistics.median(xs))}｜"
            f"P95 {f.format(q[-1])}｜最大 {f.format(xs[-1])}｜n={len(xs)}")


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} PB"


def main():
    if hasattr(sys.stdout, "reconfigure"):  # 繁中 Windows 的 cp950 印不出 ≥、≈，導向檔案時會中斷
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="QSearch 第 3 題：PTT 八卦板規模量測")
    ap.add_argument("--board", default="Gossiping")
    ap.add_argument("--days", type=float, default=7, help="往回爬幾天的列表")
    ap.add_argument("--per-day", type=int, default=30, help="每個年齡層抽幾篇文章")
    ap.add_argument("--delay", type=float, default=1.5, help="每個請求之間等幾秒（預設 1.5）")
    ap.add_argument("--out", default=str(REPO_OUT),
                    help="原始資料輸出資料夾（預設的 q3/data/ptt_q3_out 要加 --overwrite-repo-data 才會寫入）")
    ap.add_argument("--overwrite-repo-data", action="store_true", help="確定要覆蓋 repo 內 q3/data/ptt_q3_out 的列表")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-requests", type=int, default=None, help="請求數上限（含重試；預設不設限）")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if out_dir.resolve() == REPO_OUT.resolve() and not args.overwrite_repo_data:
        sys.exit("q3/data/ptt_q3_out 存的是 10/26 的列表，普查、重算、圖表與測試都讀它；"
                 "請用 --out 指定別的資料夾（確定要覆蓋就加 --overwrite-repo-data）")
    out_dir.mkdir(parents=True, exist_ok=True)
    f = Fetcher(args.delay, args.max_requests)
    started = datetime.now(TW)
    t_start = time.perf_counter()
    run = {"started": started.isoformat(timespec="seconds"), "board": args.board, "days": args.days,
           "per_day": args.per_day, "delay_s": args.delay, "exit": "ok"}
    try:
        measure(args, f, out_dir, started, t_start)
        if f.capped:
            run["exit"] = "capped"
    except BaseException as e:
        run["exit"] = exit_reason(e)
        raise
    finally:
        # 中斷、出錯也要留下本次的請求數；加在最後一行，跑幾次就有幾行
        run = {**run, "elapsed_s": round(time.perf_counter() - t_start, 1), **f.summary()}
        n_runs, total = append_run(out_dir / "run_summary.jsonl", run)
        print(f"[請求] 共 {run['requests']} 次 {run['by_kind']}（{run['exit']}），最短間隔 {run['min_interval_s']} 秒，"
              f"上限 {args.max_requests}{'（已達上限）' if f.capped else ''}；"
              f"這個資料夾累計 {n_runs} 次執行、{total:,} 個請求", file=sys.stderr)


def measure(args, f, out_dir, started, t_start):
    """爬列表、抽樣文章、印出統計；請求紀錄由 main() 在結束時寫。"""
    now_ts = time.time()
    cutoff = now_ts - args.days * 86400

    # ── 1. 列表頁：用頁碼往回爬，避免新文章把頁面往後推 ──────────────────────
    r = f.get(f"{BASE}/bbs/{args.board}/index.html", "index")
    if r is None:
        sys.exit(f"已達請求上限 {args.max_requests}，第一個列表頁沒有送出" if f.capped else "連不到列表頁")
    first, prev = parse_index(r.text, newest=True)
    newest_page = int(re.search(r"index(\d+)\.html", prev).group(1)) + 1
    pages = {newest_page: first}
    p = newest_page - 1
    while p >= 1:
        if (newest_page - p) % 25 == 0:
            print(f"[列表] index{p}.html，已爬 {len(pages)} 頁", file=sys.stderr)
        r = f.get(f"{BASE}/bbs/{args.board}/index{p}.html", "index")
        if r is None and f.capped:
            print(f"  ! 達到請求上限 {args.max_requests}，停止往回翻", file=sys.stderr)
            break
        if r is None:
            print(f"  ! index{p} 失敗，略過", file=sys.stderr)
            p -= 1
            continue
        entries, _ = parse_index(r.text, newest=False)
        pages[p] = entries
        ts_on_page = [e["ts"] for e in entries if e["ts"]]
        if ts_on_page and max(ts_on_page) < cutoff:
            break
        p -= 1

    all_entries = []
    for pg in sorted(pages, reverse=True):
        for e in pages[pg]:
            all_entries.append({**e, "page": pg})
    # 已刪除的文章沒有 ID，用同頁前後文章的時間估
    window = [e for e in all_entries if e["ts"] and e["ts"] >= cutoff]
    alive = [e for e in window if not e["deleted"]]
    win_pages = sorted({e["page"] for e in window})
    deleted_in_window = sum(
        1 for e in all_entries if e["deleted"] and e["page"] in win_pages[1:-1]
    )
    full_pages = [pg for pg in pages if pg != newest_page]
    bad_pages = [pg for pg in full_pages if len(pages[pg]) != PER_PAGE]

    with open(out_dir / "index_entries.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=["page", "aid", "ts", "nrec", "deleted", "author", "title", "url"])
        w.writeheader()
        w.writerows(all_entries)

    # ── 2. 依文章年齡分層抽樣 ─────────────────────────────────────────────────
    rng = random.Random(args.seed)
    buckets = defaultdict(list)
    for e in alive:
        buckets[int((now_ts - e["ts"]) // 86400)].append(e)
    sample = []
    n_buckets = int(args.days + 0.999)
    for b in range(n_buckets):
        pool = buckets.get(b, [])
        picks = rng.sample(pool, min(args.per_day, len(pool)))
        for i, e in enumerate(picks):
            if f.capped:
                break
            print(f"[文章] 第 {b} 天 {i + 1}/{len(picks)} {e['title'][:28]}", file=sys.stderr)
            r = f.get(e["url"], "article")
            if r is None:
                continue
            info = parse_article(r.text, e["ts"])
            if info:
                sample.append({**e, **info, "age_day": b, "html_bytes": len(r.content),
                               "age_h": (now_ts - e["ts"]) / 3600})

    with open(out_dir / "article_sample.json", "w", encoding="utf-8") as fp:
        json.dump(sample, fp, ensure_ascii=False, indent=1)

    elapsed = time.perf_counter() - t_start
    run = f.summary()

    # ── 3. 統計與推算 ─────────────────────────────────────────────────────────
    o = []
    P = o.append
    P("== 結果（請整段貼回來）==")
    P(f"題目：QSearch 第 3 題 PTT 全文搜尋｜看板：{args.board}")
    P(f"量測時間：{started:%Y-%m-%d %H:%M} (UTC+8)，耗時 {elapsed / 60:.1f} 分鐘")
    P(f"參數：days={args.days} per_day={args.per_day} delay={args.delay}s")

    P("")
    P("[A. 連線]")
    ok = [x for x in f.log if x[1] == 200]
    P(f"請求數 {len(f.log)}（列表 {sum(x[0] == 'index' for x in f.log)}、"
      f"文章 {sum(x[0] == 'article' for x in f.log)}），狀態碼 {dict(Counter(str(x[1]) for x in f.log))}")
    P(f"回應時間（秒）：{stats([x[2] for x in ok], 3)}")
    P(f"列表頁大小（KB）：{stats([x[3] / 1024 for x in ok if x[0] == 'index'])}")
    P(f"最短請求間隔：{run['min_interval_s']} 秒；請求上限：{'不設限' if args.max_requests is None else args.max_requests}"
      + ("（已達上限，以下結果不完整）" if f.capped else ""))

    P("")
    P(f"[B. 發文量：最近 {args.days:g} 天]")
    hours = (now_ts - min(e["ts"] for e in window)) / 3600 if window else 0
    per_day = len(window) / (hours / 24) if hours else 0
    P(f"視窗內文章（有 ID）：{len(window)} 篇，涵蓋 {hours:.1f} 小時 → 平均 {per_day:,.0f} 篇/天"
      + short_window_note(hours))
    P(f"視窗內列表頁：{len(win_pages)} 頁（index{win_pages[0]}–{win_pages[-1]}），"
      f"約 {per_day / PER_PAGE:,.0f} 頁/天" if win_pages else "視窗內列表頁：0")
    P(f"已刪除（列表上無連結）：約 {deleted_in_window} 篇"
      f"（{deleted_in_window / max(1, len(window) + deleted_in_window) * 100:.1f}%）")
    day_cnt = Counter(datetime.fromtimestamp(e["ts"], TW).strftime("%m/%d(%a)") for e in window)
    P("每日（UTC+8，頭尾兩天不完整）：" + "、".join(f"{k} {v}" for k, v in sorted(day_cnt.items())))
    full_days = sorted({datetime.fromtimestamp(e["ts"], TW).date() for e in window})[1:-1]
    hour_cnt = Counter(datetime.fromtimestamp(e["ts"], TW).hour for e in window
                       if datetime.fromtimestamp(e["ts"], TW).date() in full_days)
    if full_days:
        nd = len(full_days)
        hourly = [hour_cnt.get(h, 0) / nd for h in range(24)]
        peak = max(range(24), key=lambda h: hourly[h])
        low = min(range(24), key=lambda h: hourly[h])
        P(f"完整天數 {nd} 天的每日平均：{sum(hourly):,.0f} 篇")
        P(f"每小時平均：尖峰 {peak:02d} 時 {hourly[peak]:.0f} 篇、離峰 {low:02d} 時 {hourly[low]:.0f} 篇")
        P("每小時分布：" + " ".join(f"{h:02d}:{hourly[h]:.0f}" for h in range(24)))
        busiest6 = max(sum(hourly[(h + i) % 24] for i in range(6)) for h in range(24))
        P(f"最忙的連續 6 小時：{busiest6:,.0f} 篇（6 小時抓取批次的上限）")
    P(f"資料不變量檢查：完整列表頁 {len(full_pages)} 頁中，篇數 ≠ {PER_PAGE} 的有 {len(bad_pages)} 頁")
    ts_seq = [e["ts"] for pg in sorted(pages) for e in pages[pg] if e["ts"]]  # 舊→新
    inversions = sum(1 for a, b in zip(ts_seq, ts_seq[1:]) if b < a)
    P(f"列表順序與文章 ID 時間戳不一致的相鄰對：{inversions}")
    nrecs = [e["nrec"] for e in alive]
    P(f"列表推文數：{stats(nrecs)}；爆文 {sum(n >= 100 for n in nrecs)} 篇")

    P("")
    P(f"[C. 文章抽樣：{len(sample)} 篇，依年齡分層]")
    if sample:
        P("年齡層｜篇數｜平均留言｜中位數留言｜平均 HTML KB")
        for b in range(n_buckets):
            s = [x for x in sample if x["age_day"] == b]
            if s:
                P(f"  {b}–{b + 1} 天｜{len(s)}｜{statistics.mean(x['comments'] for x in s):,.1f}｜"
                  f"{statistics.median(x['comments'] for x in s):,.0f}｜"
                  f"{statistics.mean(x['html_bytes'] for x in s) / 1024:,.1f}")
        mature = [x for x in sample if x["age_h"] >= 48] or sample
        P(f"留言數（≥2 天的文章，視為已穩定）：{stats([x['comments'] for x in mature])}")
        P(f"內文字數：{stats([x['body_chars'] for x in sample], 0)}")
        P(f"每則留言字數：平均 {sum(x['comment_chars'] for x in sample) / max(1, sum(x['comments'] for x in sample)):.1f}")
        P(f"HTML 大小（KB，≥2 天）：{stats([x['html_bytes'] / 1024 for x in mature])}")
        tp, tb, ta = (sum(x[k] for x in sample) for k in ("push", "boo", "arrow"))
        P(f"推/噓/→ 比例：{tp}/{tb}/{ta}")

        # 留言抵達時間：用 ≥3 天的文章，看留言在發文後多久進來
        old = [x for x in sample if x["age_h"] >= 72]
        delays = [d for x in old for d in x["delays_h"]]
        if delays:
            total = len(delays)
            cum = "、".join(f"{h}h 內 {sum(d <= h for d in delays) / total * 100:.1f}%"
                           for h in (1, 6, 24, 48, 72, 168))
            P(f"留言抵達時間（{len(old)} 篇 ≥3 天文章、{total} 則留言）：{cum}")
            after = [x for x in old if any(d > 24 for d in x["delays_h"])]
            P(f"24 小時後仍有新留言的文章：{len(after)}/{len(old)} 篇"
              f"（{len(after) / len(old) * 100:.1f}%）")

        # 不變量候選：列表推文數 ≈ 推 − 噓
        def clamp(n):
            # 列表顯示：≥100 爆、-10~-99 為 X1~X9、-1~-9 不顯示
            return 100 if n >= 100 else (-100 if n <= -100 else (-((-n) // 10) * 10 if n <= -10 else max(n, 0)))
        diffs = [clamp(x["push"] - x["boo"]) - x["nrec"] for x in sample]
        P(f"列表推文數 vs 文章內 推−噓：完全相同 {sum(d == 0 for d in diffs)}/{len(diffs)}、"
          f"差距 ≤2 {sum(abs(d) <= 2 for d in diffs)}/{len(diffs)}")

        # ── 推算 ──────────────────────────────────────────────────────────
        P("")
        P("[D. 推算（7 天搜尋視窗）]")
        n_day = per_day
        n7 = n_day * 7
        c_mean = statistics.mean(x["comments"] for x in mature)
        body_b = statistics.mean(x["body_bytes"] for x in sample)
        cchar = sum(x["comment_chars"] for x in sample) / max(1, sum(x["comments"] for x in sample))
        html_by_age = {b: statistics.mean(x["html_bytes"] for x in sample if x["age_day"] == b)
                       for b in range(n_buckets) if any(x["age_day"] == b for x in sample)}
        if short_window_note(hours):
            P("（以下的日均來自不到一天的視窗，只能示範算式，不能當規模估算）")
        P(f"文章數：{n_day:,.0f} 篇/天 × 7 = {n7:,.0f} 篇")
        P(f"留言數：{n7:,.0f} × {c_mean:.1f} 則/篇 = {n7 * c_mean:,.0f} 則")
        text7 = n7 * body_b + n7 * c_mean * (cchar * 3 + 40)  # 留言 +40 bytes 給 ID、時間
        P(f"文字量：內文 {human_bytes(n7 * body_b)} + 留言 {human_bytes(n7 * c_mean * (cchar * 3 + 40))}"
          f" ≈ {human_bytes(text7)}（純文字 UTF-8）")
        html7 = n7 * statistics.mean(x["html_bytes"] for x in mature)
        P(f"原始 HTML 若全存：≈ {human_bytes(html7)}")
        idx = n_day / PER_PAGE
        spec1 = n_day + idx + 4 * 2  # 新文章各抓一次 + 對應列表頁 + 每 6 小時輪詢的重疊頁
        spec2 = n_day * 7  # 7 天內每篇每天重抓一次
        P(f"規格 1（6 小時內入庫）每日請求：文章 {n_day:,.0f} + 列表 {idx:,.0f} + 輪詢重疊 8 ≈ {spec1:,.0f}")
        P(f"規格 2（7 天內每天更新留言）每日請求：{n_day:,.0f} × 7 = {spec2:,.0f}")
        P(f"兩者合計每日 ≈ {spec2 + idx + 8:,.0f}（規格 2 的第 0 天就是規格 1 的首抓）"
          f" → 平均 {(spec2 + idx + 8) / 86400:.3f} req/s")
        P(f"請求數倍數：規格 2 / 規格 1 ≈ {spec2 / spec1:.1f} 倍")
        if html_by_age:
            b1 = n_day * html_by_age.get(0, 0)
            b2 = n_day * sum(html_by_age.get(b, html_by_age[max(html_by_age)]) for b in range(7))
            P(f"每日下載量：規格 1 ≈ {human_bytes(b1)}、規格 2 ≈ {human_bytes(b2)}"
              f"（{b2 / b1:.1f} 倍，舊文留言多、頁面較大）" if b1 else "")
    P("")
    P(f"原始資料：{out_dir}/index_entries.csv、{out_dir}/article_sample.json；請求紀錄：{out_dir}/run_summary.jsonl")
    P("== 結果結束 ==")
    print("\n".join(o))


if __name__ == "__main__":
    main()
