"""環境變數設定。名稱對應 GCP 版本的資源（見 shared/infra/gcp）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"environment variable {name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    # 派：RabbitMQ（取代 Cloud Tasks 佇列 + Pub/Sub 主題/訂閱）
    amqp_url: str = field(default_factory=lambda: _env("AMQP_URL"))
    rabbit_mgmt_url: str = field(default_factory=lambda: _env("RABBITMQ_MGMT_URL", "http://rabbitmq:15672"))
    rabbit_user: str = field(default_factory=lambda: _env("RABBITMQ_USER"))
    rabbit_password: str = field(default_factory=lambda: _env("RABBITMQ_PASSWORD"))
    # 存：MinIO（取代 GCS raw-scraped）
    s3_endpoint: str = field(default_factory=lambda: _env("S3_ENDPOINT", "minio:9000"))
    s3_access_key: str = field(default_factory=lambda: _env("S3_ACCESS_KEY"))
    s3_secret_key: str = field(default_factory=lambda: _env("S3_SECRET_KEY"))
    raw_bucket: str = field(default_factory=lambda: _env("RAW_BUCKET", "qsearch-raw-scraped"))
    # 解：PostgreSQL + pg_bigm（取代 Cloud SQL）
    pg_dsn: str = field(default_factory=lambda: _env("PG_DSN"))
    # 用：ClickHouse（取代 BigQuery ingest_events）、OpenSearch（取代 Elasticsearch）、Redis（取代 Firestore session 池）
    ch_host: str = field(default_factory=lambda: _env("CLICKHOUSE_HOST", "clickhouse"))
    ch_port: int = field(default_factory=lambda: int(_env("CLICKHOUSE_PORT", "8123")))
    ch_user: str = field(default_factory=lambda: _env("CLICKHOUSE_USER"))
    ch_password: str = field(default_factory=lambda: _env("CLICKHOUSE_PASSWORD"))
    ch_database: str = field(default_factory=lambda: _env("CLICKHOUSE_DB", "qsearch"))
    opensearch_url: str = field(default_factory=lambda: _env("OPENSEARCH_URL", "http://opensearch:9200"))
    os_index: str = field(default_factory=lambda: _env("OPENSEARCH_INDEX", "ptt-posts"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://redis:6379/0"))
    # 抓：只讀 repo 內的 fixture，不連外
    fixture_root: str = field(default_factory=lambda: _env("FIXTURE_ROOT", "/fixtures"))
    schema_path: str = field(default_factory=lambda: _env("SCHEMA_PATH", "/schemas/qsearch-crawl-event-v1.avsc"))
    # 重試：對應 Cloud Tasks retry_config.max_attempts=5 與 parser 訂閱 max_delivery_attempts=5
    crawl_max_attempts: int = field(default_factory=lambda: int(_env("CRAWL_MAX_ATTEMPTS", "5")))
    parser_max_attempts: int = field(default_factory=lambda: int(_env("PARSER_MAX_ATTEMPTS", "5")))
    retry_backoff_seconds: float = field(default_factory=lambda: float(_env("RETRY_BACKOFF_SECONDS", "0.5")))
    # 對應 Cloud Tasks rate_limits.max_dispatches_per_second=5
    crawl_rate_per_second: int = field(default_factory=lambda: int(_env("CRAWL_RATE_PER_SECOND", "5")))
    sources: tuple[str, ...] = field(default_factory=lambda: tuple(_env("CRAWL_SOURCES", "ptt").split(",")))


def settings() -> Settings:
    return Settings()
