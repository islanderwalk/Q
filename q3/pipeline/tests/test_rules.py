"""品質關卡、整輪回歸檢查、不變量、排程的規則（不需要模擬器）。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from q3pipe import bq, job, scheduler, search, verify
from q3pipe.config import Config
from q3pipe.ptt import TW, nrec_of, parse_nrec

CFG = Config()


def flags(n200=0, n404=0, fail=0, empty=0, refresh=0, shrink=0):
    out = [{"status": 404} for _ in range(n404)]
    out += [{"status": 200, "parse_fail": True} for _ in range(fail)]
    for i in range(n200 - fail):
        out.append({"status": 200, "empty": i < empty, "refresh": i < refresh, "shrink": i < shrink})
    return out


def test_gate_passes_normal_batch():
    assert job.gate(CFG, flags(n200=48, n404=2, refresh=40, shrink=1)) is None


def test_gate_blocks_layout_change_empty_pages_mass_404_and_shrink():
    assert "解析失敗" in job.gate(CFG, flags(n200=50, fail=5))
    assert "空白" in job.gate(CFG, flags(n200=50, empty=15))
    assert "404" in job.gate(CFG, flags(n200=35, n404=15))
    assert "縮水" in job.gate(CFG, flags(n200=50, refresh=50, shrink=8))


def test_gate_small_batch_uses_absolute_counts():
    assert job.gate(CFG, flags(n200=3, n404=1)) is None          # 一小批裡 1 篇 404 是正常的
    assert job.gate(CFG, flags(n200=4, fail=3)) is not None
    assert job.gate(CFG, flags(n404=5)) is not None


def stats(searchable=6000, empty=0, deleted=10, comment_sum=200000):
    return {"searchable": searchable, "empty": empty, "deleted": deleted, "comment_sum": comment_sum}


def run_state(new=40, refreshed=400, deleted=2):
    return SimpleNamespace(new=new, refreshed=refreshed, deleted=deleted)


def test_regression_ok_for_normal_hour():
    assert job.regression(CFG, stats(), stats(searchable=6038, deleted=12, comment_sum=203000), run_state()) is None


def test_regression_catches_slow_degradation():
    st = run_state()
    assert "可搜尋" in job.regression(CFG, stats(), stats(searchable=5600), st)
    assert "空白" in job.regression(CFG, stats(), stats(empty=60), st)
    assert "刪文" in job.regression(CFG, stats(), stats(deleted=90), st)
    assert "留言" in job.regression(CFG, stats(), stats(comment_sum=180000), st)


def test_nrec_display_rules():
    assert [parse_nrec(x) for x in ("", "爆", "X1", "XX", "37")] == [0, 100, -10, -100, 37]
    assert [nrec_of(p, b) for p, b in ((5, 2), (150, 1), (1, 30), (0, 3))] == [3, 100, -20, 0]


def row(page, slot, aid=None, ts=None, deleted=False, in_posts=True, nrec=0, push=0, boo=0):
    return {"page_no": page, "slot": slot, "aid": aid, "ts": ts, "deleted": deleted, "nrec": nrec, "in_posts": in_posts,
            "status": 200 if in_posts and aid else None, "push": push if in_posts and aid else None, "boo": boo}


def full_page(page, start_ts, deleted_slot=None):
    out = []
    for s in range(20):
        if s == deleted_slot:
            out.append(row(page, s, deleted=True, in_posts=False))
        else:
            out.append(row(page, s, f"M.{start_ts + s}.A.ABC", start_ts + s))
    return out


def test_invariant_pass_and_each_failure_mode():
    rows = full_page(10, 1000, deleted_slot=3) + full_page(11, 1100)
    assert verify.check(rows, 0)["ok"]
    short = rows[:-1]
    assert verify.check(short, 0)["pages_not_20"] == 1
    missing = [dict(r) for r in rows]
    missing[5]["in_posts"] = False
    assert verify.check(missing, 0)["missing_ids"] == 1
    assert verify.check(missing, 10**9)["missing_ids"] == 0  # 視窗起點之前的文不算遺漏
    swapped = [dict(r) for r in rows]
    swapped[25]["ts"] = 900
    assert verify.check(swapped, 0)["out_of_order_pairs"] == 1


def test_schedule_and_daily_verify():
    cfg = Config(schedule_minute=5)
    now = datetime(2026, 11, 3, 12, 30, tzinfo=timezone.utc)
    assert scheduler.next_run_at(now, cfg) == datetime(2026, 11, 3, 13, 5, tzinfo=timezone.utc)
    assert scheduler.next_run_at(now.replace(minute=2), cfg) == datetime(2026, 11, 3, 12, 5, tzinfo=timezone.utc)
    assert scheduler.verify_due(cfg, None, now)  # 從沒檢查過：先跑一次
    last = datetime(2026, 11, 3, 4, 10, tzinfo=TW)
    assert not scheduler.verify_due(cfg, last, datetime(2026, 11, 3, 23, 0, tzinfo=TW))
    assert not scheduler.verify_due(cfg, last, datetime(2026, 11, 4, 3, 0, tzinfo=TW))
    assert scheduler.verify_due(cfg, last, datetime(2026, 11, 4, 4, 5, tzinfo=TW))


def test_single_worker_waits_for_running_round(tmp_path):
    import threading
    lock, order = tmp_path / "run.lock", []

    def second():
        with scheduler.single_worker(str(lock)):
            order.append("second")

    with scheduler.single_worker(str(lock)):
        t = threading.Thread(target=second)
        t.start()
        t.join(0.3)
        assert t.is_alive()  # 前一輪還在跑：等
        order.append("first")
    t.join(5)
    assert order == ["first", "second"]


class _Counter:
    def labels(self, *a):
        return self

    def inc(self, *a):
        pass


def test_run_stops_after_two_failed_writes_in_a_row():
    class DownWh:
        def commit_batch(self, *a, **k):
            raise ConnectionError("資料庫斷線")

    ctx = SimpleNamespace(cfg=CFG, wh=DownWh(), metrics=SimpleNamespace(batches=_Counter()), inject={}, now=None)
    run = job.RunState(id="r", started=datetime(2026, 11, 3, tzinfo=timezone.utc))
    batch = [({"aid": "A"}, {"aid": "A", "status": 200, "comments": []}, {"status": 200})]
    job.commit(ctx, run, batch, "article_new")  # 第一批失敗：還原、記下來、繼續
    assert run.rolled_back == 1
    try:
        job.commit(ctx, run, batch, "article_new")
        raise AssertionError("連續第二批失敗應該停下這一輪")
    except job.WriteFailing as e:
        assert "連續 2 批" in str(e)


def test_drill_config():
    assert scheduler.drill_batches(Config()) == set()
    assert scheduler.drill_batches(Config(drill_fail_batches="1, 3")) == {1, 3}


class FakeWh:
    bytes_processed = 0

    def search(self, board, kw, since, limit):
        t = datetime(2026, 11, 3, tzinfo=timezone.utc)
        post = {"aid": "A1", "title": "颱風假", "author": "u", "published_at": t, "url": "x", "push": 1, "boo": 0,
                "arrow": 0, "comment_count": 2, "snippet": "內文"}
        other = {**post, "aid": "A2", "title": "別的", "published_at": t - timedelta(hours=1)}
        comments = [{"aid": "A2", "floor": 0, "tag": "推", "user_id": "x", "content": "颱風", "comment_at": t},
                    {"aid": "A1", "floor": 1, "tag": "→", "user_id": "y", "content": "颱風來了", "comment_at": t}]
        return [post], [other], comments


def test_search_merges_post_and_comment_hits():
    res = search.do_search(FakeWh(), CFG, "颱風", 7, 10, now=datetime(2026, 11, 3, 1, tzinfo=timezone.utc))
    by = {p["aid"]: p for p in res["results"]}
    assert by["A1"]["matched_in"] == ["title", "comments"] and len(by["A1"]["matched_comments"]) == 1
    assert by["A2"]["matched_in"] == ["comments"]
    assert [p["aid"] for p in res["results"]] == ["A1", "A2"]  # 新的在前


def test_stream_insert_chunks_by_rows_and_bytes():
    small = [{"aid": f"A{i}", "title": "短"} for i in range(1200)]
    assert [len(c) for c in bq._chunks(small)] == [500, 500, 200]
    hot = [{"aid": f"H{i}", "comments": [{"content": "颱風" * 20}] * 2000} for i in range(6)]  # 每篇約 0.5 MB
    chunks = list(bq._chunks(hot, max_bytes=2_000_000))
    assert sum(len(c) for c in chunks) == 6 and len(chunks) > 1
    assert all(sum(len(bq.json.dumps(r)) for r in c) <= 2_000_000 for c in chunks)
    huge = [{"aid": "X", "content": "x" * 3_000_000}]
    assert [len(c) for c in bq._chunks(huge)] == [1]  # 單篇超過上限就自己一個請求


def test_nrec_compares_only_snapshots_taken_close_together():
    rows = full_page(10, 1000)
    for r in rows:
        r.update(push=5, boo=0, nrec=5, same_time=True)
    rows[0].update(push=9, same_time=False)  # 列表是昨天的快照，文章今天重抓過推文變多：不拿來比
    res = verify.check(rows, 0)
    assert res["nrec_checked"] == 19 and res["nrec_agree_ratio"] == 1.0 and res["ok"]
    rows[1].update(push=9)  # 同一時間抓的卻對不上：解析可能有問題
    assert verify.check(rows, 0)["nrec_agree_ratio"] < 1.0


def test_labelled_counters_start_at_zero():
    """帶標籤的計數器啟動時就有 0：第一次 rollback／重抓才算得進 increase()，故障演練的還原才看得到。"""
    from types import SimpleNamespace
    from q3pipe.metrics import Metrics
    m = Metrics(SimpleNamespace())
    val = m.reg.get_sample_value
    for res in ("committed", "rolled_back", "gate_blocked"):
        assert val("q3_batches_total", {"result": res}) == 0
    assert val("q3_run_rollbacks_total") == 0
    for age in map(str, range(8)):
        assert val("q3_posts_refreshed_total", {"age_day": age}) == 0
        assert val("q3_comments_new_total", {"fetch": "refresh", "age_day": age}) == 0
    assert val("q3_posts_deleted_total", {"when": "refresh"}) == 0
    assert val("q3_http_requests_total", {"kind": "article_new", "status": "error"}) == 0
