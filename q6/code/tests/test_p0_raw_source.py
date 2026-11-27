"""回補 Job 原文來源：本機 file 與正式環境 BigQuery 吐一樣的東西；BigQuery 讀固定時間點、依 post_id 排序從 checkpoint 接著拉；
時間點過期就從頭重跑。不連 BigQuery／ES／PG（全用假物件）。"""
import datetime as dt
import json
import sys

import pytest

from _p0_fakes import SEG, FakeLedger, ensure_prometheus_client

ensure_prometheus_client()
if str(SEG) not in sys.path:
    sys.path.insert(0, str(SEG))
job = pytest.importorskip("qseg.job")
rawsrc = pytest.importorskip("qseg.rawsrc")

TABLE = "qsearch-prod.raw.posts"
NOW = dt.datetime.now(dt.timezone.utc)


class FakeJob:
    def __init__(self, rows):
        self.rows = rows

    def result(self, page_size=None):
        self.page_size = page_size
        return iter(self.rows)


class FakeBQ:
    """照 POSTS_SQL 的語意回資料：已去重、依 post_id 排序、只回 rn > @start 的那些。"""

    def __init__(self, posts, raw_lines=None, null_ids=0):
        self.posts = sorted(posts, key=lambda p: p["post_id"])
        self.raw_lines, self.null_ids, self.queries = raw_lines or len(posts), null_ids, []

    def query(self, sql, job_config=None):
        params = {p.name: p.value for p in job_config.query_parameters}
        self.queries.append((sql, params))
        if "COUNT(*)" in sql:
            return FakeJob([{"raw_lines": self.raw_lines, "raw_count": len(self.posts), "null_ids": self.null_ids}])
        start = params["start"]
        return FakeJob([dict(p, rn=k + 1) for k, p in enumerate(self.posts) if k + 1 > start])


def _posts(n):
    return [{"post_id": f"p{k:03d}", "channel": "ptt", "sub_type": "post", "source": "Gossiping", "url": f"u{k}",
             "published_at": "2026-10-05T10:00:00+08:00", "fetched_at": "2026-10-06T00:00:00+08:00", "author": "a", "text": f"t{k}"}
            for k in range(n)]


@pytest.fixture
def bq(monkeypatch):
    pytest.importorskip("google.cloud.bigquery")   # 只有 bigquery 測試要這個套件；file 的測試沒裝也照跑
    monkeypatch.setattr(job.config, "RAW_SOURCE", "bigquery")
    monkeypatch.setattr(job.config, "RAW_BQ_TABLE", TABLE)
    fake = FakeBQ(_posts(7))
    monkeypatch.setattr(rawsrc, "bq_client", lambda: fake)
    return fake


def test_file_source_dedupes_first_wins_and_resumes_from_start(tmp_path, monkeypatch):
    (tmp_path / "ptt").mkdir()
    rows = [{"post_id": "a", "text": "1"}, {"post_id": "b", "text": "2"}, {"post_id": "a", "text": "3"}, {"post_id": "c", "text": "4"}]
    (tmp_path / "ptt" / "2026-10.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", encoding="utf-8")
    monkeypatch.setattr(job.config, "RAW_ROOT", str(tmp_path))
    monkeypatch.setattr(job.config, "RAW_SOURCE", "file")
    src = rawsrc.open_partition("ptt", "2026-10")
    assert (src.raw_count, src.raw_lines) == (3, 4)
    assert [p["post_id"] for p in src.iter_posts()] == ["a", "b", "c"]
    assert [p["text"] for p in src.iter_posts(1)] == ["2", "4"]


def test_bigquery_query_pins_snapshot_and_streams_from_checkpoint(bq):
    # FakeBQ 自己不會去重：去重只能檢查 SQL 字面（真正的 QUALIFY 行為要在 BigQuery 上驗）
    src = rawsrc.open_partition("ptt", "2026-10", NOW, page_size=500)
    assert (src.raw_count, src.raw_lines) == (7, 7)
    got = list(src.iter_posts(3))
    assert [p["post_id"] for p in got] == ["p003", "p004", "p005", "p006"] and "rn" not in got[0]
    (count_sql, count_p), (sql, p) = bq.queries
    for s, params in ((count_sql, count_p), (sql, p)):
        assert f"`{TABLE}`" in s and "FOR SYSTEM_TIME AS OF LEAST(@snap, CURRENT_TIMESTAMP())" in s
        assert params["snap"] == NOW and params["channel"] == "ptt" and params["month"] == "2026-10"
    assert "COUNT(DISTINCT post_id) AS raw_count" in count_sql
    # 重抓的同一篇：ingested_at 先到先贏（和 file 的第一行贏一致），平手再用整列雜湊定順序
    assert "PARTITION BY t.post_id ORDER BY t.ingested_at, FARM_FINGERPRINT(TO_JSON_STRING(t))) = 1" in sql
    assert "t.post_id IS NOT NULL" in sql and "t.month = @month" in sql
    assert "ROW_NUMBER() OVER (ORDER BY post_id) AS rn" in sql and "rn > @start ORDER BY rn" in sql and p["start"] == 3


@pytest.mark.parametrize("published, month", [("2026-11-01T00:30:00+08:00", "2026-11"),   # PTT 月初半夜：照原時區算 11 月
                                               ("2026-10-31T23:50:00+08:00", "2026-10"),
                                               ("2026-10-31T23:30:00Z", "2026-10")])           # YouTube 是 UTC
def test_bigquery_row_values_match_jsonl_shape(published, month):
    row = {"post_id": "y1", "channel": "ptt", "sub_type": "comment", "source": "UCx", "url": None,
           "published_at": published, "fetched_at": "2026-11-01T00:00:00Z", "author": None, "text": None, "ingested_at": NOW}
    post = rawsrc.row_to_post(row)
    assert post["published_at"] == published and post["text"] == "" and "ingested_at" not in post
    assert job.base_doc(post)["month"] == month


def test_bigquery_timestamp_published_at_rejected():
    # 欄位若被建成 TIMESTAMP：11/1 00:30+08:00 回來變 10/31 16:30 UTC，會被算進 10 月 → 直接擋
    row = {"post_id": "p1", "published_at": dt.datetime(2026, 10, 31, 16, 30, tzinfo=dt.timezone.utc), "text": "x"}
    with pytest.raises(ValueError, match="STRING"):
        rawsrc.row_to_post(row)


def test_config_checked_before_claiming(monkeypatch):
    monkeypatch.setattr(job.config, "RAW_SOURCE", "gcs")
    with pytest.raises(SystemExit):
        rawsrc.check_config()
    monkeypatch.setattr(job.config, "RAW_SOURCE", "bigquery")
    for bad in ("", "raw.posts", "proj.raw.posts`; DROP TABLE x; --", "qsearch-prod.raw-data.posts"):
        monkeypatch.setattr(job.config, "RAW_BQ_TABLE", bad)
        with pytest.raises(SystemExit):
            rawsrc.check_config()
    for good in (TABLE, "qsearch-prod.raw.posts-2026"):   # 表名可以有 -，資料集不行
        monkeypatch.setattr(job.config, "RAW_BQ_TABLE", good)
        rawsrc.check_config()
    monkeypatch.setattr(job.config, "RAW_SOURCE", "file")
    rawsrc.check_config()


def test_main_checks_raw_source_before_claiming(monkeypatch):
    # 設定錯要在領分區之前停：領了才失敗會白白用掉一次 attempts
    monkeypatch.setattr(job.config, "RAW_SOURCE", "gcs")

    def boom(*a, **k):
        raise AssertionError("不該走到這裡")
    monkeypatch.setattr(job, "claim", boom)
    monkeypatch.setattr(job.dictionary, "load", boom)
    with pytest.raises(SystemExit):
        job.main([])


def test_snapshot_kept_while_within_time_travel_window(bq):
    led = FakeLedger()
    part = {"partition_id": "ptt/2026-10", "checkpoint_line": 500, "started_at": NOW - dt.timedelta(hours=100)}
    assert job.fresh_snapshot(led, part) is part and led.calls == []


@pytest.mark.parametrize("started", [NOW - dt.timedelta(hours=163), None])
def test_expired_snapshot_restarts_from_zero_with_new_time(bq, started):
    led = FakeLedger()
    part = {"partition_id": "ptt/2026-10", "checkpoint_line": 500, "started_at": started}
    out = job.fresh_snapshot(led, part)
    assert out["checkpoint_line"] == 0 and out["started_at"] >= NOW
    assert led.sets("ptt/2026-10") == [{"checkpoint_line": 0, "written": 0, "noop": 0, "errors": 0, "started_at": out["started_at"]}]


def test_force_resets_checkpoint_but_only_bigquery_moves_snapshot(monkeypatch):
    old = NOW - dt.timedelta(hours=5)
    monkeypatch.setattr(job.config, "RAW_SOURCE", "file")
    led = FakeLedger()   # file：和原本一樣只歸零，started_at 不動
    out = job.fresh_snapshot(led, {"partition_id": "ptt/2026-10", "checkpoint_line": 9, "started_at": old}, force=True)
    assert out["checkpoint_line"] == 0 and out["started_at"] == old
    assert led.sets("ptt/2026-10") == [{"checkpoint_line": 0, "written": 0, "noop": 0, "errors": 0}]
    monkeypatch.setattr(job.config, "RAW_SOURCE", "bigquery")
    led = FakeLedger()   # bigquery：時間點換成現在，started_at 一起寫進帳本
    out = job.fresh_snapshot(led, {"partition_id": "ptt/2026-10", "checkpoint_line": 9, "started_at": old}, force=True)
    assert out["checkpoint_line"] == 0 and out["started_at"] >= NOW
    assert led.sets("ptt/2026-10")[0]["started_at"] == out["started_at"]
    monkeypatch.setattr(job.config, "RAW_SOURCE", "file")
    led = FakeLedger()   # file 來源、沒 force：完全不碰帳本
    part = {"partition_id": "ptt/2026-10", "checkpoint_line": 9, "started_at": None}
    assert job.fresh_snapshot(led, part) is part and led.calls == []


class RunConn:
    """run_partition 留在 PG 的只有 canary 基準與 backfill_batches 批次列（帳本已在 ES）；這裡只回它要的欄位，並記下批次列。"""

    def __init__(self):
        self.batches = []

    def transaction(self):
        class Tx:
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return Tx()

    def execute(self, sql, params=()):
        if "INSERT INTO backfill_batches" in sql:
            self.batches.append(params)
        row = {"canary_baseline_ms": 10.0, "started_at": NOW}

        class R:
            def fetchone(_): return row
        return R()


class RunES:
    """實際 gates 會用到的部分：count 只回「達標篇數」＝寫進來的篇數，其他（漏斷詞、停用詞）都 0。"""
    index = "posts"

    def __init__(self):
        self.ids = []

    def write_rejected_total(self): return 0

    def req(self, *a, **k): return {}

    def count(self, q):
        return len(self.ids) if "seg_ver_v2" in json.dumps(q) else 0

    def bulk_seg(self, items, force=False):
        self.ids += [i[0] for i in items]
        return {"created": len(items), "updated": 0, "noop": 0, "errors": 0, "rejected": 0, "took_ms": 1}


def _run(monkeypatch, part, real_gates=False):
    class Dic:
        version, pipeline, stopwords = "qsearch-q6-2026.11.01.1", "v2", {"的"}

    class HB:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr(job, "CHUNK", 3)
    monkeypatch.setattr(job, "Heartbeat", HB)
    monkeypatch.setattr(job, "throttle", lambda conn, led: 1e9)
    monkeypatch.setattr(job, "segment_checked", lambda dic, ch, text: (["x"], []))
    monkeypatch.setattr(job, "seg_fields", lambda dic, terms, text: {})
    monkeypatch.setattr(job, "canary_after", lambda *a: (10.0, [10.0]))
    monkeypatch.setattr(job, "recover_throttle", lambda *a: None)
    if not real_gates:
        monkeypatch.setattr(job, "gates", lambda *a, **k: {g: {"pass": True} for g in ("G1", "G2", "G3", "G4")})
    conn, es = RunConn(), RunES()
    part = {"dict_version": Dic.version, "target_seg_ver": job.seg_ver("v2", Dic.version), "channel": "ptt", "month": "2026-10",
            "started_at": NOW - dt.timedelta(hours=1), **part}
    led = FakeLedger(rows={part["partition_id"]: {"status": "running"}})
    ok = job.run_partition(conn, led, es, Dic(), part)
    return ok, conn, es, led


def test_run_partition_resumes_bigquery_stream_from_checkpoint(bq, monkeypatch):
    pid = "ptt/2026-10"
    ok, conn, es, led = _run(monkeypatch, {"partition_id": pid, "checkpoint_line": 2})
    assert ok and es.ids == ["p002", "p003", "p004", "p005", "p006"]       # 前 2 篇上次已寫，不再拉
    assert led.checkpoints(pid) == [5, 7] and led.sets(pid)[0] == {"raw_count": 7, "raw_lines": 7}
    assert bq.queries[-1][1]["start"] == 2 and bq.queries[-1][1]["snap"] == NOW - dt.timedelta(hours=1)


def test_run_partition_stops_when_stream_shorter_than_count(bq, monkeypatch):
    bq.posts = bq.posts[:4]
    bq.query_count = bq.query

    def query(sql, job_config=None):   # 計數說 7 篇，實際只拉得到 4 篇：要停下來交給 G1，不能卡死、也不能算通過
        j = bq.query_count(sql, job_config)
        if "COUNT(*)" in sql:
            j.rows[0]["raw_count"] = 7
        return j
    bq.query = query
    pid = "ptt/2026-10"
    ok, conn, es, led = _run(monkeypatch, {"partition_id": pid, "checkpoint_line": 0}, real_gates=True)
    final = led.rows[pid]
    assert es.ids == ["p000", "p001", "p002", "p003"] and led.checkpoints(pid) == [3, 4]
    assert ok is False and final["status"] == "failed"
    assert final["gates"]["G1"]["pass"] is False and (final["gates"]["G1"]["raw"], final["gates"]["G1"]["es_at_target"]) == (7, 4)
    assert final["gates"]["G2"]["pass"] and final["gates"]["G3"]["pass"]   # 擋下來的只有 G1


# ---- 建帳本：原文來源的每個渠道 × 月一列 pending（job --plan） ----

class ListBQ:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def query(self, sql, job_config=None):
        self.sql.append(sql)
        return FakeJob(self.rows)


class Dic:
    version = "qsearch-q6-2026.10.30.3"


def test_list_partitions_file_scans_raw_root(tmp_path, monkeypatch):
    monkeypatch.setattr(job.config, "RAW_SOURCE", "file")
    monkeypatch.setattr(job.config, "RAW_ROOT", str(tmp_path))
    for ch, m in [("ptt", "2026-09"), ("ptt", "2026-10"), ("dcard", "2026-10")]:
        (tmp_path / ch).mkdir(exist_ok=True)
        (tmp_path / ch / f"{m}.jsonl").write_text("", encoding="utf-8")
    assert sorted(rawsrc.list_partitions()) == [("dcard", "2026-10"), ("ptt", "2026-09"), ("ptt", "2026-10")]


def test_list_partitions_bigquery_groups_channel_month(monkeypatch):
    monkeypatch.setattr(job.config, "RAW_SOURCE", "bigquery")
    monkeypatch.setattr(job.config, "RAW_BQ_TABLE", TABLE)
    fake = ListBQ([{"channel": "ptt", "month": "2026-10"}, {"channel": "ptt", "month": "2026-09"}])
    monkeypatch.setattr(rawsrc, "bq_client", lambda: fake)
    assert rawsrc.list_partitions() == [("ptt", "2026-10"), ("ptt", "2026-09")]
    assert f"`{TABLE}`" in fake.sql[0] and "GROUP BY channel, month" in fake.sql[0]


def test_plan_inserts_pending_rows_with_loaded_dict_version_and_is_rerunnable(monkeypatch):
    monkeypatch.setattr(rawsrc, "list_partitions", lambda: [("ptt", "2026-10"), ("ptt", "2026-09")])
    led = FakeLedger(rows={"ptt/2026-10": {"status": "passed"}})
    assert job.plan(led, Dic()) == 1          # 已在帳本的不動，只補新的
    doc = led.rows["ptt/2026-09"]
    assert (doc["partition_id"], doc["channel"], doc["month"], doc["dict_version"], doc["status"]) == ("ptt/2026-09", "ptt", "2026-09", Dic.version, "pending")
    assert doc["target_seg_ver"] == job.seg_ver("v2", Dic.version)   # 和 run_partition 的檢查同一個算法，不會 dict_mismatch
    assert led.rows["ptt/2026-10"] == {"status": "passed"}
    assert job.plan(led, Dic()) == 0


def test_main_plan_does_not_claim(monkeypatch):
    monkeypatch.setattr(job.config, "RAW_SOURCE", "file")
    monkeypatch.setattr(job.dictionary, "load", lambda: Dic())
    led = FakeLedger()
    monkeypatch.setattr(job, "Ledger", lambda *a, **k: led)
    monkeypatch.setattr(rawsrc, "list_partitions", lambda: [("ptt", "2026-10")])

    def boom(*a, **k):
        raise AssertionError("--plan 不該領分區或連 ES")
    monkeypatch.setattr(job, "claim", boom)
    monkeypatch.setattr(job, "ES", boom)
    job.main(["--plan"])
    assert led.rows["ptt/2026-10"]["status"] == "pending"   # 寫進帳本，而且沒有領任何分區
    assert not any(c[0] == "claim" for c in led.calls)
