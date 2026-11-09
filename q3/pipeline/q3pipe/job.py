"""一輪抓取（答案裡的 Cloud Run Job「crawl」）：讀列表找新文 → 抓新文 → 重抓到期的文章。

自動 backfill：
  - 列表從最新頁往回讀到上一輪的最新頁（停機多久都一樣），第一次則讀到 7 天前為止；
    中途斷掉的話，沒讀到的頁下一輪會補讀。
  - 「列表上有、posts 沒有」的文章每輪都會補抓；重抓失敗的文章 last_fetched_at 不變，下一輪還會到期。
自動 rollback：
  - 每批寫入前先過品質檢查（解析失敗、空白、404 比例、留言數縮水），不過就整批不寫並停下這一輪，
    下一輪先用一小批試探，過了才恢復正常。
  - 每批寫入失敗就還原那一批（連續兩批失敗就先停這輪，不浪費請求）；整輪結束後做回歸檢查（可搜尋文章大減、空白暴增、刪文暴增、留言總數下降），
    不過就把這一輪全部 rollback。
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import verify
from .bq import comment_key
from .ptt import aid_ts, parse_article, parse_index
from .rawstore import raw_key


def utcnow():
    return datetime.now(timezone.utc)


def log(event, **kw):
    print(json.dumps({"t": utcnow().isoformat(timespec="seconds"), "event": event, **kw},
                     ensure_ascii=False, default=str), flush=True)


class GateBlocked(Exception):
    pass


class WriteFailing(Exception):
    """連續幾批都寫不進去（例如資料庫斷線）：這輪先停，不要繼續抓了又寫不進去。"""


@dataclass
class Ctx:
    cfg: object
    wh: object
    store: object
    fetcher: object
    metrics: object
    now: object = utcnow
    parse: object = parse_article          # 測試可以換掉，模擬網站改版
    inject: dict = field(default_factory=dict)
    stopping: threading.Event = field(default_factory=threading.Event)


@dataclass
class RunState:
    id: str
    started: datetime
    batch_no: int = 0
    list_pages: int = 0
    new: int = 0
    refreshed: int = 0
    deleted: int = 0
    failures: int = 0
    batches: int = 0
    rolled_back: int = 0
    gate_blocked: int = 0
    write_fail_streak: int = 0


# ---------------- 列表 ----------------
def discover(ctx, run):
    cfg, wh, f = ctx.cfg, ctx.wh, ctx.fetcher
    now = ctx.now()
    since_ts = int((now - timedelta(days=cfg.window_days)).timestamp())
    stored = wh.page_summary(cfg.board)
    last_head = max(stored) if stored else None
    base = f"{cfg.base_url}/bbs/{cfg.board}"
    pending = []

    def keep(page_no, entries, full, resp):
        uri = ctx.store.put(raw_key(cfg.board, "index", str(page_no), now), resp.text.encode("utf-8"))
        for e in entries:
            pending.append({"board": cfg.board, "page_no": page_no, "slot": e["slot"], "aid": e["aid"], "ts": e["ts"],
                            "title": e["title"], "author": e["author"], "nrec": e["nrec"], "deleted": e["deleted"],
                            "url": e["url"], "page_full": full, "snapshot_at": now, "raw_uri": uri})
        ctx.metrics.list_pages.inc()
        run.list_pages += 1

    r = f.get(f"{base}/index.html", "list")
    if r.status != 200:
        raise RuntimeError(f"讀不到最新列表頁：{r.error or r.status}")
    entries, prev = parse_index(r.text, newest=True, base_url=cfg.base_url)
    head = (prev + 1) if prev else 1
    keep(head, entries, False, r)  # 最新頁還沒填滿，不算進不變量
    tss = [e["ts"] for e in entries if e["ts"]]
    reached = bool(tss) and min(tss) < since_ts
    p = head - 1
    while p >= 1 and not reached and run.list_pages < cfg.max_list_pages and not ctx.stopping.is_set():
        info = stored.get(p)
        # 沒讀過、上次還沒填滿、或上一輪之後才填滿的頁才要讀；其他頁用存下來的快照判斷到哪裡為止
        if info is None or not info["full"] or (last_head is not None and p >= last_head):
            r = f.get(f"{base}/index{p}.html", "list")
            if r.status != 200:
                log("list_page_failed", page=p, error=r.error or r.status)
                break  # 停在這頁，下一輪會補讀
            entries, _ = parse_index(r.text, newest=False, base_url=cfg.base_url)
            keep(p, entries, True, r)
            tss = [e["ts"] for e in entries if e["ts"]]
            min_ts = min(tss) if tss else None
        else:
            min_ts = info["min_ts"]
        if min_ts is not None and min_ts < since_ts:
            reached = True
        if len(pending) >= 400:
            wh.merge_slots(pending)
            pending = []
        p -= 1
    wh.merge_slots(pending)
    complete = reached or p < 1  # 讀到 7 天前（或看板第一頁）才算這輪列表完整
    return {"head": head, "last_head": last_head, "pages_read": run.list_pages, "list_complete": complete}


# ---------------- 文章 ----------------
def _author_id(text):
    return (text or "").split(" (", 1)[0].strip() or None


def build_rows(ctx, item, resp, now, old_keys=None):
    """一篇文章的回應 → (要寫入的 post 列, 品質旗標)。留言放在 post["comments"]。
    old_keys：重抓時這篇目前存著的留言 {識別: 第一次看到的時間}，用來算新增了幾則、保留第一次看到的時間。"""
    cfg = ctx.cfg
    aid = item["aid"]
    ts = aid_ts(aid)
    published = datetime.fromtimestamp(ts, timezone.utc)
    url = item.get("url") or f"{cfg.base_url}/bbs/{cfg.board}/{aid}.html"
    base = {"aid": aid, "board": cfg.board, "url": url, "published_at": published, "fetched_at": now}
    refresh = item.get("comment_count") is not None
    if resp.status == 404:
        return {**base, "title": item.get("title"), "author": item.get("author"), "status": 404, "comments": []}, \
            {"status": 404, "refresh": refresh}
    art = ctx.parse(resp.text, ts)
    if art is None:
        return None, {"status": 200, "parse_fail": True, "refresh": refresh}
    uri = ctx.store.put(raw_key(cfg.board, "article", aid, now), resp.text.encode("utf-8"))
    old_keys = old_keys or {}
    comments, new = [], 0
    for c in art["pushes"]:
        row = {"floor": c["floor"], "tag": c["tag"], "user_id": c["user"], "content": c["content"], "ip": c["ip"],
               "time_text": c["time_text"], "comment_at": c["comment_at"], "dup_n": c["dup_n"]}
        first = old_keys.get(comment_key(row))
        if first is None:
            new += 1
        row["first_seen_at"] = first or now
        comments.append(row)
    post = {**base, "title": art["title"] or item.get("title"), "author": item.get("author") or _author_id(art["author"]),
            "content": art["content"], "push": art["push"], "boo": art["boo"], "arrow": art["arrow"],
            "comment_count": art["comment_count"], "truncated": art["truncated"], "html_bytes": resp.nbytes,
            "raw_uri": uri, "status": 200, "comments": comments}
    old = item.get("comment_count")
    shrink = old is not None and old >= 10 and art["comment_count"] <= old / 2 and old - art["comment_count"] >= 10
    empty = not art["content"] and art["comment_count"] == 0
    return post, {"status": 200, "empty": empty, "shrink": shrink, "refresh": refresh, "new_comments": new}


def gate(cfg, flags):
    """寫入前的品質檢查。回傳不過的原因，過了回 None。"""
    n = len(flags)
    n200 = sum(f["status"] == 200 for f in flags)
    n404 = sum(f["status"] == 404 for f in flags)
    fail = sum(f.get("parse_fail", False) for f in flags)
    parsed = n200 - fail
    empty = sum(f.get("empty", False) for f in flags)
    refreshed = sum(f.get("refresh", False) and not f.get("parse_fail", False) and f["status"] == 200 for f in flags)
    shrink = sum(f.get("shrink", False) for f in flags)
    if n < cfg.gate_min_batch:  # 批次太小只看絕對數量
        if fail >= 3:
            return f"解析失敗 {fail} 篇"
        if n404 >= 5:
            return f"404 {n404} 篇"
        return None
    if n200 and fail / n200 > cfg.gate_max_parse_fail:
        return f"解析失敗 {fail}/{n200}"
    if parsed and empty / parsed > cfg.gate_max_empty:
        return f"內文與留言都空白 {empty}/{parsed}"
    if n404 / n > cfg.gate_max_404:
        return f"404 {n404}/{n}"
    if refreshed and shrink / refreshed > cfg.gate_max_shrink:
        return f"重抓後留言縮水 {shrink}/{refreshed}"
    return None


def commit(ctx, run, batch, kind, left=None):
    """batch：[(item, post, flags)]。先過品質檢查，再整批寫入；寫入失敗那批會被還原，下一輪自動補。"""
    cfg, m = ctx.cfg, ctx.metrics
    reason = gate(cfg, [b[2] for b in batch])
    if reason:
        m.batches.labels("gate_blocked").inc()
        run.gate_blocked += 1
        log("gate_blocked", run_id=run.id, kind=kind, reason=reason, size=len(batch))
        raise GateBlocked(reason)
    by_aid = {b[1]["aid"]: b for b in batch if b[1] is not None}  # 同一批同一篇只留最後一次（MERGE 來源不能重複）
    batch = list(by_aid.values())
    if not batch:
        return
    run.batch_no += 1
    run.batches += 1
    fail_after = ctx.inject.get("fail_after") if run.batch_no in ctx.inject.get("fail_batches", ()) else None
    try:
        ctx.wh.commit_batch(run.id, run.batch_no, [b[1] for b in batch], fail_after=fail_after)
    except Exception as e:
        m.batches.labels("rolled_back").inc()
        run.rolled_back += 1
        run.write_fail_streak += 1
        log("batch_rolled_back", run_id=run.id, batch=run.batch_no, kind=kind, error=str(e)[:300])
        if run.write_fail_streak >= 2:
            raise WriteFailing(f"連續 {run.write_fail_streak} 批寫入失敗，這輪先停：{str(e)[:200]}") from e
        return
    run.write_fail_streak = 0
    m.batches.labels("committed").inc()
    now = ctx.now()
    for item, post, flags in batch:
        age = str(min(7, max(0, int((now - post["published_at"]).total_seconds() // 86400))))
        if kind == "article_new":
            if post["status"] == 200:
                run.new += 1
                m.posts_new.inc()
                m.comments_new.labels("first", age).inc(len(post["comments"]))
                m.ingest_lag.observe((post["fetched_at"] - post["published_at"]).total_seconds() / 60)
            else:
                run.deleted += 1
                m.posts_deleted.labels("first").inc()
        elif post["status"] == 404:
            run.deleted += 1
            m.posts_deleted.labels("refresh").inc()
        else:
            run.refreshed += 1
            m.posts_refreshed.labels(age).inc()
            m.comments_new.labels("refresh", age).inc(flags.get("new_comments", 0))
    log("batch_committed", run_id=run.id, batch=run.batch_no, kind=kind, posts=len(batch),
        comments=sum(len(b[1]["comments"]) for b in batch), left=left)


def process(ctx, run, items, kind, backlog_gauge):
    """依序抓 items，每 batch_size 篇寫一次。重抓時先一次讀出這批文章目前的留言，好算新增了哪些。"""
    cfg, f, m = ctx.cfg, ctx.fetcher, ctx.metrics
    left = len(items)
    backlog_gauge.set(left)
    for i in range(0, len(items), cfg.batch_size):
        if ctx.stopping.is_set():
            break
        chunk = items[i:i + cfg.batch_size]
        keys = ctx.wh.comment_keys([it["aid"] for it in chunk]) if kind == "article_refresh" else {}
        batch = []
        for item in chunk:
            if ctx.stopping.is_set():
                break
            now = ctx.now()
            url = item.get("url") or f"{cfg.base_url}/bbs/{cfg.board}/{item['aid']}.html"
            resp = f.get(url, kind)
            left -= 1
            backlog_gauge.set(left)
            if resp.status not in (200, 404):
                run.failures += 1
                m.fetch_failures.labels(kind).inc()
                log("fetch_failed", kind=kind, aid=item["aid"], error=resp.error)
                continue  # 試完仍失敗：這篇留給下一輪（新文還在 due_new、重抓的 last_fetched_at 沒變）
            post, flags = build_rows(ctx, item, resp, now, keys.get(item["aid"]))
            batch.append((item, post, flags))
        if batch:
            commit(ctx, run, batch, kind, left=left)
            if run.batches % 10 == 0:
                refresh_window_gauges(ctx)


# ---------------- 整輪 ----------------
def regression(cfg, before, after, run):
    """整輪結束後和這輪開始前比（同一個 7 天起點），回傳要 rollback 的原因。"""
    touched = run.new + run.refreshed + run.deleted
    if before["searchable"] >= 50:
        drop = (before["searchable"] - after["searchable"]) / before["searchable"]
        if drop > cfg.run_max_searchable_drop:
            return f"可搜尋文章少了 {drop:.1%}（{before['searchable']}→{after['searchable']}）"
    allowed_empty = max(cfg.run_max_empty_increase, 0.02 * touched)
    if after["empty"] - before["empty"] > allowed_empty:
        return f"內文空白的文章多了 {after['empty'] - before['empty']} 篇"
    allowed_deleted = max(cfg.run_max_deleted_increase, cfg.run_max_deleted_ratio * touched)
    if after["deleted"] - before["deleted"] > allowed_deleted:
        return f"刪文多了 {after['deleted'] - before['deleted']} 篇（這輪處理 {touched} 篇）"
    if before["comment_sum"] >= 1000 and after["comment_sum"] < before["comment_sum"] * (1 - cfg.run_max_comment_drop):
        return f"留言總數下降（{before['comment_sum']}→{after['comment_sum']}）"
    return None


def refresh_window_gauges(ctx):
    """7 天內的數字平常在每輪結束時更新；第一次回補要跑幾個小時，中途也更新，看板才看得到進度。"""
    try:
        update_window_gauges(ctx, ctx.wh.window_stats(ctx.cfg.board, ctx.now(), ctx.cfg.window_days))
    except Exception as e:  # 只影響看板上的數字，不影響這輪
        log("window_gauges_failed", error=repr(e)[:200])


def restore_gauges(ctx):
    """程序重啟後指標從 0 開始；先用 BigQuery 裡的紀錄補回「上一輪」「上次不變量檢查」「7 天內」，看板不會歸零到下一輪跑完。"""
    cfg, wh, m = ctx.cfg, ctx.wh, ctx.metrics
    last = wh.last_run(cfg.board)
    if last and last["finished_at"]:
        m.run_last_finished.set(last["finished_at"].timestamp())
        m.run_duration.set((last["finished_at"] - last["started_at"]).total_seconds())
        m.set_run_status(last["status"])
    v = wh.last_verify(cfg.board)
    if v:
        verify.set_gauges(m, v, v["run_at"])
    if last:
        refresh_window_gauges(ctx)


def update_window_gauges(ctx, stats):
    m = ctx.metrics
    m.window_posts.set(stats["searchable"])
    m.window_deleted.set(stats["deleted"])
    m.window_comments.set(stats["comments"])
    m.window_truncated.set(stats["truncated"])
    m.unsearchable_oldest.set(round(stats["unsearchable_oldest_min"], 1))
    m.staleness.set(round(stats["staleness_max_h"], 2))
    m.backlog_new.set(stats["unsearchable"])


def run(ctx):
    cfg, wh, m = ctx.cfg, ctx.wh, ctx.metrics
    started = ctx.now()
    st = RunState(id=uuid.uuid4().hex, started=started)
    since = started - timedelta(days=cfg.window_days)
    m.run_in_progress.set(1)
    m.push()
    status, note, disc = "ok", "", {}
    prev = wh.last_run_status(cfg.board)
    probe = prev in ("gate_blocked", "rolled_back")
    log("run_start", run_id=st.id, previous=prev, probe=probe)
    bytes0 = wh.bytes_processed
    try:
        recovered = wh.recover(exclude_run=st.id)  # 上一輪中途被殺留下的半套批次，先還原
        if recovered:
            log("recovered_interrupted_batches", run_id=st.id, batches=recovered)
        before = wh.window_stats(cfg.board, started, cfg.window_days, since=since)
        update_window_gauges(ctx, before)
        try:
            disc = discover(ctx, st)
            log("discovered", run_id=st.id, **disc)
            refresh_window_gauges(ctx)  # 列表讀完就更新「還不能搜尋的文章等了多久」
            new = wh.due_new(cfg.board, int(since.timestamp()))
            if probe:
                new = new[:cfg.gate_min_batch]  # 上一輪被擋或被還原：先用一小批試探
            process(ctx, st, new, "article_new", m.backlog_new)
            if not (probe and new):
                due = wh.due_refresh(cfg.board, ctx.now(), cfg.refresh_policy, cfg.refresh_after_hours, cfg.window_days)
                if probe:
                    due = due[:cfg.gate_min_batch]
                process(ctx, st, due, "article_refresh", m.backlog_refresh)
        except GateBlocked as e:
            status, note = "gate_blocked", str(e)  # 這批沒寫；前面已寫的批次照樣做回歸檢查
        except WriteFailing as e:
            status, note = "failed", str(e)  # 沒有結局的批次下一輪開始前會先還原，沒抓的下一輪補
        after = wh.window_stats(cfg.board, ctx.now(), cfg.window_days, since=since)
        reason = regression(cfg, before, after, st)
        if ctx.inject.pop("regression", None) and not reason:   # 演練只作用一輪
            reason = "故障演練：這一輪當成退步處理（Q3_DRILL_REGRESSION）"
        if reason:
            undone = wh.rollback_run(st.id)
            m.run_rollbacks.inc()
            status, note = "rolled_back", f"{reason}；已還原 {len(undone)} 批" + (f"（{note}）" if note else "")
            log("run_rolled_back", run_id=st.id, reason=reason, batches=len(undone))
        elif status == "ok" and (st.rolled_back or st.failures or not disc.get("list_complete", False)
                                 or ctx.stopping.is_set()):
            status = "partial"  # 沒抓完的部分下一輪自動補
        if status in ("ok", "partial"):
            wh.prune_slots(cfg.board, int((started - timedelta(days=cfg.prune_after_days)).timestamp()))
    except Exception as e:
        status, note = "failed", repr(e)[:300]
        log("run_failed", run_id=st.id, error=note)
    finished = ctx.now()
    try:
        wh.insert_rows("crawl_runs", [{
            "run_id": st.id, "board": cfg.board, "started_at": started, "finished_at": finished, "status": status,
            "list_pages": st.list_pages, "new_posts": st.new, "refreshed": st.refreshed, "deleted_found": st.deleted,
            "failures": st.failures, "requests": ctx.fetcher.requests, "batches": st.batches,
            "rolled_back_batches": st.rolled_back, "gate_blocked": st.gate_blocked, "note": note}])
        stats = wh.window_stats(cfg.board, finished, cfg.window_days)
        update_window_gauges(ctx, stats)
        m.backlog_refresh.set(len(wh.due_refresh(cfg.board, finished, cfg.refresh_policy, cfg.refresh_after_hours, cfg.window_days)))
    except Exception as e:
        log("run_record_failed", run_id=st.id, error=repr(e)[:200])
    m.bq_bytes.labels("crawler").inc(max(0, wh.bytes_processed - bytes0))
    m.run_in_progress.set(0)
    m.run_duration.set((finished - started).total_seconds())
    m.run_last_finished.set(finished.timestamp())
    m.set_run_status(status)
    m.push()
    summary = {"run_id": st.id, "status": status, "note": note, "list_pages": st.list_pages, "new": st.new,
               "refreshed": st.refreshed, "deleted": st.deleted, "failures": st.failures, "batches": st.batches,
               "rolled_back_batches": st.rolled_back, "seconds": round((finished - started).total_seconds(), 1), **disc}
    log("run_end", **summary)
    return summary
