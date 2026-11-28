"""每日不變量檢查（瓶頸 4）。

不變量（唯一決定通過與否的條件）：
  從 PTT 列表最新頁沿著「上頁」連結一路往回走，走到出現早於 7 天前的文章為止。
  途中每一頁都要讀取成功，而且走完時最新頁沒有變。
  這次走訪看到的每一個未刪除文章 ID，都必須在 posts 裡有一筆抓取成功（status 200）的文章。

  - 走訪是 verify 自己另外對 PTT 讀一次，不用爬蟲存下來的 list_slots：爬蟲沒讀到的頁，這裡一樣看得到。
  - 「走到早於 7 天前的文章為止」：只讀一頁就停、或中途斷掉，都算不成立。
  - 「最新頁沒有變」：走訪前後各讀一次 index.html；不同代表走訪途中 PTT 重新編號，這次走訪不算數。
  - 只要求「爬蟲最近一輪開始前就已發文」的文章（截止點 cutoff），之後才發的文下一輪才會抓。

輔助數字（不影響通過與否）：走訪中不是 20 格的頁、相鄰文章 ID 時間戳倒序的對數、
列表推文數和文章內「推−噓」換算值相同的比例（只比列表快照與文章抓取時間相差 30 分鐘內的）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .ptt import PER_PAGE, nrec_of, parse_index


# ---------- 輔助：爬蟲存下來的列表格子（舊版檢查，保留給推文數比例與單元測試） ----------
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


# ---------- 不變量：另外走一次 PTT 列表 ----------
def _read(fetcher, url, newest):
    """讀一頁列表，回傳 (格子, 上頁連結頁碼)；讀不到或不是列表頁回傳 None。"""
    r = fetcher.get(url, "verify_list")
    if r.status != 200:
        return None
    try:
        return parse_index(r.text, newest=newest)
    except ValueError:
        return None


def _head_no(prev):
    return prev + 1 if prev is not None else 1


def walk_list(fetcher, cfg, since_ts, max_pages=5000):
    """從最新頁（index.html）沿每一頁的「上頁」連結往回走，直到某一頁出現早於 since_ts 的文章。
    回傳 {"head_start", "head_end", "pages": {頁碼: [格子...]}, "error_pages": [...], "reached_since": bool}。"""
    base = f"{cfg.base_url}/bbs/{cfg.board}"
    walk = {"head_start": None, "head_end": None, "pages": {}, "error_pages": [], "reached_since": False}
    got = _read(fetcher, f"{base}/index.html", newest=True)
    if got is None:
        walk["error_pages"].append("index")
        return walk
    entries, link = got
    page = walk["head_start"] = _head_no(link)
    walk["pages"][page] = entries
    while True:
        if any(e["ts"] is not None and e["ts"] < since_ts for e in walk["pages"][page]):
            walk["reached_since"] = True
            break
        if link is None:
            walk["reached_since"] = page == 1  # 第 1 頁沒有上頁，再往前沒有文章；別頁沒有上頁連結就是讀壞了
            if page != 1:
                walk["error_pages"].append(page)
            break
        if link != page - 1:  # 上頁連結必須正好指向前一頁，否則頁碼在走訪途中變了
            walk["error_pages"].append(page)
            break
        if len(walk["pages"]) >= max_pages:
            break
        got = _read(fetcher, f"{base}/index{link}.html", newest=False)
        if got is None:
            walk["error_pages"].append(link)
            break
        page, (entries, link) = link, got
        walk["pages"][page] = entries
    got = _read(fetcher, f"{base}/index.html", newest=True)
    walk["head_end"] = _head_no(got[1]) if got else None
    return walk


def check_walk(walk, posts_status, since_ts, cutoff_ts):
    """純函式。posts_status：{aid: status}（posts 表裡有的文章）。回傳不變量的結果。"""
    seen = {}
    for p, entries in walk["pages"].items():
        for e in entries:
            if e["aid"] and not e["deleted"]:
                seen[e["aid"]] = e["ts"]
    due = sorted(a for a, ts in seen.items() if ts is not None and since_ts <= ts < cutoff_ts)
    missing = [a for a in due if a not in posts_status]
    not_ok = [a for a in due if a in posts_status and posts_status[a] != 200]
    walk_complete = bool(walk["reached_since"]) and not walk["error_pages"] and walk["head_start"] is not None
    head_unchanged = walk["head_start"] is not None and walk["head_start"] == walk["head_end"]
    ok = walk_complete and head_unchanged and not missing and not not_ok

    # 輔助數字：走訪中不是 20 格的頁（最新頁、走到的最舊頁不算）、時間戳倒序
    pages_sorted = sorted(walk["pages"])
    inner = [p for p in pages_sorted if p != walk["head_start"] and p != (pages_sorted[0] if pages_sorted else None)]
    not_20 = [p for p in inner if len(walk["pages"][p]) != PER_PAGE]
    seq = [e["ts"] for p in pages_sorted for e in walk["pages"][p] if e["ts"] is not None]
    inversions = sum(1 for a, b in zip(seq, seq[1:]) if b < a)
    return {
        "ok": ok, "walk_complete": walk_complete, "head_unchanged": head_unchanged,
        "pages_checked": len(walk["pages"]), "walk_error_pages": len(walk["error_pages"]),
        "seen_ids": len(seen), "due_ids": len(due), "missing_ids": len(missing) + len(not_ok),
        "pages_not_20": len(not_20), "out_of_order_pairs": inversions,
        "details": {
            "head_start": walk["head_start"], "head_end": walk["head_end"], "reached_since": walk["reached_since"],
            "error_pages": walk["error_pages"][:20], "missing": missing[:20], "not_200": not_ok[:20],
            "page_range": [pages_sorted[0], pages_sorted[-1]] if pages_sorted else None,
            "since": datetime.fromtimestamp(since_ts, timezone.utc).isoformat(),
            "cutoff": datetime.fromtimestamp(cutoff_ts, timezone.utc).isoformat(),
        },
    }


def set_gauges(m, res, at):
    for k in ("pages_checked", "pages_not_20", "missing_ids", "out_of_order_pairs", "nrec_checked",
              "walk_error_pages", "due_ids"):
        if res.get(k) is not None:
            m.verify.labels(k).set(res[k])
    for k in ("walk_complete", "head_unchanged"):
        if res.get(k) is not None:
            m.verify.labels(k).set(1 if res[k] else 0)
    m.verify.labels("nrec_agree_ratio").set(res.get("nrec_agree_ratio") or 0)
    m.verify_ok.set(1 if res["ok"] else 0)
    m.verify_last.set(at.timestamp())


def run(ctx, until=None):
    """until：手動檢查時可把截止點往前拉（例如只驗已經完整抓完的一段）；預設是爬蟲最近一輪的開始時間。"""
    cfg, wh, m = ctx.cfg, ctx.wh, ctx.metrics
    now = ctx.now()
    since = now - timedelta(days=cfg.window_days)
    since_ts = int(since.timestamp())
    last = wh.last_run(cfg.board)
    cutoff = last["started_at"] if last and last.get("started_at") else now
    if until is not None:
        cutoff = min(cutoff, until)
    cutoff_ts = int(cutoff.timestamp())
    bytes0 = wh.bytes_processed

    walk = walk_list(ctx.fetcher, cfg, since_ts)
    posts_status = {r.aid: r.status for r in wh.q(
        f"SELECT aid, status FROM {wh.t('posts')} WHERE board = @b AND published_at >= @since",
        b=cfg.board, since=since - timedelta(days=1))}
    res = check_walk(walk, posts_status, since_ts, cutoff_ts)
    aux = check(wh.verify_rows(cfg.board, since_ts), since_ts)
    res["nrec_checked"], res["nrec_agree_ratio"] = aux["nrec_checked"], aux["nrec_agree_ratio"]

    wh.insert_rows("verify_runs", [{
        "run_at": now, "board": cfg.board, "window_start": since, "pages_checked": res["pages_checked"],
        "pages_not_20": res["pages_not_20"], "missing_ids": res["missing_ids"],
        "out_of_order_pairs": res["out_of_order_pairs"], "nrec_checked": res["nrec_checked"],
        "nrec_agree_ratio": res["nrec_agree_ratio"], "ok": res["ok"],
        "details": json.dumps({**res["details"], "walk_complete": res["walk_complete"],
                               "head_unchanged": res["head_unchanged"], "due_ids": res["due_ids"],
                               "seen_ids": res["seen_ids"], "walk_error_pages": res["walk_error_pages"]},
                              ensure_ascii=False)}])
    set_gauges(m, res, now)
    m.bq_bytes.labels("verify").inc(max(0, wh.bytes_processed - bytes0))
    m.push()
    print(json.dumps({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": "verify", **res},
                     ensure_ascii=False, default=str), flush=True)
    return res
