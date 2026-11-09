"""G4 發版收斂與寫入守門（計畫書 P0 第 7 項、ES 健康第 10 項）：
seg_guard 看 content_sha、bulk 被 429 拒絕只重送被拒的那幾篇、刪文 tombstone、每台輪詢 manifest 收斂、
定向重跑共用回補限速、發版 outbox 背景重送、recon 的收斂 gauge、elasticsearch_exporter／快照／slowlog 設定。
全部用對應元件，不連 ES／PG／Pub/Sub。"""
import json
import sys
import threading
import time

import pytest

from _p0_fakes import API, MON, PG, REALENV, SEG, ensure_prometheus_client, has_series, read_text, read_value

ensure_prometheus_client()
if str(SEG) not in sys.path:
    sys.path.insert(0, str(SEG))
qes = pytest.importorskip("qseg.es")
control = pytest.importorskip("qseg.control")
rerun = pytest.importorskip("qseg.rerun")
job = pytest.importorskip("qseg.job")
recon = pytest.importorskip("qseg.recon")
metrics = pytest.importorskip("qseg.metrics")
yaml = pytest.importorskip("yaml")

V_OLD, V_NEW = "qsearch-q6-2026.10.31.1", "qsearch-q6-2026.11.02.1"


# ---------- ES 對應元件 ----------
class R:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(data.decode("utf-8"))
        return self.responses.pop(0)


def item(pid, result=None, status=None, error=None):
    u = {"_id": pid}
    if error:
        u.update(error=error, status=status)
    else:
        u["result"] = result
    return {"update": u}


def es_with(responses):
    e = qes.ES(url="http://es:9200", index="posts")
    e.s = Session(responses)
    return e


ITEMS = [("a", {"content_sha": "x"}, {"seg_ver_v2": 22026110201, "terms_v2": ["颱風"]}, "v2"),
         ("b", {}, {"seg_ver_v2": 22026110201}, "v2"),
         ("c", {}, {"seg_ver_v2": 22026110201}, "v2")]


class D:
    def __init__(self, v):
        self.pipeline, self.version = "v2", v


# ---------- seg_guard：content_sha ----------
def test_guard_script_rewrites_body_when_content_sha_changes():
    s = qes.GUARD_SCRIPT
    assert "content_sha" in s and "boolean changed" in s
    assert "cur != null && !changed && (force ? cur > params.seg_ver : cur >= params.seg_ver)" in s   # 同版、內容沒變才 noop
    assert "if (changed || !ctx._source.containsKey(entry.getKey())" in s                            # 內容變了就重寫本體
    assert 'GUARD_SCRIPT = """' in read_text(REALENV / "scripts" / "setup.py")                         # setup.py 裝同一段進 ES
    body = qes.ES(url="http://es:9200", index="posts")._bulk_lines(ITEMS[:1], force=True).decode("utf-8")
    assert '"force": true' in body and '"content_sha": "x"' in body and '"id": "seg_guard"' in body


# ---------- bulk 429 ----------
def test_bulk_seg_retries_only_rejected_items_with_backoff():
    slept = []
    es = es_with([
        R(429, {}),                                                                        # 整批被拒：全部重送
        R(200, {"items": [item("a", "updated"), item("b", status=429, error={"type": "es_rejected_execution_exception"}), item("c", "noop")]}),
        R(200, {"items": [item("b", "created")]}),
    ])
    out = es.bulk_seg(ITEMS, sleep=slept.append)
    assert (out["created"], out["updated"], out["noop"], out["errors"]) == (1, 1, 1, 0)
    assert out["retries"] == 2 and out["rejected"] == 4 and out["failed_ids"] == [] and slept == [1, 2]   # 1、2、4 秒退避（提案值）
    third = es.s.calls[2]
    assert '"_id": "b"' in third and '"_id": "a"' not in third and '"_id": "c"' not in third           # 只重送被拒的那篇


def test_bulk_seg_gives_up_after_retries_and_reports_failed_ids():
    slept = []
    es = es_with([R(200, {"items": [item("a", status=429, error={"type": "es_rejected_execution_exception"})]})] * 2)
    out = es.bulk_seg(ITEMS[:1], retries=1, sleep=slept.append)
    assert out["errors"] == 1 and out["failed_ids"] == ["a"] and out["retries"] == 1 and out["rejected"] == 2 and slept == [1]
    assert qes.BULK_RETRIES == 3


# ---------- tombstone ----------
def test_write_tombstones_only_sets_deleted_at_and_counts_missing():
    es = es_with([R(200, {"items": [item("a", "updated"), item("zz", status=404, error={"type": "document_missing_exception"}),
                                     item("c", status=400, error={"type": "mapper_parsing_exception"})]})])
    out = es.write_tombstones([("a", "2026-11-02T00:00:00Z"), ("zz", "2026-11-02T00:00:00Z"), ("c", "2026-11-02T00:00:00Z")])
    assert out == {"updated": 1, "missing": 1, "errors": 1}
    body = es.s.calls[0]
    assert '"doc": {"deleted_at": "2026-11-02T00:00:00Z"}' in body and "doc_as_upsert" not in body and "scripted_upsert" not in body
    app = read_text(API / "app.py")
    assert '"must_not": [{"exists": {"field": "deleted_at"}}]' in app          # 文字雲查詢排除 tombstone
    service = read_text(SEG / "qseg" / "service.py")
    assert 'attrs.get("action") == "delete"' in service and "write_tombstones" in service and 'attrs.get("edited") == "1"' in service


# ---------- 每台輪詢 manifest 收斂 ----------
def test_manifest_version_only_moves_forward():
    assert control.newer_version(V_OLD, V_NEW)
    assert not control.newer_version(V_NEW, V_OLD)
    assert not control.newer_version(V_NEW, V_NEW)
    assert not control.newer_version(V_NEW, None) and not control.newer_version(V_NEW, "garbage")


def test_swap_removes_old_dict_info_series():
    old, new = D(V_OLD), D(V_NEW)
    build = control.config.SEG_BUILD
    st = control.SegState(old)
    metrics.DICT.labels("v2", V_OLD, str(control.seg_ver("v2", V_OLD)), build).set(1)
    st.swap(new)
    assert not has_series(metrics.DICT, "v2", V_OLD, str(control.seg_ver("v2", V_OLD)), build)   # 舊版不能還是 1
    assert read_value(metrics.DICT, "v2", V_NEW, str(control.seg_ver("v2", V_NEW)), build) == 1
    assert st.dic is new


def test_manifest_poller_swaps_once_when_current_is_newer(monkeypatch):
    st = control.SegState(D(V_OLD))
    versions = iter([V_OLD, None, V_NEW, V_NEW])
    monkeypatch.setattr(control, "current_manifest_version", lambda base_url=None: next(versions, V_NEW))
    swaps = []

    def fake_load(state, version, source, **extra):
        swaps.append((version, source))
        state._dic = D(version)
        return state._dic
    monkeypatch.setattr(control, "load_and_swap", fake_load)
    stop = threading.Event()
    t = control.start_manifest_poller(st, interval=0.01, stop=stop)
    deadline = time.time() + 3
    while time.time() < deadline and len(swaps) < 1:
        time.sleep(0.01)
    time.sleep(0.1)
    stop.set()
    t.join(2)
    assert swaps == [(V_NEW, "manifest")] and st.dic.version == V_NEW   # 新版只換一次；同版或抓不到都不動
    assert control.MANIFEST_POLL_SEC == 15


def test_release_redelivered_after_done_does_not_overwrite_status(monkeypatch):
    """重跑超過 ack 期限、同一則發版訊息又送來一次：服務已在新版，只有還是 queued 的那一列才記 skipped，done 不會被蓋掉。"""
    executed = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            executed.append((sql, list(params)))
    monkeypatch.setattr(control, "_pg", lambda: Conn())
    control.handle_release({"dict_version": V_NEW, "words": ["台中"], "run_id": 7}, control.SegState(D(V_NEW)), es=None)
    (sql, params), = executed
    assert sql.startswith("UPDATE rerun_runs SET status=%s") and sql.endswith("WHERE id=%s AND status = ANY(%s)")
    assert params[0] == "skipped" and params[-2:] == [7, ["queued"]]
    executed.clear()
    control.set_run(7, status="done")                       # 一般的進度更新不加條件
    assert executed[0][0].endswith("WHERE id=%s") and executed[0][1][-1] == 7


def test_handle_release_uses_load_and_swap_and_counts_source():
    src = read_text(SEG / "qseg" / "control.py")
    assert 'load_and_swap(state, msg["dict_version"], source="control"' in src
    assert 'load_and_swap(state, v, source="manifest")' in src
    assert "metrics.DICT_SWAPS.labels(dic.pipeline, source).inc()" in src


# ---------- 定向重跑：共用回補限速、G3 ----------
def test_rerun_rate_limit_shares_backfill_budget(monkeypatch):
    class Conn:
        def __init__(self, row):
            self.row = row

        def execute(self, sql, params=()):
            row = self.row

            class Cur:
                def fetchone(self):
                    return row
            return Cur()
    assert rerun.rate_limit(Conn({"max_docs_per_sec": 3000, "paused": False, "n": 2})) == 1000   # 全域 ÷（回補分區數＋1）
    assert rerun.rate_limit(Conn({"max_docs_per_sec": 3000, "paused": False, "n": 0})) == 3000
    assert rerun.rate_limit(Conn({"max_docs_per_sec": 1, "paused": False, "n": 9})) == 1          # 最低 1 篇／秒
    assert rerun.rate_limit(Conn(None)) is None
    monkeypatch.setattr(job, "throttle", lambda conn: 7)                                           # paused：與回補 Job 同一段等待
    assert rerun.rate_limit(Conn({"max_docs_per_sec": 3000, "paused": True, "n": 0})) == 7
    src = read_text(SEG / "qseg" / "rerun.py")
    assert "canary_after(conn, es" in src and 'out["g3"]' in src and '"--no-limit"' in src


# ---------- 發版 outbox ----------
def test_release_outbox_flush_marks_sent_or_keeps_pending(monkeypatch):
    from test_p0_api_error_reason import load_app
    app = load_app()
    rows = [{"id": 1, "dict_version": V_OLD, "run_id": 7, "payload": {"action": "release"}, "attempts": 0},
            {"id": 2, "dict_version": V_NEW, "run_id": 8, "payload": {"action": "release"}, "attempts": 3}]
    execs = []
    monkeypatch.setattr(app, "pg_rows", lambda sql, params=(): [{"n": 1}] if "count(*)" in sql else rows)
    monkeypatch.setattr(app, "pg_exec", lambda sql, params=(): execs.append((sql, params)) or (1, None))

    def publish(topic, payload, **attrs):
        if attrs["run_id"] == 8:
            raise RuntimeError("emulator down")
        return "m-1"
    monkeypatch.setattr(app.PUBSUB, "publish", publish)
    before = read_value(app.OUTBOX_SENT, "retry_sent")
    assert app.outbox_flush() == 1
    assert read_value(app.OUTBOX_SENT, "retry_sent") == before + 1 and read_value(app.OUTBOX_PENDING) == 1
    assert "status='sent'" in execs[0][0] and execs[0][1] == ("m-1", 1)
    assert "attempts=attempts+1, last_error=%s" in execs[1][0] and execs[1][1] == ("emulator down", 2)


def test_release_writes_outbox_before_switching_current():
    src = read_text(API / "app.py")
    i_run, i_ob, i_sw, i_pub = (src.index("INSERT INTO rerun_runs"), src.index("INSERT INTO release_outbox"),
                                src.index("ops.switch_current(DICT_ROOT, v)"), src.index("PUBSUB.publish(CONTROL_TOPIC, payload"))
    assert i_run < i_ob < i_sw < i_pub                                  # 先寫 outbox 再切 current，最後才發
    assert 'name="release-outbox"' in src and "def outbox_flush" in src and '"outbox_pending"' in src
    assert "CREATE TABLE IF NOT EXISTS release_outbox" in read_text(PG / "live.sql")


# ---------- recon：收斂 gauge ----------
def test_recon_convergence_gauges():
    class Conn:
        def execute(self, sql, params=()):
            class Cur:
                def fetchall(self):
                    if "service_state" in sql:
                        return [("v2", 3, 2), ("v1", 1, 1)]
                    if "rerun_runs" in sql:
                        return [("running", 1, 125.0), ("done", 4, None)]
                    return []

                def fetchone(self):
                    return (2,) if "release_outbox" in sql else None
            return Cur()
    recon.convergence(Conn())
    assert read_value(metrics.SERVICE_VERSIONS, "v2") == 2 and read_value(metrics.SERVICE_ALIVE, "v2") == 3
    assert read_value(metrics.SERVICE_ALIVE, "v0") == 0 and read_value(metrics.SERVICE_VERSIONS, "v0") == 0
    assert read_value(metrics.RERUN_RUNS, "running") == 1 and read_value(metrics.RERUN_RUNS, "done") == 4 and read_value(metrics.RERUN_RUNS, "failed") == 0
    assert read_value(metrics.RERUN_RUNNING_AGE) == 125.0 and read_value(metrics.RELEASE_OUTBOX_PENDING) == 2

    class NoTable(Conn):
        def execute(self, sql, params=()):
            if "release_outbox" in sql:
                raise RuntimeError('relation "release_outbox" does not exist')
            return super().execute(sql, params)
    recon.convergence(NoTable())
    assert read_value(metrics.RELEASE_OUTBOX_PENDING) == 0


# ---------- ES exporter、快照、slowlog ----------
def test_es_exporter_snapshot_and_slowlog_are_wired():
    compose = read_text(REALENV / "docker-compose.yml")
    assert "quay.io/prometheuscommunity/elasticsearch-exporter:v1.9.0" in compose and "--es.slm" in compose
    assert "path.repo=/usr/share/elasticsearch/snapshots" in compose and "essnap:/usr/share/elasticsearch/snapshots" in compose
    prom = yaml.safe_load(read_text(MON / "prometheus.yml"))
    jobs = {j["job_name"]: [t for s in j["static_configs"] for t in s["targets"]] for j in prom["scrape_configs"]}
    assert jobs["es-exporter"] == ["es-exporter:9114"]
    es_rules = yaml.safe_load(read_text(MON / "rules" / "es.yml"))
    rules = [r for g in es_rules["groups"] for r in g["rules"]]
    assert {"ElasticsearchExporterDown", "ElasticsearchClusterRed", "ElasticsearchDiskCritical", "ElasticsearchWriteRejections",
            "ElasticsearchSnapshotFailed", "ElasticsearchSnapshotStale"} <= {r["alert"] for r in rules}
    exprs = " ".join(r["expr"] for r in rules)
    assert 'up{job="es-exporter"} == 0' in exprs and 'absent(up{job="es-exporter"})' in exprs
    rel = yaml.safe_load(read_text(MON / "rules" / "release.yml"))
    assert {"DictVersionDivergence", "RerunStuck", "ReleaseOutboxStuck", "SegNoAliveInstances"} <= {r["alert"] for g in rel["groups"] for r in g["rules"]}
    mapping = json.loads(read_text(REALENV / "es" / "index" / "posts.json"))
    assert mapping["settings"]["index.search.slowlog.threshold.query.warn"] == "60s"
    props = mapping["mappings"]["properties"]
    assert props["deleted_at"]["type"] == "date" and props["content_sha"]["type"] == "keyword" and props["author"]["type"] == "keyword"
    setup = read_text(REALENV / "scripts" / "setup.py")
    assert "_snapshot/local" in setup and "_slm/policy/daily-posts" in setup and "search.slowlog.threshold.query.warn" in setup
    assert "es-exporter" in read_text(REALENV / "run.sh")


def test_recon_idle_refreshes_heartbeat_gauges_between_full_runs(monkeypatch):
    """全量對帳每小時一次，但 alive／收斂 gauge 要每分鐘刷新；refresh 失敗不中斷等待。"""
    calls = []

    def refresh():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("pg down")
    n = recon.idle(0.05, 0.01, refresh)
    assert len(calls) >= 3 and n == len(calls) - 1   # 第二次失敗只記 log
    assert recon.HEARTBEAT_TICK == 60.0


def test_each_service_exposes_only_its_own_gauges():
    """共用 metrics 模組：串流服務不能把 recon／quality／coverage 的無標籤 gauge 以 0 曝露出去（否則 Stale 類告警每個 instance 各響一次）。"""
    stream, cov, rec, qual = (metrics.exposed_names(o) for o in (None, "coverage", "recon", "quality"))
    assert "qseg_recon_last_success_timestamp_seconds" in rec and "qseg_quality_whole_word_recall" in qual
    assert "qseg_coverage_last_success_timestamp_seconds" in cov and "qseg_service_instances_alive" in rec
    for name in ("qseg_recon_last_success_timestamp_seconds", "qseg_quality_whole_word_recall", "qseg_coverage_last_success_timestamp_seconds",
                 "qseg_service_instances_alive", "qseg_backfill_remaining_docs", "qseg_release_outbox_pending"):
        assert name not in stream, name
    assert not (rec & qual & cov) - {n for n in rec if not n.startswith("qseg_")}   # 三個批次服務的 qseg_ 指標互不重疊
    assert "qseg_rerun_runs" in rec and "qseg_rerun_runs" not in stream
    assert any(n.startswith("process_") for n in stream) or any(n.startswith("python_") for n in stream)
