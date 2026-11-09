"""每日不變量檢查（答案第 4 題）。

7 天內每個填滿的列表頁：
  (a) 恰好 20 格；
  (b) 每一格是刪文佔位，或 posts 裡有這篇文章（404 也算，代表抓過、後來被刪）；
  (c) 依頁碼、格子順序排下來，相鄰文章 ID 的時間戳不倒序。
(a)(b)(c) 都成立才算通過。輔助指標：列表推文數和文章內「推−噓」換算值相同的比例（不影響通過與否）；
只比列表快照與文章抓取時間相差 30 分鐘內的，因為較舊的列表快照之後不會重讀，文章重抓後推文數本來就會變。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .ptt import PER_PAGE, nrec_of


def check(rows, since_ts):
    """rows：Warehouse.verify_rows() 的結果（已排除最新頁）。純函式，方便單元測試。"""
    pages = {}
    for r in rows:
        pages.setdefault(r["page_no"], []).append(r)
    bad_pages = sorted(p for p, rs in pages.items() if len(rs) != PER_PAGE)
    # 視窗起點那一頁前半可能是 7 天前的文，那些本來就不抓，不算遺漏
    missing = [r["aid"] for r in rows if r["aid"] and not r["deleted"] and (r["ts"] or 0) >= since_ts and not r["in_posts"]]
    seq = [(r["page_no"], r["slot"], r["aid"], r["ts"]) for r in sorted(rows, key=lambda r: (r["page_no"], r["slot"]))
           if r["aid"] and r["ts"]]
    out_of_order = [(a[2], b[2]) for a, b in zip(seq, seq[1:]) if b[3] < a[3]]
    checked = agree = 0
    for r in rows:
        if r["aid"] and r["in_posts"] and r["status"] == 200 and r["push"] is not None and r.get("same_time", True):
            checked += 1
            agree += int(nrec_of(r["push"], r["boo"] or 0) == r["nrec"])
    ok = bool(pages) and not bad_pages and not missing and not out_of_order
    return {
        "pages_checked": len(pages), "pages_not_20": len(bad_pages), "missing_ids": len(missing),
        "out_of_order_pairs": len(out_of_order), "nrec_checked": checked,
        "nrec_agree_ratio": round(agree / checked, 4) if checked else None, "ok": ok,
        "details": {"bad_pages": bad_pages[:20], "missing": missing[:20], "out_of_order": out_of_order[:20],
                    "page_range": [min(pages), max(pages)] if pages else None},
    }


def set_gauges(m, res, at):
    for k in ("pages_checked", "pages_not_20", "missing_ids", "out_of_order_pairs", "nrec_checked"):
        m.verify.labels(k).set(res[k])
    m.verify.labels("nrec_agree_ratio").set(res["nrec_agree_ratio"] or 0)
    m.verify_ok.set(1 if res["ok"] else 0)
    m.verify_last.set(at.timestamp())


def run(ctx):
    cfg, wh, m = ctx.cfg, ctx.wh, ctx.metrics
    now = ctx.now()
    since = now - timedelta(days=cfg.window_days)
    since_ts = int(since.timestamp())
    bytes0 = wh.bytes_processed
    res = check(wh.verify_rows(cfg.board, since_ts), since_ts)
    wh.insert_rows("verify_runs", [{
        "run_at": now, "board": cfg.board, "window_start": since, "pages_checked": res["pages_checked"],
        "pages_not_20": res["pages_not_20"], "missing_ids": res["missing_ids"],
        "out_of_order_pairs": res["out_of_order_pairs"], "nrec_checked": res["nrec_checked"],
        "nrec_agree_ratio": res["nrec_agree_ratio"], "ok": res["ok"],
        "details": json.dumps(res["details"], ensure_ascii=False)}])
    set_gauges(m, res, now)
    m.bq_bytes.labels("verify").inc(max(0, wh.bytes_processed - bytes0))
    m.push()
    print(json.dumps({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": "verify", **res},
                     ensure_ascii=False, default=str), flush=True)
    return res
