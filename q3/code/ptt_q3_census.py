#!/usr/bin/env python3
"""QSearch 第 3 題：規模估算的補強驗證。

A：重開原本抽樣的 210 篇，逐欄量留言的實際 UTF-8 bytes（推/噓/→、帳號、內容、時間），
   取代「每則留言其他欄位 40 B」這個假設。
B：把同一個 7 天視窗（q3/data/ptt_q3_out/index_entries.csv）的全部文章都打開，
   數出留言總數與文字總量，取代抽樣推估。

同一支程式依序抓（A 的 210 篇排最前面），預設每個請求之間至少等 1.5 秒，不平行。
每篇結果立刻寫進 JSONL；中斷後重跑會從斷點續抓。

用法：
    python ptt_q3_census.py            # 抓取，並在 A、B 完成時各產生一份報告
    python ptt_q3_census.py --report   # 只用已抓的資料重新產生報告

換一個視窗普查（不碰 repo 內的資料）：先用 ptt_q3_measure.py 把列表抓到別的資料夾，
再指定視窗結束時間與天數，例如普查「昨天」一整天（台灣時間今天 00:00 往回 1 天）：
    python ptt_q3_measure.py --days 2 --per-day 10 --out /tmp/q3_run
    python ptt_q3_census.py --index /tmp/q3_run/index_entries.csv --sample /tmp/q3_run/article_sample.json \
        --window-end "$(TZ=Asia/Taipei date +%Y-%m-%dT00:00)" --days 1 --max-requests 1200 --out /tmp/q3_census

每次抓取都在輸出資料夾的 run_summary.jsonl 加一行（本次請求數、狀態碼、最短請求間隔、是否碰到上限、
怎麼結束的），中斷、碰到上限或連續失敗停下時也會寫；續抓幾次就有幾行，總請求數是各行相加。
換了視窗或列表卻沒給 --out 時直接停下，不寫進預設資料夾；列表沒涵蓋整個視窗時也不抓。
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

TW = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (X11; Linux x86_64) ptt_q3_census/1.0"
WINDOW_START = datetime(2026, 10, 26, 3, 36, tzinfo=TW)  # 第一次量測的時間點（預設視窗的結束時間）
OLD_FIELD_GUESS = 40  # 先前假設：每則留言除了內容以外的欄位 bytes


def nbytes(s):
    return len(s.encode("utf-8"))


def parse_article(html, post_ts):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content")
    if main is None:
        return None
    post_dt = datetime.fromtimestamp(post_ts, TW)
    r = {"push": 0, "boo": 0, "arrow": 0, "warn_rows": 0,
         "tag_bytes": 0, "user_bytes": 0, "content_bytes": 0, "content_chars": 0,
         "time_bytes": 0, "with_ip": 0, "delays_min": []}
    for p in main.select("div.push"):
        tag = p.select_one("span.push-tag")
        if tag is None:
            r["warn_rows"] += 1  # 例如「檔案過大！部分文章無法顯示」
            continue
        t = tag.get_text().strip()
        key = {"推": "push", "噓": "boo", "→": "arrow"}.get(t)
        if key:
            r[key] += 1
        user = p.select_one("span.push-userid")
        content = p.select_one("span.push-content")
        when = p.select_one("span.push-ipdatetime")
        u = user.get_text().strip() if user else ""
        c = content.get_text().lstrip(": ").strip() if content else ""
        w = when.get_text().strip() if when else ""
        r["tag_bytes"] += nbytes(t)
        r["user_bytes"] += nbytes(u)
        r["content_bytes"] += nbytes(c)
        r["content_chars"] += len(c)
        r["time_bytes"] += nbytes(w)
        if re.search(r"\d+\.\d+\.\d+\.\d+", w):
            r["with_ip"] += 1
        m = re.search(r"(\d{2})/(\d{2})\s+(\d{2}):(\d{2})", w)
        if m:
            mo, d, hh, mm = map(int, m.groups())
            year = post_dt.year + (1 if mo < post_dt.month else 0)  # 跨年
            try:
                cdt = datetime(year, mo, d, hh, mm, tzinfo=TW)
                r["delays_min"].append(max(0, int((cdt - post_dt).total_seconds() // 60)))
            except ValueError:
                pass
    r["comments"] = r["push"] + r["boo"] + r["arrow"]
    for sel in ("div.article-metaline", "div.article-metaline-right", "div.push"):
        for el in main.select(sel):
            el.decompose()
    body = main.get_text().split("※ 發信站")[0].rstrip("-\n ").strip()
    r["body_chars"] = len(body)
    r["body_bytes"] = nbytes(body)
    return r


def parse_window_end(text):
    """ISO 8601 時間；沒寫時區就當 UTC+8。"""
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=TW)


def load_targets(index_csv, sample_json, days, window_end=WINDOW_START):
    end = window_end.timestamp()
    cutoff = end - days * 86400
    rows = {}
    with open(index_csv, encoding="utf-8-sig") as fp:
        for r in csv.DictReader(fp):
            # 視窗是 [結束時間 − days, 結束時間)；列表若抓得比結束時間晚，較新的文章不算
            if r["aid"] and r["deleted"] == "False" and cutoff <= int(r["ts"]) < end:
                rows[r["aid"]] = r
    with open(sample_json, encoding="utf-8") as fp:
        sample = [x["aid"] for x in json.load(fp) if x["aid"] in rows]
    sample_set = set(sample)
    rest = sorted((a for a in rows if a not in sample_set), key=lambda a: int(rows[a]["ts"]))
    return rows, sample, rest


def index_coverage(index_csv, window_end, days):
    """列表有沒有涵蓋整個視窗。回傳（會讓普查不完整的問題, 只需留意的提醒），都是空的代表沒問題。"""
    pages, ts = set(), []
    with open(index_csv, encoding="utf-8-sig") as fp:
        for r in csv.DictReader(fp):
            pages.add(int(r["page"]))
            if r["ts"]:
                ts.append(int(r["ts"]))
    start, end = window_end - timedelta(days=days), window_end
    problems, warnings = [], []
    if not ts or min(ts) >= start.timestamp():
        oldest = f"{datetime.fromtimestamp(min(ts), TW):%m/%d %H:%M}" if ts else "（沒有文章）"
        problems.append(f"列表最舊一篇是 {oldest}，沒有早於視窗起點 {start:%m/%d %H:%M}：列表沒往回翻到視窗開頭")
    missing = sorted(set(range(min(pages), max(pages) + 1)) - pages) if pages else []
    if missing:
        problems.append(f"列表缺了 {len(missing)} 頁（例如 index{missing[0]}）：那幾頁的文章不會被普查")
    if ts and max(ts) < end.timestamp() - 3600:
        # 凌晨一小時可能只有幾篇，所以只提醒：列表可能是在視窗結束前就抓了
        warnings.append(f"列表最新一篇是 {datetime.fromtimestamp(max(ts), TW):%m/%d %H:%M}，"
                        f"比視窗結束時間 {end:%m/%d %H:%M} 早超過 1 小時：視窗尾端可能沒涵蓋")
    return problems, warnings


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


def load_done(path):
    done = {}
    if path.exists():
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                rec = json.loads(line)
                done[rec["aid"]] = rec  # 同一篇重抓過，以最後一筆為準
    return done


def stats(xs, digits=1):
    if not xs:
        return "（無資料）"
    xs = sorted(xs)
    p95 = statistics.quantiles(xs, n=20)[-1] if len(xs) >= 2 else xs[0]
    f = f"{{:,.{digits}f}}"
    return (f"平均 {f.format(statistics.mean(xs))}｜中位數 {f.format(statistics.median(xs))}｜"
            f"P95 {f.format(p95)}｜最大 {f.format(xs[-1])}｜n={len(xs):,}")


def mb(n):
    return f"{n / 1e6:,.1f} MB"


def disp(n):
    """列表推文數的顯示規則：≥100 爆、-10~-99 為 X1~X9、-1~-9 不顯示。"""
    if n >= 100:
        return 100
    if n <= -100:
        return -100
    if n <= -10:
        return -((-n) // 10) * 10
    return max(n, 0)


def field_lines(recs):
    total_c = sum(r["comments"] for r in recs)
    if not total_c:
        return ["（沒有留言資料）"], 0
    per = {k: sum(r[k] for r in recs) / total_c
           for k in ("tag_bytes", "user_bytes", "content_bytes", "time_bytes")}
    other = per["tag_bytes"] + per["user_bytes"] + per["time_bytes"]
    whole = other + per["content_bytes"]
    chars = sum(r["content_chars"] for r in recs)
    lines = [
        f"留言則數：{total_c:,}（{len(recs):,} 篇）",
        f"每則平均 bytes：推/噓/→ {per['tag_bytes']:.1f}｜帳號 {per['user_bytes']:.1f}｜"
        f"時間 {per['time_bytes']:.1f}｜內容 {per['content_bytes']:.1f}",
        f"內容以外的欄位合計：{other:.1f} B（先前假設 {OLD_FIELD_GUESS} B）",
        f"每則留言合計：{whole:.1f} B（先前估 12.3 字 × 3 + {OLD_FIELD_GUESS} ≈ 77 B）",
        f"留言內容平均 {chars / total_c:.1f} 字，每字 {per['content_bytes'] * total_c / max(1, chars):.2f} B",
        f"時間欄帶 IP 的留言：{sum(r['with_ip'] for r in recs) / total_c * 100:.1f}%",
    ]
    return lines, whole


def report_a(done, sample):
    recs = [done[a] for a in sample if a in done and done[a].get("status") == 200 and "comments" in done[a]]
    o = [f"== A：留言欄位實測（原抽樣 {len(sample)} 篇）==",
         f"重抓成功 {len(recs)}/{len(sample)} 篇"]
    lines, _ = field_lines(recs)
    o += lines
    o.append("== A 結束 ==")
    return "\n".join(o)


def report_b(done, rows, sample, days, window_end=WINDOW_START):
    targets = list(rows)
    got = [done[a] for a in targets if a in done]
    ok = [r for r in got if r.get("status") == 200 and "comments" in r]
    gone = [r for r in got if r.get("status") == 404]
    err = [r for r in got if r.get("status") not in (200, 404)]
    o = [f"== B：{days:g} 天全部文章普查（視窗 {window_end - timedelta(days=days):%m/%d %H:%M} ～ {window_end:%m/%d %H:%M}）=="]
    o.append(f"目標 {len(targets):,} 篇；成功 {len(ok):,}、已被刪（404）{len(gone):,}、失敗 {len(err):,}")
    if not ok:
        return "\n".join(o)

    fetched = sorted(r["fetched_at"] for r in got)
    # 中斷後續抓會留下空檔：間隔超過 10 分鐘就切成不同段，只算實際抓取的時間
    segments, seg_start = [], fetched[0]
    for prev, cur in zip(fetched, fetched[1:]):
        if cur - prev > 600:
            segments.append((seg_start, prev))
            seg_start = cur
    segments.append((seg_start, fetched[-1]))
    hours = sum(e - s for s, e in segments) / 3600
    longest = max(e - s for s, e in segments) / 3600
    o.append("")
    o.append("[抓取過程]")
    # 每篇只記最後一次的狀態碼；送了幾個請求（含重試）要看 run_summary.jsonl
    o.append(f"文章 {len(got):,} 篇（最後狀態），分 {len(segments)} 段{'（中途中斷後續抓）' if len(segments) > 1 else ''}，"
             f"實際抓取 {hours:.2f} 小時、最長連續 {longest:.2f} 小時，"
             f"平均每 {hours * 3600 / max(1, len(got) - len(segments)):.2f} 秒一篇；"
             f"最後狀態 {dict(Counter(str(r['status']) for r in got))}")
    o.append(f"回應時間（秒）：{stats([r['latency'] for r in ok], 3)}")
    slow = [r["latency"] for r in got if r["latency"] >= 5]
    if slow:
        # 失敗一次會先退避 5 秒再重試，回應時間把退避也算進去
        o.append(f"回應時間 ≥5 秒的文章 {len(slow):,} 篇（{min(slow):.1f}–{max(slow):.1f} 秒）："
                 f"可能是第一次失敗、退避 5 秒後重試成功（若是，每篇多送 1 個請求）")

    c = [r["comments"] for r in ok]
    o.append("")
    o.append("[留言數：全數，不是抽樣]")
    o.append(f"留言總數：{sum(c):,} 則（推 {sum(r['push'] for r in ok):,}／噓 {sum(r['boo'] for r in ok):,}／"
             f"→ {sum(r['arrow'] for r in ok):,}）")
    o.append(f"每篇留言：{stats(c)}")
    o.append(f"沒有任何留言的文章：{sum(x == 0 for x in c):,} 篇（{sum(x == 0 for x in c) / len(c) * 100:.1f}%）")
    o.append(f"留言最多的 1% 文章佔全部留言：{sum(sorted(c)[-max(1, len(c) // 100):]) / max(1, sum(c)) * 100:.1f}%")
    o.append("依發文日（原視窗內）的平均留言：" + "、".join(
        f"{d} {statistics.mean(v):.1f}" for d, v in sorted(_group(ok, lambda r: datetime.fromtimestamp(r['ts'], TW).strftime('%m/%d')).items())))
    o.append(f"出現「檔案過大」等截斷提示的文章：{sum(r['warn_rows'] > 0 for r in ok):,} 篇")

    o.append("")
    o.append("[文字量：全數]")
    body = sum(r["body_bytes"] for r in ok)
    cm = sum(r["tag_bytes"] + r["user_bytes"] + r["content_bytes"] + r["time_bytes"] for r in ok)
    html = sum(r["html_bytes"] for r in ok)
    o.append(f"內文：{mb(body)}（每篇平均 {body / len(ok):,.0f} B）")
    o.append(f"留言（推噓→＋帳號＋內容＋時間）：{mb(cm)}（每則平均 {cm / max(1, sum(c)):.1f} B）")
    o.append(f"純文字合計：{mb(body + cm)}，留言佔 {cm / max(1, body + cm) * 100:.1f}%")
    o.append(f"原始 HTML 合計：{mb(html)}（每篇平均 {html / len(ok) / 1024:,.1f} KB）")
    lines, _ = field_lines(ok)
    o += ["留言欄位（全部文章）：" + lines[1], "  " + lines[2]]

    o.append("")
    o.append("[留言抵達時間：全數]")
    now = fetched[-1]
    for min_age_h, label in ((72, "抓取時已發文 ≥3 天"), (168, "抓取時已發文 ≥7 天")):
        sub = [r for r in ok if (r["fetched_at"] - r["ts"]) / 3600 >= min_age_h]
        ds = [d for r in sub for d in r["delays_min"]]
        if ds:
            o.append(f"{label}（{len(sub):,} 篇、{len(ds):,} 則）：" + "、".join(
                f"{h}h 內 {sum(d <= h * 60 for d in ds) / len(ds) * 100:.2f}%" for h in (1, 6, 24, 48, 72, 168)))
    sub3 = [r for r in ok if (r["fetched_at"] - r["ts"]) / 3600 >= 72]
    if sub3:
        late = sum(any(d > 24 * 60 for d in r["delays_min"]) for r in sub3)
        o.append(f"24 小時後仍有新留言的文章：{late:,}/{len(sub3):,}（{late / len(sub3) * 100:.1f}%）")

    o.append("")
    o.append("[不變量：列表推文數 vs 推−噓]")
    old = [r for r in ok if window_end.timestamp() - r["ts"] >= 3 * 86400]  # 列表快照時已 ≥3 天，推文數應已穩定
    if old:
        match = sum(disp(r["push"] - r["boo"]) == r["nrec"] for r in old)
        near = sum(abs(disp(r["push"] - r["boo"]) - r["nrec"]) <= 2 for r in old)
        o.append(f"列表快照時已 ≥3 天的 {len(old):,} 篇：完全相同 {match:,}（{match / len(old) * 100:.1f}%）、"
                 f"差距 ≤2 {near:,}（{near / len(old) * 100:.1f}%）")

    if window_end == WINDOW_START and days == 7:  # 先前的推估只針對這個視窗
        o.append("")
        o.append("[對照先前的抽樣推估]")
        o.append(f"留言總數：推估 ≈ 27 萬（19–36 萬）→ 全數 {sum(c):,}")
        o.append(f"純文字：推估 ≈ 26 MB → 全數 {mb(body + cm)}")
        o.append(f"原始 HTML：推估 ≈ 108 MB → 全數 {mb(html)}")
    o.append("== B 結束 ==")
    return "\n".join(o)


def _group(recs, keyf):
    g = {}
    for r in recs:
        g.setdefault(keyf(r), []).append(r["comments"])
    return g


def main():
    if hasattr(sys.stdout, "reconfigure"):  # 繁中 Windows 的 cp950 印不出 ≥、≈，導向檔案時會中斷
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="QSearch 第 3 題補強驗證")
    ap.add_argument("--index", default=str(Path(__file__).resolve().parents[1] / "data" / "ptt_q3_out" / "index_entries.csv"))
    ap.add_argument("--sample", default=str(Path(__file__).resolve().parents[1] / "data" / "ptt_q3_out" / "article_sample.json"))
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--delay", type=float, default=1.5, help="每篇之間等幾秒（預設 1.5）")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "ptt_q3_census_out"),
                    help="輸出資料夾（預設 q3/data/ptt_q3_census_out，會續寫裡面的 articles.jsonl）")
    ap.add_argument("--window-end", default=WINDOW_START.isoformat(timespec="minutes"),
                    help="視窗結束時間（ISO 8601，沒寫時區當 UTC+8）；預設是第一次量測的時間點")
    ap.add_argument("--max-requests", type=int, default=None, help="本次請求數上限（含重試；預設不設限）")
    ap.add_argument("--report", action="store_true", help="只產生報告，不抓取")
    args = ap.parse_args()

    window_end = parse_window_end(args.window_end)
    out = Path(args.out)
    # 預設資料夾放的是 10/19–10/26 的普查；換了視窗或列表還寫進去，兩個視窗會混在同一份 JSONL 與報告裡
    if out.resolve() == Path(ap.get_default("out")).resolve() and (
            window_end != WINDOW_START or args.days != 7
            or any(Path(getattr(args, k)).resolve() != Path(ap.get_default(k)).resolve() for k in ("index", "sample"))):
        sys.exit("預設輸出資料夾存的是 10/19–10/26 的普查；換視窗或換列表時請用 --out 指定別的資料夾")
    problems, warnings = index_coverage(args.index, window_end, args.days)
    for msg in problems + warnings:
        print(f"  ! {msg}", file=sys.stderr)
    if problems and not args.report:
        sys.exit("列表沒有涵蓋整個視窗，不抓（普查會不完整）；請先用 ptt_q3_measure.py 把列表往回抓到視窗起點之前")
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "articles.jsonl"
    rows, sample, rest = load_targets(args.index, args.sample, args.days, window_end)
    done = load_done(jsonl)

    def write_reports():
        (out / "result_A.txt").write_text(report_a(done, sample) + "\n", encoding="utf-8")
        (out / "result_B.txt").write_text(report_b(done, rows, sample, args.days, window_end) + "\n", encoding="utf-8")

    if args.report:
        write_reports()
        print((out / "result_A.txt").read_text(encoding="utf-8"))
        print((out / "result_B.txt").read_text(encoding="utf-8"))
        return

    started = datetime.now(TW)
    t_start = time.perf_counter()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    # 八卦板的滿 18 歲確認頁就是設這個 cookie；只放在這個 session 的記憶體裡，不寫檔
    s.cookies.set("over18", "1", domain="www.ptt.cc")
    finished = {a for a, r in done.items() if r.get("status") in (200, 404)}
    todo = [a for a in sample + rest if a not in finished]
    a_pending = any(a not in finished for a in sample)
    print(f"目標 {len(rows):,} 篇（A {len(sample)} 篇在前），已完成 {len(finished):,}，待抓 {len(todo):,}", flush=True)

    fails = 0
    attempts = []  # 本次每個請求（含重試）收到的狀態碼
    starts = []  # 每個請求送出的時間（perf_counter），用來算請求間隔；比 attempts 多的就是送出後沒等到回應
    capped = False  # 是否因為碰到請求上限而停下
    ended = "ok"
    try:
        with open(jsonl, "a", encoding="utf-8") as fp:
            for i, aid in enumerate(todo, 1):
                row = rows[aid]
                rec = {"aid": aid, "ts": int(row["ts"]), "nrec": int(row["nrec"])}
                t0 = time.perf_counter()
                status, html, size = None, None, 0
                for attempt in range(3):
                    if args.max_requests is not None and len(attempts) >= args.max_requests:
                        capped = True
                        break
                    starts.append(time.perf_counter())
                    try:
                        resp = s.get(row["url"], timeout=20)
                        status, size = resp.status_code, len(resp.content)
                        attempts.append(status)
                        if status in (200, 404):
                            html = resp.text if status == 200 else None
                            break
                    except requests.RequestException as e:
                        status = f"ERR {type(e).__name__}"
                        attempts.append(status)
                    time.sleep(5 * (attempt + 1))  # 失敗就退避，不硬打
                if capped and status is None:
                    break  # 這篇一個請求都還沒送出，不寫紀錄，下次續抓
                rec.update(status=status, html_bytes=size, latency=round(time.perf_counter() - t0, 3),
                           fetched_at=int(time.time()))
                if status == 200:
                    info = parse_article(html, rec["ts"])
                    if info:
                        rec.update(info)
                if status in (200, 404):
                    fails = 0
                else:
                    fails += 1
                    print(f"  ! {status} {aid}（連續失敗 {fails}）", flush=True)
                    if fails >= 30:
                        print("ABORT：連續失敗 30 次，停止抓取以免被擋", flush=True)
                        ended = "abort"
                        break
                    if fails % 10 == 0:
                        time.sleep(300)
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fp.flush()
                done[aid] = rec
                if a_pending and all(done.get(a, {}).get("status") in (200, 404) for a in sample):
                    a_pending = False
                    write_reports()
                    print("A_DONE", flush=True)
                if i % 250 == 0:
                    print(f"[進度] {i:,}/{len(todo):,}", flush=True)
                if capped:
                    break
                time.sleep(args.delay)
        if capped and ended == "ok":
            ended = "capped"
    except BaseException as e:
        ended = exit_reason(e)
        raise
    finally:
        # 中斷、出錯也要留下本次的請求數；加在最後一行，續抓幾次就有幾行
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        status_count = dict(Counter(str(x) for x in attempts))
        if len(starts) > len(attempts):
            status_count["no_response"] = len(starts) - len(attempts)
        run = {"started": started.isoformat(timespec="seconds"), "elapsed_s": round(time.perf_counter() - t_start, 1),
               "exit": ended, "window_end": window_end.isoformat(timespec="minutes"), "days": args.days,
               "delay_s": args.delay, "targets": len(rows), "already_done": len(finished), "todo": len(todo),
               "requests": len(starts), "status": status_count,
               "min_interval_s": round(min(gaps), 3) if gaps else None,
               "max_requests": args.max_requests, "capped": capped}
        n_runs, total = append_run(out / "run_summary.jsonl", run)
        print(f"[請求] 本次共 {run['requests']:,} 次（{ended}），狀態碼 {run['status']}，最短間隔 {run['min_interval_s']} 秒，"
              f"上限 {args.max_requests}{'（已達上限，B 不完整，重跑會續抓）' if capped else ''}；"
              f"這個資料夾累計 {n_runs} 次執行、{total:,} 個請求（{out / 'run_summary.jsonl'}）", flush=True)
    write_reports()
    print("B_DONE", flush=True)


if __name__ == "__main__":
    main()
