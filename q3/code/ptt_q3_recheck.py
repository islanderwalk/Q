#!/usr/bin/env python3
"""QSearch 第 3 題：用 repo 內已抓的資料重算 README 的數字，不連網。

檢查四件事：
1. 列表不變量：填滿的列表頁剛好 20 格、文章 ID 時間戳沒有倒序。
2. 每天發文量：6 個完整日的平均（README 的 890 篇/天）。
3. 短視窗為什麼不能外推：把 ptt_q3_measure.py 的日均算法套在 7 天資料的每個 1.2 小時、
   6 小時、24 小時視窗上，看估出來的日均分布多散；再用普查的留言時間回推，
   在 1.2 小時視窗結束時去抓，每篇看得到幾則留言。
4. 兩條規格的請求數：README 的算法與 ptt_q3_measure.py 原始輸出的算法各算一次。

用法（在 repo 根目錄）：
    python q3/code/ptt_q3_recheck.py                                   # 印出 JSON
    python q3/code/ptt_q3_recheck.py --out q3/data/runs/2026-10-30_recheck.json
"""
import argparse
import bisect
import csv
import json
import statistics
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

TW = timezone(timedelta(hours=8))
WINDOW_END = datetime(2026, 10, 26, 3, 36, tzinfo=TW)  # 列表快照的時間點（同 ptt_q3_census.py）
DATA = Path(__file__).resolve().parents[1] / "data"
PER_PAGE = 20
# shared/docs/python.md 記載的 Windows 實跑：1.2 小時視窗推出的日均與 7 天留言數（原始 log 不在 repo）
SHORT_RUN_PER_DAY = 1781
SHORT_RUN_COMMENTS = 74822


def load(index_csv, census_jsonl):
    with open(index_csv, encoding="utf-8-sig") as fp:
        index = list(csv.DictReader(fp))
    census = {}
    with open(census_jsonl, encoding="utf-8") as fp:
        for line in fp:
            rec = json.loads(line)
            census[rec["aid"]] = rec
    return index, list(census.values())


def check_index(index):
    pages = {}
    for r in index:
        pages.setdefault(int(r["page"]), []).append(r)
    newest = max(pages)
    full = [p for p in pages if p != newest]
    seq = [int(r["ts"]) for p in sorted(pages) for r in pages[p] if r["ts"]]  # 舊→新
    return {
        "rows": len(index), "pages": len(pages), "newest_page": newest, "newest_page_rows": len(pages[newest]),
        "full_pages": len(full), "full_pages_with_20": sum(len(pages[p]) == PER_PAGE for p in full),
        "adjacent_pairs": len(seq) - 1, "ts_inversions": sum(b < a for a, b in zip(seq, seq[1:])),
        "deleted_rows": sum(r["deleted"] == "True" for r in index),
    }


def daily(window_ts, end):
    days = Counter(datetime.fromtimestamp(t, TW).date() for t in window_ts)
    ds = sorted(days)
    full = ds[1:-1]  # 頭尾兩天只涵蓋部分時段
    return {
        "per_day": {d.isoformat(): days[d] for d in ds},
        "full_days": len(full),
        "full_day_mean": round(sum(days[d] for d in full) / len(full), 1),
        "full_day_min": min(days[d] for d in full), "full_day_max": max(days[d] for d in full),
        # ptt_q3_measure.py 的算法：視窗篇數 ÷（現在 − 視窗內最舊一篇）
        "script_style_per_day": round(len(window_ts) / ((end - min(window_ts)) / 86400), 1),
    }, full


def window_ends(first_end, last_end, step_s=600):
    t = first_end
    while t <= last_end:
        yield t
        t += step_s


def script_estimate(ts_sorted, t, w):
    """ptt_q3_measure.py 的日均：視窗篇數 ÷（結束時間 − 視窗內最舊一篇）；視窗內沒有文章時它印 0。"""
    lo = bisect.bisect_left(ts_sorted, t - w)
    hi = bisect.bisect_right(ts_sorted, t)
    span = t - ts_sorted[lo] if hi > lo else 0
    return (hi - lo) / (span / 86400) if span else 0.0


def rolling_estimates(ts_sorted, first_end, last_end, hours, step_s=600):
    """視窗長度 hours，結束時間從 first_end 每 step_s 秒往後推，用 ptt_q3_measure.py 的算法估日均。"""
    w = hours * 3600
    est, by_hour, empty = [], {}, 0
    for t in window_ends(first_end, last_end, step_s):
        e = script_estimate(ts_sorted, t, w)
        empty += e == 0
        est.append(e)
        by_hour.setdefault(datetime.fromtimestamp(t, TW).hour, []).append(e)
    est.sort()
    q = statistics.quantiles(est, n=20)
    return est, by_hour, {
        "window_hours": hours, "windows": len(est), "empty_windows": empty, "step_s": step_s,
        "min": round(est[0]), "p5": round(q[0]), "median": round(statistics.median(est)),
        "p95": round(q[-1]), "max": round(est[-1]),
    }


def visible_comments(rec, age_min):
    """發文 age_min 分鐘時看得到幾則留言（delays_min 是留言時間減發文時間，取整數分鐘）。"""
    return sum(d <= age_min for d in rec["delays_min"])


def young_windows(ok, ts_sorted, first_end, last_end, hours=1.2, step_s=600):
    """每個視窗一結束就把視窗內的文章全抓，平均每篇看得到幾則留言；另外記下該視窗估出的日均與結束時刻。"""
    w = hours * 3600
    recs = sorted(ok, key=lambda r: r["ts"])
    keys = [r["ts"] for r in recs]
    out = []
    for t in window_ends(first_end, last_end, step_s):
        sub = recs[bisect.bisect_left(keys, t - w):bisect.bisect_right(keys, t)]
        if sub:
            v = statistics.mean(visible_comments(r, (t - r["ts"]) / 60) for r in sub)
            out.append((script_estimate(ts_sorted, t, w), v, datetime.fromtimestamp(t, TW).hour))
    return out


def main():
    ap = argparse.ArgumentParser(description="QSearch 第 3 題：離線重算 README 的數字")
    ap.add_argument("--index", default=str(DATA / "ptt_q3_out" / "index_entries.csv"))
    ap.add_argument("--census", default=str(DATA / "ptt_q3_census_out" / "articles.jsonl"))
    ap.add_argument("--out", default=None, help="JSON 輸出檔（預設只印出來）")
    args = ap.parse_args()

    index, census = load(args.index, args.census)
    end = WINDOW_END.timestamp()
    start = end - 7 * 86400
    window = [r for r in index if r["ts"] and int(r["ts"]) >= start]
    alive = {r["aid"] for r in window if r["deleted"] == "False"}
    res = {"window": {"start": datetime.fromtimestamp(start, TW).isoformat(timespec="minutes"),
                      "end": WINDOW_END.isoformat(timespec="minutes")}}
    res["index"] = check_index(index)
    ts_sorted = sorted(int(r["ts"]) for r in window)
    res["daily"], full = daily(ts_sorted, end)

    ok = [r for r in census if r.get("status") == 200 and "comments" in r]
    lat = sorted(r["latency"] for r in ok)
    # 普查只記每篇的最終狀態，沒記每次嘗試。失敗一次會先退避 5 秒再重試（失敗兩次至少 15 秒），
    # 所以回應時間 < 5 秒的文章一定只送 1 次；5–15 秒的最多多送 1 次。
    # 每篇抓完才寫紀錄，中斷當下正在送的請求沒有紀錄，所以上限只是約略值。
    slow_recs = sorted((r for r in census if r["latency"] >= 5), key=lambda r: r["fetched_at"])
    slow = [r["latency"] for r in slow_recs]
    res["census"] = {
        "records": len(census), "targets_equal_window_alive": {r["aid"] for r in census} == alive,
        "status": dict(Counter(str(r["status"]) for r in census)),
        "comments": sum(r["comments"] for r in ok), "comments_per_article": round(sum(r["comments"] for r in ok) / len(ok), 1),
        "latency_p95_s": round(statistics.quantiles(lat, n=20)[-1], 3),
        "latency_max_below_5s": max(r["latency"] for r in census if r["latency"] < 5),
        "latency_5s_or_more": {"articles": len(slow), "min": min(slow, default=None), "max": max(slow, default=None),
                               "over_15s": sum(x >= 15 for x in slow),
                               "fetched_at": [datetime.fromtimestamp(r["fetched_at"], TW).strftime("%m/%d %H:%M")
                                              for r in slow_recs]},
        "http_requests_range": [len(census), len(census) + len(slow) + sum(x >= 15 for x in slow)],
        "http_requests_note": "下限是篇數；上限假設回應時間 ≥5 秒的文章各多送 1 次，不含中斷當下沒有紀錄的請求",
    }

    # 短視窗：結束時間落在 6 個完整日之內（00:00 起算），每 10 分鐘取一次
    d0 = datetime.combine(full[0], datetime.min.time(), TW).timestamp()
    d1 = datetime.combine(full[-1], datetime.min.time(), TW).timestamp() + 86400
    mean = res["daily"]["full_day_mean"]
    res["short_windows"] = []
    for hours in (1.2, 6, 24):
        est, by_hour, summ = rolling_estimates(ts_sorted, d0 + hours * 3600, d1, hours)
        summ["share_within_10pct_of_mean"] = round(sum(abs(e - mean) <= mean * 0.1 for e in est) / len(est), 3)
        summ["share_ge_short_run"] = round(sum(e >= SHORT_RUN_PER_DAY for e in est) / len(est), 3)
        if hours < 24:
            summ["median_by_end_hour"] = {h: round(statistics.median(v)) for h, v in sorted(by_hour.items())}
        res["short_windows"].append(summ)

    # 短視窗裡的文章才發文 0–72 分鐘，留言還沒進來：用普查的留言時間算「發文 a 分鐘時看得到幾則」
    yw = young_windows(ok, ts_sorted, d0 + 1.2 * 3600, d1)
    vis = sorted(v for _, v, _ in yw)
    hot = [(v, h) for e, v, h in yw if e >= SHORT_RUN_PER_DAY]  # 估出的日均跟 Windows 那次一樣高的視窗
    qv = statistics.quantiles(vis, n=20)
    res["young_articles"] = {
        "visible_comments_by_age_min": {a: round(statistics.mean(visible_comments(r, a) for r in ok), 1)
                                        for a in (0, 36, 72)},
        # 年齡在 0–72 分鐘平均分布時（每分鐘一樣多篇）的平均
        "visible_comments_age_uniform_0_72_min": round(
            statistics.mean(statistics.mean(visible_comments(r, a) for a in range(72)) for r in ok), 1),
        "visible_at_1_2h_window_end": {
            "windows": len(vis), "p5": round(qv[0], 1), "median": round(statistics.median(vis), 1),
            "p95": round(qv[-1], 1)},
        "visible_at_1_2h_window_end_ge_short_run": {
            "windows": len(hot), "end_hours": sorted({h for _, h in hot}),
            "min": round(min(v for v, _ in hot), 1), "median": round(statistics.median(v for v, _ in hot), 1),
            "max": round(max(v for v, _ in hot), 1)},
        "visible_at_36_min_by_post_hour": {
            h: round(statistics.mean(visible_comments(r, 36) for r in ok
                                     if datetime.fromtimestamp(r["ts"], TW).hour == h), 1) for h in range(24)},
        "final_comments_per_article": res["census"]["comments_per_article"],
        "short_run_implied_comments_per_article": round(SHORT_RUN_COMMENTS / (SHORT_RUN_PER_DAY * 7), 1),
    }

    # 兩條規格：README 用 6 個完整日平均，規格 1 = 首抓 + 列表 70、規格 2 = 第 1–6 天各重抓一次；
    # ptt_q3_measure.py 原始輸出用整個視窗的日均，規格 1 = 首抓 + 列表 n/20 + 8、規格 2 = 第 0–6 天共 7 次
    n = round(mean)
    ns = len(ts_sorted) / ((end - ts_sorted[0]) / 86400)  # 不先取整數，與 ptt_q3_measure.py 一致
    s1, s2 = n + 70, n * 6
    m1, m2 = round(ns + ns / PER_PAGE + 8), round(ns * 7)
    res["spec_cost"] = {
        "readme": {"per_day": n, "spec1": s1, "spec2": s2, "ratio": round(s2 / s1, 2),
                   "ratio_range_by_day": [round(6 * x / (x + 70), 2) for x in (res["daily"]["full_day_min"], res["daily"]["full_day_max"])]},
        "measure_script": {"per_day": round(ns), "spec1": m1, "spec2": m2, "ratio": round(ns * 7 / (ns + ns / PER_PAGE + 8), 2)},
    }

    text = json.dumps(res, ensure_ascii=False, indent=1) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
