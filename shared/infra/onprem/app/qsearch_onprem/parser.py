"""解 + 用：parser consumer（對應 GCP parser Cloud Run 的 /events/crawl-fetched）。

流程（每則 crawl.fetched 事件）：
  1. fastavro 驗證事件（qsearch-crawl-event-v1.avsc），raw_data_uri 必須是 s3://
  2. PostgreSQL inbox：INSERT ... ON CONFLICT (event_id) -> 重複投遞只會 deliveries+1
  3. 從 MinIO 讀原始檔、解析 PTT 標題／作者／日期（文章頁另有內文）
  4. PostgreSQL upsert ptt_posts（pg_bigm GIN 索引在 title、body）
  5. ClickHouse 寫一列分析資料（ingest_events，依日分區）—— 只在 ch_done=false 時寫
  6. OpenSearch 以 post_id 當 _id 建索引（重送 = 覆寫同一份文件）
  7. 全部成功才 ack；任何例外 -> nack(requeue) -> 超過 x-delivery-limit -> qsearch.parser.dlq

訊息 header x-replay=reparse（由 replay.py 送出）會強制重跑 2–4、6，但不重複寫 ClickHouse。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from opensearchpy import helpers

from . import common, ptt
from .config import Settings, settings

CH_COLUMNS = ["event_id", "schema_version", "job_id", "platform", "event_type", "raw_data_uri", "post_count",
              "ingested_at"]
POST_COLUMNS = ["post_id", "board", "title", "author", "posted_at", "list_date", "nrec", "body", "url"]

UPSERT_POST = """
INSERT INTO ptt_posts (post_id, board, title, author, posted_at, list_date, nrec, body, url, source_event_id)
VALUES (%(post_id)s, %(board)s, %(title)s, %(author)s, %(posted_at)s, %(list_date)s, %(nrec)s, %(body)s, %(url)s,
        %(source_event_id)s)
ON CONFLICT (post_id) DO UPDATE SET
  board = EXCLUDED.board,
  title = EXCLUDED.title,
  author = COALESCE(EXCLUDED.author, ptt_posts.author),
  posted_at = COALESCE(EXCLUDED.posted_at, ptt_posts.posted_at),
  list_date = COALESCE(EXCLUDED.list_date, ptt_posts.list_date),
  nrec = COALESCE(EXCLUDED.nrec, ptt_posts.nrec),
  body = COALESCE(EXCLUDED.body, ptt_posts.body),
  url = COALESCE(EXCLUDED.url, ptt_posts.url),
  updated_at = now()
"""


class Parser:
    def __init__(self, s: Settings):
        self.s = s
        self.conn = common.pg_connect(s)
        self.mc = common.minio_client(s)
        self.chc = common.ch_client(s)
        self.osc = common.os_client(s)

    # ------------------------------------------------------------------
    def process(self, event: dict, reparse: bool) -> dict:
        s, conn = self.s, self.conn
        with common.pg_tx(conn) as cur:
            cur.execute(
                """INSERT INTO ingest_events (event_id, job_id, platform, event_type, schema_version, raw_data_uri)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (event_id) DO UPDATE SET deliveries = ingest_events.deliveries + 1
                   RETURNING (xmax = 0) AS inserted, pg_done, ch_done, os_done""",
                (event["event_id"], event["job_id"], event["platform"], event["event_type"],
                 event["schema_version"], event["raw_data_uri"]),
            )
            inserted, pg_done, ch_done, os_done = cur.fetchone()
        if not inserted and pg_done and ch_done and os_done and not reparse:
            return {"event_id": event["event_id"], "duplicate": True}

        bucket, key = common.split_s3_uri(event["raw_data_uri"])
        resp = self.mc.get_object(bucket, key)
        try:
            raw = resp.read()
            kind = resp.headers.get("x-amz-meta-kind")
            expected_sha = resp.headers.get("x-amz-meta-sha256")
        finally:
            resp.close()
            resp.release_conn()
        if expected_sha and common.sha256_hex(raw) != expected_sha:
            raise ValueError("raw object sha256 does not match metadata")
        board = key.split("/")[1] if key.count("/") >= 2 else "unknown"
        posts = ptt.dedupe(ptt.parse_raw(kind, raw, board))
        ids = [p.post_id for p in posts]

        with common.pg_tx(conn) as cur:
            cur.executemany(UPSERT_POST, [{**p.to_dict(), "source_event_id": event["event_id"]} for p in posts])
            cur.execute(
                "UPDATE ingest_events SET pg_done = true, post_count = %s WHERE event_id = %s",
                (len(posts), event["event_id"]),
            )

        if not ch_done:
            row = [event["event_id"], event["schema_version"], event["job_id"], event["platform"],
                   event["event_type"], event["raw_data_uri"], len(posts), datetime.now(timezone.utc)]
            self.chc.insert(f"{s.ch_database}.ingest_events", [row], column_names=CH_COLUMNS)
            with common.pg_tx(conn) as cur:
                cur.execute("UPDATE ingest_events SET ch_done = true WHERE event_id = %s", (event["event_id"],))

        # OpenSearch 以 PostgreSQL 的最終狀態為準（避免不同原始檔互相覆蓋掉作者等欄位）。
        with common.pg_tx(conn) as cur:
            cur.execute(f"SELECT {', '.join(POST_COLUMNS)}, source_event_id FROM ptt_posts WHERE post_id = ANY(%s)",
                        (ids,))
            rows = cur.fetchall()
        actions = []
        for r in rows:
            doc = dict(zip(POST_COLUMNS + ["source_event_id"], r))
            if doc["posted_at"] is not None:
                doc["posted_at"] = doc["posted_at"].isoformat()
            actions.append({"_op_type": "index", "_index": s.os_index, "_id": doc["post_id"], "_source": doc})
        common.ensure_os_index(self.osc, s.os_index)  # 索引被刪掉時用正確 mapping 重建，不靠 auto-create
        ok, errors = helpers.bulk(self.osc, actions, chunk_size=1000, raise_on_error=False)
        if errors:
            raise RuntimeError(f"OpenSearch bulk errors: {errors[:3]}")
        with common.pg_tx(conn) as cur:
            # processed_count 放在最後一個下游寫完之後才加：replay 以它判斷「整條解析做完」，
            # 若在 PostgreSQL 階段就加，OpenSearch bulk 還在途時 replay 就會提早返回（實測發生過）。
            cur.execute("""UPDATE ingest_events SET os_done = true, processed_count = processed_count + 1,
                                  last_processed_at = now() WHERE event_id = %s""", (event["event_id"],))
        return {"event_id": event["event_id"], "posts": len(posts), "inserted": inserted, "reparse": reparse,
                "ch_written": not ch_done, "os_indexed": ok}

    # ------------------------------------------------------------------
    def on_message(self, ch, method, props, body: bytes) -> None:
        key = props.message_id or hashlib.sha1(body).hexdigest()
        attempts = common.record_attempt(self.conn, "parser", key)
        replay = (props.headers or {}).get("x-replay", "")
        try:
            event = json.loads(body)
            if not isinstance(event, dict):
                raise ValueError("event is not a JSON object")
            common.validate_event(event, self.s.schema_path)
            result = self.process(event, reparse=(replay == "reparse"))
            ch.basic_ack(method.delivery_tag)
            common.log.info("parsed %s attempt=%d replay=%s", json.dumps(result, ensure_ascii=False), attempts,
                            replay or "-")
        except Exception as exc:  # noqa: BLE001 - 永久性錯誤也重試，與 Pub/Sub 推送 422 的行為一致
            common.set_attempt_error(self.conn, "parser", key, f"{type(exc).__name__}: {exc}")
            common.log.warning("parse failed key=%s attempt=%d err=%s: %s", key, attempts, type(exc).__name__,
                               str(exc)[:200])
            time.sleep(self.s.retry_backoff_seconds)
            ch.basic_nack(method.delivery_tag, requeue=True)


def main() -> int:
    common.setup_logging()
    s = settings()
    p = Parser(s)
    rconn = common.rabbit_connect(s)
    ch = rconn.channel()
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(common.Q_PARSER, p.on_message)
    common.log.info("parser consuming %s", common.Q_PARSER)
    ch.start_consuming()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
