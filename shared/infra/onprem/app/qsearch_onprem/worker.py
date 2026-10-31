"""抓 + 存：crawler worker（對應 GCP crawler-worker Cloud Run）。

消費 crawl.<source> 佇列的 job，從 repo fixture「抓」原始檔（不連外），
存進 MinIO（s3://qsearch-raw-scraped/<source>/<board>/dt=YYYY-MM-DD/<job_id>.<ext>），
再發佈 crawl.fetched 事件（欄位符合 qsearch-crawl-event-v1.avsc，raw_data_uri 為 s3://）。

失敗 -> nack(requeue=True)；quorum queue 的 x-delivery-limit 用完 -> DLX -> crawl.<source>.dlq。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from datetime import datetime, timezone

from . import common
from .config import Settings, settings

EXT = {"ptt_index": ".html", "ptt_article": ".html", "ptt_titles_jsonl": ".jsonl"}


def _message_key(props, body: bytes) -> str:
    return props.message_id or hashlib.sha1(body).hexdigest()


def rate_limit(rc, source: str, per_second: int) -> None:
    """固定視窗限速（對應 Cloud Tasks max_dispatches_per_second）；多個 worker 共用 Redis 計數。"""
    while True:
        now = int(time.time())
        key = f"ratelimit:{source}:{now}"
        n = rc.incr(key)
        rc.expire(key, 5)
        if n <= per_second:
            return
        time.sleep(max(0.05, now + 1 - time.time()))


def read_fixture(root: str, rel: str) -> bytes:
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, rel))
    if not path.startswith(base + os.sep):
        raise ValueError(f"fixture path escapes FIXTURE_ROOT: {rel}")
    with open(path, "rb") as fh:  # FileNotFoundError -> 重試 -> DLQ
        return fh.read()


def handle_job(s: Settings, conn, mc, rc, ch, job: dict) -> dict:
    source, board, kind = job["source"], job["board"], job["kind"]
    rate_limit(rc, source, s.crawl_rate_per_second)
    raw = read_fixture(s.fixture_root, job["fixture"])
    digest = common.sha256_hex(raw)
    w = job["window_key"]
    day = f"{w[0:4]}-{w[4:6]}-{w[6:8]}"
    key = f"{source}/{board}/dt={day}/{job['job_id']}{EXT.get(kind, '.bin')}"
    fetched_at = datetime.now(timezone.utc).isoformat()
    mc.put_object(
        s.raw_bucket, key, io.BytesIO(raw), length=len(raw),
        content_type="text/html; charset=utf-8" if kind != "ptt_titles_jsonl" else "application/x-ndjson",
        metadata={"job-id": job["job_id"], "sha256": digest, "kind": kind, "fixture": job["fixture"],
                  "fetched-at": fetched_at},
    )
    raw_uri = f"s3://{s.raw_bucket}/{key}"
    event = common.build_event(event_id=common.make_event_id(job["job_id"], digest), job_id=job["job_id"],
                               platform=source, raw_uri=raw_uri)
    common.validate_event(event, s.schema_path)
    with common.pg_tx(conn) as cur:
        cur.execute(
            """UPDATE crawl_jobs SET status='fetched', fetched_at=now(), raw_uri=%s, content_sha256=%s,
                      event_id=%s, last_error=NULL WHERE job_id=%s""",
            (raw_uri, digest, event["event_id"], job["job_id"]),
        )
    common.publish_json(ch, common.EX_FETCHED, "", event, message_id=event["event_id"],
                        headers={"schema": "qsearch-crawl-event-v1", "kind": kind})
    rc.hset("crawl:last_fetch", source, fetched_at)
    return event


def on_message(s: Settings, conn, mc, rc, ch, method, props, body: bytes) -> None:
    key = _message_key(props, body)
    attempts = common.record_attempt(conn, "worker", key)
    try:
        job = json.loads(body)
        event = handle_job(s, conn, mc, rc, ch, job)
        with common.pg_tx(conn) as cur:
            cur.execute("UPDATE crawl_jobs SET fetch_attempts = fetch_attempts + %s WHERE job_id=%s",
                        (attempts, job["job_id"]))
        ch.basic_ack(method.delivery_tag)
        common.log.info("fetched job=%s event=%s uri=%s attempt=%d", job["job_id"], event["event_id"],
                        event["raw_data_uri"], attempts)
    except Exception as exc:  # noqa: BLE001 - 一律重試，次數由 x-delivery-limit 控制
        common.set_attempt_error(conn, "worker", key, f"{type(exc).__name__}: {exc}")
        common.log.warning("job failed key=%s attempt=%d err=%s: %s", key, attempts, type(exc).__name__, exc)
        time.sleep(s.retry_backoff_seconds)
        ch.basic_nack(method.delivery_tag, requeue=True)


def main() -> int:
    common.setup_logging()
    s = settings()
    conn = common.pg_connect(s)
    mc = common.minio_client(s)
    rc = common.redis_client(s)
    rconn = common.rabbit_connect(s)
    ch = rconn.channel()
    ch.confirm_delivery()
    ch.basic_qos(prefetch_count=1)
    for source in s.sources:
        ch.basic_consume(common.crawl_queue(source),
                         lambda c, m, p, b: on_message(s, conn, mc, rc, c, m, p, b))
    common.log.info("worker consuming %s", [common.crawl_queue(x) for x in s.sources])
    ch.start_consuming()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
