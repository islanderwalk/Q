"""共用：連線、RabbitMQ 拓撲、Avro 事件、資料表定義。

GCP ↔ 地端名稱對照（拓撲）：
  Cloud Tasks 佇列 crawl-<source>        -> exchange qsearch.crawl.jobs (direct) + quorum queue crawl.<source>
  Cloud Tasks retry_config.max_attempts  -> quorum queue x-delivery-limit（= 最大嘗試次數 - 1）
  Pub/Sub 主題 qsearch-crawl-fetched     -> exchange qsearch.crawl-fetched (fanout)
  Pub/Sub 訂閱 qsearch-parser-sub        -> quorum queue qsearch.parser
  Pub/Sub 訂閱 audit-sub（保留 7 天）     -> classic queue qsearch.audit（TTL 7 天）
  Pub/Sub DLQ qsearch-parser-dlq         -> exchange qsearch.dlx + queue qsearch.parser.dlq
  Pub/Sub Seek（重播已 ACK 訊息）          -> 沒有對應功能；改用 replay.py（MinIO 原始檔 + crawl_jobs 表）
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import fastavro
import pika
import psycopg

from .config import Settings

log = logging.getLogger("qsearch_onprem")

EX_JOBS = "qsearch.crawl.jobs"
EX_FETCHED = "qsearch.crawl-fetched"
EX_DLX = "qsearch.dlx"
Q_PARSER = "qsearch.parser"
Q_PARSER_DLQ = "qsearch.parser.dlq"
Q_AUDIT = "qsearch.audit"


def crawl_queue(source: str) -> str:
    return f"crawl.{source}"


def crawl_dlq(source: str) -> str:
    return f"crawl.{source}.dlq"


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("pika", "opensearch", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------- RabbitMQ
def rabbit_connect(s: Settings, retries: int = 60) -> pika.BlockingConnection:
    params = pika.URLParameters(s.amqp_url)
    params.heartbeat = 60
    last: Exception | None = None
    for _ in range(retries):
        try:
            return pika.BlockingConnection(params)
        except pika.exceptions.AMQPConnectionError as exc:  # broker 還沒起來
            last = exc
            time.sleep(1)
    raise RuntimeError(f"cannot connect to RabbitMQ: {last}")


def declare_topology(ch: pika.adapters.blocking_connection.BlockingChannel, s: Settings) -> list[str]:
    """宣告所有 exchange / queue。重複執行不會改變任何東西（同參數 declare 是冪等的）。"""
    ch.exchange_declare(EX_JOBS, exchange_type="direct", durable=True)
    ch.exchange_declare(EX_FETCHED, exchange_type="fanout", durable=True)
    ch.exchange_declare(EX_DLX, exchange_type="direct", durable=True)
    created = []
    for source in s.sources:
        dlq = crawl_dlq(source)
        ch.queue_declare(dlq, durable=True)
        ch.queue_bind(dlq, EX_DLX, routing_key=dlq)
        q = crawl_queue(source)
        ch.queue_declare(q, durable=True, arguments={
            "x-queue-type": "quorum",
            "x-delivery-limit": s.crawl_max_attempts - 1,
            "x-dead-letter-exchange": EX_DLX,
            "x-dead-letter-routing-key": dlq,
        })
        ch.queue_bind(q, EX_JOBS, routing_key=source)
        created += [q, dlq]
    ch.queue_declare(Q_PARSER_DLQ, durable=True)
    ch.queue_bind(Q_PARSER_DLQ, EX_DLX, routing_key=Q_PARSER_DLQ)
    ch.queue_declare(Q_PARSER, durable=True, arguments={
        "x-queue-type": "quorum",
        "x-delivery-limit": s.parser_max_attempts - 1,
        "x-dead-letter-exchange": EX_DLX,
        "x-dead-letter-routing-key": Q_PARSER_DLQ,
    })
    ch.queue_bind(Q_PARSER, EX_FETCHED)
    ch.queue_declare(Q_AUDIT, durable=True, arguments={"x-message-ttl": 7 * 24 * 3600 * 1000, "x-max-length": 100000})
    ch.queue_bind(Q_AUDIT, EX_FETCHED)
    return created + [Q_PARSER, Q_PARSER_DLQ, Q_AUDIT]


def publish_json(ch, exchange: str, routing_key: str, body: dict | bytes, *, message_id: str,
                 headers: dict | None = None) -> None:
    payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    ch.basic_publish(
        exchange=exchange,
        routing_key=routing_key,
        body=payload,
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,  # persistent
            message_id=message_id,
            headers=headers or {},
            timestamp=int(time.time()),
        ),
        mandatory=True,
    )


# ---------------------------------------------------------------- Avro 事件
_SCHEMA_CACHE: dict[str, Any] = {}


def load_schema(path: str) -> Any:
    if path not in _SCHEMA_CACHE:
        with open(path, encoding="utf-8") as fh:
            _SCHEMA_CACHE[path] = fastavro.parse_schema(json.load(fh))
    return _SCHEMA_CACHE[path]


def make_event_id(job_id: str, content_sha256: str) -> str:
    # 決定性：同一個 job 抓到同一份內容 -> 同一個 event_id（重送時下游可以用 UNIQUE(event_id) 去重）
    return "evt-" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"qsearch:{job_id}:{content_sha256}"))


def build_event(*, event_id: str, job_id: str, platform: str, raw_uri: str) -> dict:
    return {
        "event_id": event_id,
        "schema_version": 1,
        "job_id": job_id,
        "platform": platform,
        "event_type": "crawl.fetched",
        "raw_data_uri": raw_uri,
    }


def validate_event(event: dict, schema_path: str) -> dict:
    """用 fastavro 驗證（strict：多欄位也算錯），再用 Avro JSON 編碼讀回一次。"""
    schema = load_schema(schema_path)
    fastavro.validation.validate(event, schema, raise_errors=True, strict=True)
    wire = json.dumps(event, ensure_ascii=False)
    decoded = list(fastavro.json_reader(io.StringIO(wire), schema))
    if decoded != [event]:
        raise ValueError("Avro JSON round-trip mismatch")
    if event["event_type"] != "crawl.fetched" or event["schema_version"] != 1:
        raise ValueError("unsupported event_type/schema_version")
    if not event["raw_data_uri"].startswith("s3://"):
        raise ValueError("raw_data_uri must be s3:// on-prem")
    return event


def split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3 uri: {uri}")
    bucket, _, key = uri[5:].partition("/")
    return bucket, key


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- PostgreSQL
PG_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_bigm;

-- 排／抓：job 表。對應 Cloud Tasks 任務；同時是 replay（取代 Pub/Sub Seek）的索引。
CREATE TABLE IF NOT EXISTS crawl_jobs (
  job_id          text PRIMARY KEY,
  source          text NOT NULL,
  board           text,
  kind            text NOT NULL,
  fixture         text NOT NULL,
  window_key      text NOT NULL,
  status          text NOT NULL DEFAULT 'scheduled',  -- scheduled | queued | fetched
  scheduled_at    timestamptz NOT NULL DEFAULT now(),
  queued_at       timestamptz,
  fetched_at      timestamptz,
  raw_uri         text,
  content_sha256  text,
  event_id        text UNIQUE,
  fetch_attempts  int NOT NULL DEFAULT 0,
  last_error      text
);

-- 解：inbox 表。UNIQUE(event_id) 是冪等的關鍵；重複投遞只會讓 deliveries +1。
CREATE TABLE IF NOT EXISTS ingest_events (
  id                bigserial PRIMARY KEY,
  event_id          text NOT NULL,
  job_id            text NOT NULL,
  platform          text NOT NULL,
  event_type        text NOT NULL,
  schema_version    int  NOT NULL,
  raw_data_uri      text NOT NULL,
  post_count        int,
  deliveries        int  NOT NULL DEFAULT 1,
  processed_count   int  NOT NULL DEFAULT 0,
  pg_done           boolean NOT NULL DEFAULT false,
  ch_done           boolean NOT NULL DEFAULT false,
  os_done           boolean NOT NULL DEFAULT false,
  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  last_processed_at timestamptz,
  CONSTRAINT ingest_events_event_id_key UNIQUE (event_id)
);

-- 用：PTT 文章（標題／作者／日期，文章頁另有內文）。
CREATE TABLE IF NOT EXISTS ptt_posts (
  post_id          text PRIMARY KEY,
  board            text NOT NULL,
  title            text NOT NULL,
  author           text,
  posted_at        timestamptz,
  list_date        text,
  nrec             text,
  body             text,
  url              text,
  source_event_id  text NOT NULL,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ptt_posts_title_bigm_idx ON ptt_posts USING gin (title gin_bigm_ops);
CREATE INDEX IF NOT EXISTS ptt_posts_body_bigm_idx  ON ptt_posts USING gin (body gin_bigm_ops);

-- 每個訊息被處理的次數（證明「重試 N 次後才進 DLQ」）。
CREATE TABLE IF NOT EXISTS delivery_attempts (
  stage        text NOT NULL,
  message_key  text NOT NULL,
  attempts     int  NOT NULL DEFAULT 0,
  first_at     timestamptz NOT NULL DEFAULT now(),
  last_at      timestamptz NOT NULL DEFAULT now(),
  last_error   text,
  PRIMARY KEY (stage, message_key)
);
"""


def pg_connect(s: Settings, retries: int = 60) -> psycopg.Connection:
    last: Exception | None = None
    for _ in range(retries):
        try:
            return psycopg.connect(s.pg_dsn, autocommit=True)  # 交易一律用 pg_tx() 明確包起來
        except psycopg.OperationalError as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(f"cannot connect to PostgreSQL: {last}")


@contextmanager
def pg_tx(conn: psycopg.Connection) -> Iterator[psycopg.Cursor]:
    with conn.transaction():
        with conn.cursor() as cur:
            yield cur


def record_attempt(conn: psycopg.Connection, stage: str, key: str, error: str | None = None) -> int:
    with pg_tx(conn) as cur:
        cur.execute(
            """INSERT INTO delivery_attempts (stage, message_key, attempts, last_error)
               VALUES (%s, %s, 1, %s)
               ON CONFLICT (stage, message_key)
               DO UPDATE SET attempts = delivery_attempts.attempts + 1, last_at = now(),
                             last_error = COALESCE(EXCLUDED.last_error, delivery_attempts.last_error)
               RETURNING attempts""",
            (stage, key, error),
        )
        return cur.fetchone()[0]


def set_attempt_error(conn: psycopg.Connection, stage: str, key: str, error: str) -> None:
    with pg_tx(conn) as cur:
        cur.execute("UPDATE delivery_attempts SET last_error=%s WHERE stage=%s AND message_key=%s",
                    (error[:500], stage, key))


# ---------------------------------------------------------------- ClickHouse
CH_SCHEMA = [
    "CREATE DATABASE IF NOT EXISTS {db}",
    # 對應 BigQuery sentiment_analytics.ingest_events：依日分區、90 天到期（BQ expiration_ms=7776000000）。
    """CREATE TABLE IF NOT EXISTS {db}.ingest_events (
         event_id String,
         schema_version Int32,
         job_id String,
         platform LowCardinality(String),
         event_type LowCardinality(String),
         raw_data_uri String,
         post_count UInt32,
         ingested_at DateTime64(3, 'UTC')
       ) ENGINE = MergeTree
       PARTITION BY toDate(ingested_at)
       ORDER BY (platform, ingested_at, event_id)
       TTL toDateTime(ingested_at) + INTERVAL 90 DAY""",
]


def ch_client(s: Settings):
    import clickhouse_connect

    last: Exception | None = None
    for _ in range(60):
        try:
            return clickhouse_connect.get_client(host=s.ch_host, port=s.ch_port, username=s.ch_user,
                                                 password=s.ch_password)
        except Exception as exc:  # noqa: BLE001 - server 還沒起來
            last = exc
            time.sleep(1)
    raise RuntimeError(f"cannot connect to ClickHouse: {last}")


# ---------------------------------------------------------------- OpenSearch
OS_INDEX_BODY = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "post_id": {"type": "keyword"},
            "board": {"type": "keyword"},
            # 內建 cjk analyzer = 中日韓字元 bigram；與 pg_bigm 的 2-gram 概念一致（正式斷詞見 Q6）。
            "title": {"type": "text", "analyzer": "cjk"},
            "body": {"type": "text", "analyzer": "cjk"},
            "author": {"type": "keyword"},
            "posted_at": {"type": "date"},
            "nrec": {"type": "keyword"},
            "source_event_id": {"type": "keyword"},
        }
    },
}


def os_client(s: Settings):
    from opensearchpy import OpenSearch

    return OpenSearch(hosts=[s.opensearch_url], timeout=30, max_retries=3, retry_on_timeout=True)


def ensure_os_index(client, index: str) -> bool:
    if client.indices.exists(index=index):
        return False
    client.indices.create(index=index, body=OS_INDEX_BODY)
    return True


# ---------------------------------------------------------------- MinIO / Redis
def minio_client(s: Settings):
    from minio import Minio

    return Minio(s.s3_endpoint, access_key=s.s3_access_key, secret_key=s.s3_secret_key, secure=False)


def redis_client(s: Settings):
    import redis

    return redis.Redis.from_url(s.redis_url, decode_responses=True)
