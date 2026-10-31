"""端到端 smoke test：對「正在跑的」qsearch-onprem stack 驗證 排→派→抓→存→解→用。

前提：全新的 stack（docker compose down -v 之後再 up）。測試依檔案順序執行，彼此有先後依賴。
期望值（文章數、關鍵字命中數）由本檔用正規表示式從 fixture 獨立算出，不呼叫 qsearch_onprem.ptt，
避免「用同一份解析程式驗證自己」。
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import uuid
from pathlib import Path

import fastavro
import pika
import psycopg
import pytest

from qsearch_onprem import common, replay, scheduler
from qsearch_onprem.config import settings

S = settings()
FIX = Path(S.fixture_root)
KEYWORD = "蔡康永"          # 標題關鍵字（中文 3 字 -> pg_bigm 2-gram：蔡康、康永）
BODY_KEYWORD = "洋流小物"     # 只出現在文章頁內文
STATE: dict = {"timings": {}}
RESULTS_DIR = Path(os.environ.get("RESULTS_DIR", "/tmp/results"))


# ----------------------------------------------------------------- 期望值（獨立計算）
def expected_posts() -> dict[str, str]:
    posts: dict[str, str] = {}
    anchor = re.compile(r'<div class="title">\s*<a href="/bbs/Gossiping/(M\.\d+\.A\.[0-9A-F]{3})\.html">([^<]*)</a>')
    for name in ("ptt_index_latest.html", "ptt_index_39386.html"):
        for pid, title in anchor.findall((FIX / "capture" / name).read_text(encoding="utf-8")):
            posts[pid] = html.unescape(title).strip()
    art = (FIX / "capture" / "ptt_article.html").read_text(encoding="utf-8")
    pid = re.search(r'文章網址: <a href="https://www\.ptt\.cc/bbs/Gossiping/(M\.\d+\.A\.[0-9A-F]{3})\.html"', art).group(1)
    title = re.search(r'標題</span><span class="article-meta-value">([^<]*)</span>', art).group(1)
    posts[pid] = html.unescape(title).strip()
    for line in (FIX / "q6" / "ptt_titles_7d.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            posts[row["doc_id"]] = row["text"]
    return posts


EXPECTED = expected_posts()
EXPECTED_JOBS = sum(len(v["fixtures"]) for v in scheduler.SOURCES.values())


# ----------------------------------------------------------------- helpers
def pg() -> psycopg.Connection:
    return psycopg.connect(S.pg_dsn, autocommit=True)


def q1(sql: str, params=None):
    with pg() as c:
        return c.execute(sql, params).fetchone()


def wait_until(fn, timeout: float = 120, interval: float = 0.5, what: str = ""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timeout waiting for {what}: last={last!r}")


def rabbit():
    conn = pika.BlockingConnection(pika.URLParameters(S.amqp_url))
    return conn, conn.channel()


def queue_depth(name: str) -> int:
    conn, ch = rabbit()
    try:
        return ch.queue_declare(name, passive=True).method.message_count
    finally:
        conn.close()


def counts() -> dict:
    with pg() as c:
        pg_events = c.execute("SELECT count(*) FROM ingest_events").fetchone()[0]
        pg_posts = c.execute("SELECT count(*) FROM ptt_posts").fetchone()[0]
        jobs_fetched = c.execute("SELECT count(*) FROM crawl_jobs WHERE status='fetched'").fetchone()[0]
    chc = common.ch_client(S)
    ch_rows = chc.command(f"SELECT count() FROM {S.ch_database}.ingest_events")
    osc = common.os_client(S)
    osc.indices.refresh(index=S.os_index)
    os_docs = osc.count(index=S.os_index)["count"]
    mc = common.minio_client(S)
    objects = sum(1 for _ in mc.list_objects(S.raw_bucket, recursive=True))
    return {"pg_ingest_events": pg_events, "pg_ptt_posts": pg_posts, "pg_jobs_fetched": jobs_fetched,
            "ch_ingest_events": int(ch_rows), "os_docs": os_docs, "minio_objects": objects}


def save_summary():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "smoke_summary.json").write_text(json.dumps(STATE, ensure_ascii=False, indent=2, default=str),
                                                    encoding="utf-8")


# ----------------------------------------------------------------- tests
def test_00_fresh_stack():
    n = q1("SELECT count(*) FROM crawl_jobs")[0]
    assert n == 0, "smoke test 需要全新的 stack（先 docker compose down -v）"
    STATE["expected"] = {"posts": len(EXPECTED), "jobs": EXPECTED_JOBS,
                         "title_keyword": KEYWORD,
                         "title_keyword_rows": sum(KEYWORD in t for t in EXPECTED.values())}


def test_01_pg_bigm_installed():
    name, ver = q1("SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_bigm'")
    bigrams = q1("SELECT show_bigm(%s)", (KEYWORD,))[0]
    server = q1("SHOW server_version")[0]
    STATE["pg_bigm"] = {"extversion": ver, "show_bigm": bigrams, "server_version": server}
    assert name == "pg_bigm"
    assert "蔡康" in bigrams and "康永" in bigrams


def test_02_scheduler_tick_is_idempotent():
    t0 = time.time()
    first = scheduler.tick(S)
    second = scheduler.tick(S)
    STATE["tick"] = {"first": first, "second": second}
    STATE["timings"]["tick_start"] = t0
    assert first["inserted"] == EXPECTED_JOBS and first["published"] == EXPECTED_JOBS
    assert second["inserted"] == 0 and second["published"] == 0


def test_03_pipeline_drains():
    t0 = STATE["timings"]["tick_start"]
    wait_until(lambda: q1("SELECT count(*) FROM crawl_jobs WHERE status='fetched'")[0] == EXPECTED_JOBS,
               timeout=120, what="all jobs fetched")
    STATE["timings"]["fetch_all_s"] = round(time.time() - t0, 2)
    wait_until(lambda: q1("SELECT count(*) FROM ingest_events WHERE pg_done AND ch_done AND os_done")[0]
               == EXPECTED_JOBS, timeout=180, what="all events parsed")
    STATE["timings"]["tick_to_all_parsed_s"] = round(time.time() - t0, 2)
    with pg() as c:
        rows = c.execute("SELECT job_id, raw_uri, event_id, fetch_attempts FROM crawl_jobs ORDER BY job_id").fetchall()
    STATE["jobs"] = [dict(zip(["job_id", "raw_uri", "event_id", "fetch_attempts"], r)) for r in rows]


def test_04_minio_raw_objects_dated_keys():
    mc = common.minio_client(S)
    keys = sorted(o.object_name for o in mc.list_objects(S.raw_bucket, recursive=True))
    STATE["minio_keys"] = keys
    assert len(keys) == EXPECTED_JOBS
    for k in keys:
        assert re.fullmatch(r"ptt/Gossiping/dt=\d{4}-\d{2}-\d{2}/job-ptt-[a-z0-9-]+-\d{8}\.(html|jsonl)", k), k


def test_05_published_events_match_avro_schema():
    with open(S.schema_path, encoding="utf-8") as fh:
        schema = fastavro.parse_schema(json.load(fh))
    conn, ch = rabbit()
    msgs = []
    try:
        while True:
            method, props, body = ch.basic_get(common.Q_AUDIT, auto_ack=True)
            if method is None:
                break
            msgs.append(json.loads(body))
    finally:
        conn.close()
    STATE["audit_sample_event"] = msgs[0] if msgs else None
    assert len(msgs) == EXPECTED_JOBS
    field_names = {f["name"] for f in schema["fields"]}
    for m in msgs:
        fastavro.validation.validate(m, schema, raise_errors=True, strict=True)
        assert set(m) == field_names  # validate() 不擋多餘欄位，另外檢查
        assert m["raw_data_uri"].startswith("s3://qsearch-raw-scraped/")
        assert m["event_type"] == "crawl.fetched" and m["schema_version"] == 1


def test_06_postgres_rows():
    n = q1("SELECT count(*) FROM ptt_posts")[0]
    authors = q1("SELECT count(*) FROM ptt_posts WHERE author IS NOT NULL")[0]
    bodies = q1("SELECT count(*) FROM ptt_posts WHERE body IS NOT NULL")[0]
    STATE["pg_posts"] = {"rows": n, "with_author": authors, "with_body": bodies}
    assert n == len(EXPECTED)
    assert authors == 18 + 20 + 1  # 兩個列表頁 + 文章頁有作者；JSONL 只有標題
    assert bodies == 1


def _explain(cur, sql, params) -> str:
    return "\n".join(r[0] for r in cur.execute("EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) " + sql, params).fetchall())


def test_07_bigm_index_used_for_chinese_like():
    sql = "SELECT post_id FROM ptt_posts WHERE title LIKE %s"
    pattern = f"%{KEYWORD}%"
    with pg() as c:
        c.execute("ANALYZE ptt_posts")
        with c.cursor() as cur:
            plan_default = _explain(cur, sql, (pattern,))
            forced = "ptt_posts_title_bigm_idx" not in plan_default
            if forced:
                cur.execute("SET enable_seqscan = off")
                plan = _explain(cur, sql, (pattern,))
            else:
                plan = plan_default
            rows = cur.execute(sql, (pattern,)).fetchall()
            body_plan = _explain(cur, "SELECT post_id FROM ptt_posts WHERE body LIKE %s", (f"%{BODY_KEYWORD}%",))
            body_rows = cur.execute("SELECT post_id FROM ptt_posts WHERE body LIKE %s",
                                    (f"%{BODY_KEYWORD}%",)).fetchall()
    expected = sorted(pid for pid, t in EXPECTED.items() if KEYWORD in t)
    STATE["bigm"] = {"plan_default": plan_default, "enable_seqscan_off": forced, "plan": plan,
                     "rows": len(rows), "expected_rows": len(expected),
                     "body_plan": body_plan, "body_rows": len(body_rows)}
    assert "Bitmap Index Scan on ptt_posts_title_bigm_idx" in plan
    assert sorted(r[0] for r in rows) == expected
    assert len(body_rows) == 1


def test_08_clickhouse_rows_partitioned_by_day():
    chc = common.ch_client(S)
    n = int(chc.command(f"SELECT count() FROM {S.ch_database}.ingest_events"))
    parts = chc.query("SELECT DISTINCT partition FROM system.parts WHERE database = %(db)s "
                      "AND table = 'ingest_events' AND active", parameters={"db": S.ch_database}).result_rows
    posts = int(chc.command(f"SELECT sum(post_count) FROM {S.ch_database}.ingest_events"))
    engine = chc.command("SELECT engine_full FROM system.tables WHERE database = 'qsearch' AND name = 'ingest_events'")
    STATE["clickhouse"] = {"rows": n, "partitions": [p[0] for p in parts], "sum_post_count": posts,
                           "engine_full": engine}
    assert n == EXPECTED_JOBS
    assert posts == len(EXPECTED)
    assert "PARTITION BY toDate(ingested_at)" in engine


def test_09_opensearch_hits():
    osc = common.os_client(S)
    osc.indices.refresh(index=S.os_index)
    total = osc.count(index=S.os_index)["count"]
    res = osc.search(index=S.os_index, body={"query": {"match_phrase": {"title": KEYWORD}},
                                             "track_total_hits": True, "size": 100, "_source": ["post_id"]})
    hits = res["hits"]["total"]["value"]
    ids = sorted(h["_source"]["post_id"] for h in res["hits"]["hits"])
    expected = sorted(pid for pid, t in EXPECTED.items() if KEYWORD in t)
    STATE["opensearch"] = {"docs": total, "match_phrase_hits": hits, "version": osc.info()["version"]["number"]}
    assert total == len(EXPECTED)
    assert ids == expected


def test_10_redis_aof_and_rate_limiter():
    rc = common.redis_client(S)
    appendonly = rc.config_get("appendonly")["appendonly"]
    info = rc.info("persistence")
    last_fetch = rc.hget("crawl:last_fetch", "ptt")
    STATE["redis"] = {"appendonly": appendonly, "aof_enabled": info["aof_enabled"],
                      "aof_last_write_status": info["aof_last_write_status"], "crawl_last_fetch_ptt": last_fetch,
                      "version": rc.info("server")["redis_version"]}
    assert appendonly == "yes" and info["aof_enabled"] == 1
    assert info["aof_last_write_status"] == "ok"
    assert last_fetch


def _x_death(queue: str, key: str):
    conn, ch = rabbit()
    try:
        for _ in range(queue_depth(queue)):
            method, props, body = ch.basic_get(queue, auto_ack=False)
            if method is None:
                break
            if props.message_id == key:
                hdr = (props.headers or {}).get("x-death") or []
                ch.basic_nack(method.delivery_tag, requeue=True)
                return [{k: (v.decode() if isinstance(v, bytes) else v) for k, v in d.items()
                         if k in ("reason", "queue", "count")} for d in hdr]
            ch.basic_nack(method.delivery_tag, requeue=True)
    finally:
        conn.close()
    return None


def test_11_poison_job_goes_to_crawl_dlq_after_retries():
    dlq = common.crawl_dlq("ptt")
    before = queue_depth(dlq)
    job_id = f"job-ptt-poison-{uuid.uuid4().hex[:8]}"
    conn, ch = rabbit()
    ch.confirm_delivery()
    common.publish_json(ch, common.EX_JOBS, "ptt", {"job_id": job_id, "source": "ptt", "board": "Gossiping",
                                                    "kind": "ptt_index", "fixture": "capture/does-not-exist.html",
                                                    "window_key": "20261030"}, message_id=job_id)
    conn.close()
    wait_until(lambda: queue_depth(dlq) == before + 1, timeout=90, what="poison job in DLQ")
    attempts, err = q1("SELECT attempts, last_error FROM delivery_attempts WHERE stage='worker' AND message_key=%s",
                       (job_id,))
    STATE["dlq_crawl"] = {"queue": dlq, "depth": queue_depth(dlq), "attempts": attempts, "last_error": err,
                          "x_death": _x_death(dlq, job_id)}
    assert attempts == S.crawl_max_attempts
    assert "FileNotFoundError" in err
    assert STATE["dlq_crawl"]["x_death"][0]["reason"] == "delivery_limit"


def test_12_poison_event_goes_to_parser_dlq_after_retries():
    before = queue_depth(common.Q_PARSER_DLQ)
    key = f"poison-evt-{uuid.uuid4().hex[:8]}"
    conn, ch = rabbit()
    ch.confirm_delivery()
    common.publish_json(ch, common.EX_FETCHED, "", b"{this is not json", message_id=key)
    conn.close()
    wait_until(lambda: queue_depth(common.Q_PARSER_DLQ) == before + 1, timeout=90, what="poison event in DLQ")
    attempts, err = q1("SELECT attempts, last_error FROM delivery_attempts WHERE stage='parser' AND message_key=%s",
                       (key,))
    STATE["dlq_parser"] = {"queue": common.Q_PARSER_DLQ, "depth": queue_depth(common.Q_PARSER_DLQ),
                           "attempts": attempts, "last_error": err, "x_death": _x_death(common.Q_PARSER_DLQ, key)}
    assert attempts == S.parser_max_attempts
    assert "JSONDecodeError" in err
    assert STATE["dlq_parser"]["x_death"][0]["reason"] == "delivery_limit"
    # 毒訊息不能留下任何資料
    assert q1("SELECT count(*) FROM ingest_events")[0] == EXPECTED_JOBS


def test_13_baseline_counts():
    c = counts()
    STATE["baseline"] = c
    assert c == {"pg_ingest_events": EXPECTED_JOBS, "pg_ptt_posts": len(EXPECTED), "pg_jobs_fetched": EXPECTED_JOBS,
                 "ch_ingest_events": EXPECTED_JOBS, "os_docs": len(EXPECTED), "minio_objects": EXPECTED_JOBS}


def test_14_duplicate_publish_is_idempotent():
    event_id, job_id, raw_uri = q1("SELECT event_id, job_id, raw_uri FROM crawl_jobs ORDER BY job_id LIMIT 1")
    before = q1("SELECT deliveries FROM ingest_events WHERE event_id=%s", (event_id,))[0]
    event = common.build_event(event_id=event_id, job_id=job_id, platform="ptt", raw_uri=raw_uri)
    conn, ch = rabbit()
    ch.confirm_delivery()
    for _ in range(2):
        common.publish_json(ch, common.EX_FETCHED, "", event, message_id=event_id)
    conn.close()
    wait_until(lambda: q1("SELECT deliveries FROM ingest_events WHERE event_id=%s", (event_id,))[0] == before + 2,
               timeout=60, what="duplicate deliveries recorded")
    c = counts()
    STATE["duplicate_publish"] = {"event_id": event_id, "deliveries_before": before, "deliveries_after": before + 2,
                                  "counts": c}
    assert c == STATE["baseline"]


def test_15_replay_redeliver_is_idempotent():
    t0 = time.time()
    r = replay.replay(S, "redeliver", wait=True, timeout=120)
    c = counts()
    STATE["replay_redeliver"] = {"result": r, "counts": c, "seconds": round(time.time() - t0, 2)}
    assert r["replayed"] == EXPECTED_JOBS and not r["missing_raw"]
    assert r["processed_after"] == r["processed_before"]  # 重複投遞不會重新解析
    assert c == STATE["baseline"]


def test_16_replay_reparse_reprocesses_from_minio():
    t0 = time.time()
    r = replay.replay(S, "reparse", wait=True, timeout=180)
    c = counts()
    STATE["replay_reparse"] = {"result": r, "counts": c, "seconds": round(time.time() - t0, 2)}
    assert r["processed_after"] - r["processed_before"] == EXPECTED_JOBS
    assert c == STATE["baseline"]


def test_17_rebuild_derived_stores_from_minio_and_job_table():
    """模擬下游資料遺失：清空 PG inbox/文章、ClickHouse、OpenSearch，只靠 MinIO + crawl_jobs 重建。"""
    with pg() as c:
        c.execute("TRUNCATE ingest_events, ptt_posts")
    chc = common.ch_client(S)
    chc.command(f"TRUNCATE TABLE {S.ch_database}.ingest_events")
    osc = common.os_client(S)
    osc.indices.delete(index=S.os_index)
    common.ensure_os_index(osc, S.os_index)
    wiped = counts()
    t0 = time.time()
    r = replay.replay(S, "reparse", wait=True, timeout=180)
    c = counts()
    STATE["rebuild"] = {"wiped": wiped, "result": r, "counts": c, "seconds": round(time.time() - t0, 2)}
    assert wiped["pg_ptt_posts"] == 0 and wiped["ch_ingest_events"] == 0 and wiped["os_docs"] == 0
    assert c == STATE["baseline"]


def test_99_write_summary():
    STATE["final_counts"] = counts()
    STATE["timings"].pop("tick_start", None)
    save_summary()
    print("\nSMOKE_SUMMARY " + json.dumps(STATE, ensure_ascii=False, default=str))
