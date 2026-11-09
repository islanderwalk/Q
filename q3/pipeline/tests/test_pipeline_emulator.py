"""端到端：模擬的 PTT 八卦板 → 抓取 → BigQuery 模擬器，含自動回補與自動 rollback 的故障注入。

需要 BigQuery 模擬器（docker compose up -d bigquery，或 Q3_TEST_BQ_ENDPOINT 指到別處）；沒有就跳過。
"""
import dataclasses
import json
from datetime import timedelta

import pytest

from q3pipe import job, search, verify
from q3pipe.bq import POST_NAMES
from q3pipe.metrics import Metrics


def posts_by_aid(ctx):
    rows = ctx.wh.q(f"""SELECT aid, status, deleted_at, content, comment_count, fetch_count, last_fetched_at,
                               TO_JSON_STRING(comments) AS cj FROM {ctx.wh.t('posts')}""")
    return {r.aid: {**dict(r.items()), "comments": json.loads(r.cj or "[]")} for r in rows}


def snapshot(ctx):
    cols = [n for n in POST_NAMES if n != "comments"]
    rows = ctx.wh.q(f"SELECT {', '.join(cols)}, TO_JSON_STRING(comments) AS cj FROM {ctx.wh.t('posts')} ORDER BY aid")
    return [tuple(r.values()) for r in rows]


def in_window(board, now):
    since = (now - timedelta(days=7)).timestamp()
    return {p["aid"] for p in board.posts if not p["deleted"] and p["ts"] >= since}


def stored_in_window(ctx, now):
    """posts 裡發文在 7 天內的文章（更早的文章還在表裡，只是不在搜尋範圍）。"""
    rows = ctx.wh.q(f"SELECT aid FROM {ctx.wh.t('posts')} WHERE published_at >= @since", since=now - timedelta(days=7))
    return {r.aid for r in rows}


def test_backfill_hourly_refresh_delete_and_search(pipe):
    b, clock = pipe.board, pipe.clock
    b.seed(days=8, per_day=6)
    placeholder = b.posts[20]["aid"]
    b.delete(placeholder)  # 第一次看到就已經是刪文佔位
    ctx = pipe.make()

    r1 = job.run(ctx)  # 第一次：從最新頁往回讀到 7 天前，整段回補
    assert r1["status"] == "ok" and r1["list_complete"] and r1["rolled_back_batches"] == 0
    got = posts_by_aid(ctx)
    assert set(got) == in_window(b, clock.now()) and placeholder not in got
    assert all(p["status"] == 200 and p["comment_count"] == len(p["comments"]) for p in got.values())
    v = verify.run(ctx)
    assert v["ok"] and v["pages_checked"] >= 2 and v["missing_ids"] == 0

    clock.advance(hours=1)  # 一小時後：只讀新的頁，新文入庫，還不到重抓
    for i in range(5):
        b.add_post(clock.now() - timedelta(minutes=50 - 10 * i), n_pushes=2)
    index_hits = b.hits["index"]
    r2 = job.run(ctx)
    assert r2["new"] == 5 and r2["refreshed"] == 0
    assert b.hits["index"] - index_hits <= 3  # 最新頁＋上一輪的最新頁（可能剛填滿），不是整段重讀

    clock.advance(hours=23, minutes=30)  # 一天後：重抓 7 天內的文章
    target = b.posts[-12]
    for _ in range(3):
        b.add_push(target["aid"], content="新留言 颱風")
    gone = b.posts[-20]
    b.delete(gone["aid"])
    old_first_seen = {c["floor"]: c["first_seen_at"] for c in posts_by_aid(ctx)[target["aid"]]["comments"]}
    r3 = job.run(ctx)
    assert r3["status"] == "ok" and r3["refreshed"] > 0 and r3["deleted"] == 1
    got = posts_by_aid(ctx)
    assert got[gone["aid"]]["status"] == 404 and got[gone["aid"]]["deleted_at"] is not None
    assert got[gone["aid"]]["content"]  # 刪文只標記，舊內容保留
    tc = got[target["aid"]]["comments"]
    assert sum(c["content"] == "新留言 颱風" for c in tc) == 3
    assert all(c["first_seen_at"] == old_first_seen[c["floor"]] for c in tc if c["floor"] in old_first_seen)
    assert verify.run(ctx)["ok"]

    res = search.do_search(ctx.wh, ctx.cfg, "颱風", 7, 50, now=clock.now())
    hit = {p["aid"]: p for p in res["results"]}
    assert target["aid"] in hit and "comments" in hit[target["aid"]]["matched_in"]
    assert gone["aid"] not in hit  # 刪文不出現在搜尋結果
    assert res["post_hits"] >= 1


def test_restart_restores_dashboard_numbers(pipe):
    """程序重啟後指標從 0 開始；先從 BigQuery 補回上一輪、上次不變量檢查、7 天內的數字，看板不會歸零。"""
    pipe.board.seed(days=8, per_day=6)
    ctx = pipe.make()
    r = job.run(ctx)
    v = verify.run(ctx)
    fresh = Metrics(ctx.cfg)
    job.restore_gauges(dataclasses.replace(ctx, metrics=fresh))
    get = fresh.reg.get_sample_value
    assert get("q3_run_status", {"status": r["status"]}) == 1
    assert get("q3_run_last_finished_timestamp_seconds") > 0
    assert get("q3_verify_ok") == 1 and v["ok"]
    assert get("q3_verify", {"check": "pages_checked"}) == v["pages_checked"]
    assert get("q3_verify_last_run_timestamp_seconds") > 0
    assert get("q3_window_posts") == r["new"] and get("q3_window_comments") > 0


def test_downtime_is_backfilled_by_next_run(pipe):
    b, clock = pipe.board, pipe.clock
    b.seed(days=8, per_day=6)
    ctx = pipe.make()
    job.run(ctx)
    clock.advance(hours=30)  # 停機 30 小時，期間發了 3 頁多的文章
    for i in range(70):
        b.add_post(clock.now() - timedelta(hours=29) + timedelta(minutes=24 * i), n_pushes=1)
    r = job.run(ctx)
    assert r["status"] == "ok" and r["new"] == 70
    assert stored_in_window(ctx, clock.now()) == in_window(b, clock.now())
    st = ctx.wh.window_stats(ctx.cfg.board, clock.now(), 7)
    assert st["unsearchable"] == 0 and st["staleness_max_h"] < 1
    assert verify.run(ctx)["ok"]


def test_failed_batch_is_rolled_back_and_refetched_next_run(pipe):
    b, clock = pipe.board, pipe.clock
    b.seed(days=8, per_day=6)
    ctx = pipe.make()
    ctx.inject.update(fail_after="merge", fail_batches={2})  # 第 2 批 MERGE 完才出錯
    r1 = job.run(ctx)
    assert r1["status"] == "partial" and r1["rolled_back_batches"] == 1
    assert ctx.wh.batch_states(r1["run_id"])[2] == "rolled_back"
    missing = in_window(b, clock.now()) - set(posts_by_aid(ctx))
    assert len(missing) == ctx.cfg.batch_size  # 那一批整批不在，沒有半套
    ctx.inject.clear()
    r2 = job.run(ctx)
    assert r2["new"] == len(missing)
    assert set(posts_by_aid(ctx)) == in_window(b, clock.now()) and verify.run(ctx)["ok"]

    clock.advance(hours=24)  # 重抓那一批出錯：已存在的文章要回到寫入前的樣子（含留言）
    for p in b.posts:
        b.add_push(p["aid"])
    before = snapshot(ctx)
    ctx.inject.update(fail_after="merge", fail_batches={1})
    r3 = job.run(ctx)
    assert r3["rolled_back_batches"] == 1
    after = {row[0]: row for row in snapshot(ctx)}
    first_batch = sorted(ctx.wh.q(f"SELECT aid FROM {ctx.wh.t('post_versions')} WHERE run_id = @r AND batch_no = 1",
                                  r=r3["run_id"]), key=lambda x: x.aid)
    olds = {row[0]: row for row in before}
    assert first_batch and all(after[x.aid] == olds[x.aid] for x in first_batch)


def test_interrupted_batch_is_restored_before_next_run(pipe):
    b, clock = pipe.board, pipe.clock
    b.seed(days=8, per_day=6)
    ctx = pipe.make()
    real_log = ctx.wh.log_batch

    def killed(run_id, batch_no, state, *args, **kw):
        if state == "committed" and batch_no == 2:
            raise KeyboardInterrupt("程序在第 2 批寫完、記錄結局之前被殺")
        return real_log(run_id, batch_no, state, *args, **kw)

    ctx.wh.log_batch = killed
    with pytest.raises(KeyboardInterrupt):
        job.run(ctx)
    ctx.wh.log_batch = real_log
    r = job.run(ctx)  # 開始前先還原沒有結局的第 2 批，接著補抓
    assert r["status"] == "ok"
    assert set(posts_by_aid(ctx)) == in_window(b, clock.now()) and verify.run(ctx)["ok"]
    states = ctx.wh.q(f"SELECT note FROM {ctx.wh.t('batch_log')} WHERE state = 'rolled_back'")
    assert any("中途中斷" in (x.note or "") for x in states)


def test_layout_change_is_blocked_then_probe_resumes(pipe):
    b, clock = pipe.board, pipe.clock
    b.seed(days=8, per_day=6)
    ctx = pipe.make()
    job.run(ctx)
    clock.advance(hours=1)
    fresh = [b.add_post(clock.now() - timedelta(minutes=55 - 2 * i), n_pushes=1)["aid"] for i in range(25)]
    b.layout_changed = True  # 網站改版：文章頁找不到 #main-content
    r2 = job.run(ctx)
    assert r2["status"] == "gate_blocked" and r2["new"] == 0
    assert not set(fresh) & set(posts_by_aid(ctx))  # 一篇都沒寫進去
    b.layout_changed = False
    clock.advance(hours=1)
    r3 = job.run(ctx)  # 上一輪被擋：先用一小批試探
    assert r3["status"] == "ok" and r3["new"] == ctx.cfg.gate_min_batch
    r4 = job.run(ctx)
    assert r4["new"] == len(fresh) - ctx.cfg.gate_min_batch
    assert set(fresh) <= set(posts_by_aid(ctx))


def test_degraded_run_is_rolled_back_entirely(pipe):
    b, clock = pipe.board, pipe.clock
    b.seed(days=8, per_day=6)
    # 每批 404 比例放寬到 60% 讓每一批都過得了關，看整輪回歸檢查能不能抓到
    ctx = pipe.make(gate_max_404=0.6, run_max_deleted_increase=3, run_max_deleted_ratio=0.05)
    job.run(ctx)
    before = snapshot(ctx)
    clock.advance(hours=24)
    for p in b.posts:
        b.add_push(p["aid"])
    b.gone = {p["aid"] for p in b.posts[::3]}  # 三分之一回 404，列表沒變：被擋或網站異常，不是刪文
    r2 = job.run(ctx)
    assert r2["status"] == "rolled_back" and "刪文" in r2["note"]
    assert snapshot(ctx) == before  # 文章、留言、抓取時間全部回到這輪開始前
    b.gone = set()
    r3 = job.run(ctx)  # 試探一小批重抓，正常就恢復
    assert r3["status"] == "ok" and r3["refreshed"] == ctx.cfg.gate_min_batch


def test_regression_drill_rolls_back_one_run_only(pipe):
    """Q3_DRILL_REGRESSION：啟動後那一輪結束時當成退步、整輪還原；只作用一輪，之後試探一小批就恢復。"""
    b, clock = pipe.board, pipe.clock
    b.seed(days=2, per_day=6)
    ctx = pipe.make()
    assert job.run(ctx)["status"] == "ok"
    before = snapshot(ctx)
    clock.advance(hours=1)
    for k in range(4):
        b.add_post(clock.now() - timedelta(minutes=10 * (k + 1)))
    ctx.inject["regression"] = True
    r2 = job.run(ctx)
    assert r2["status"] == "rolled_back" and "故障演練" in r2["note"]
    assert snapshot(ctx) == before            # 這輪新增的文章全部還原
    assert "regression" not in ctx.inject     # 只作用一輪
    r3 = job.run(ctx)
    assert r3["status"] == "ok" and r3["new"] == 4    # 被還原的 4 篇補回來
