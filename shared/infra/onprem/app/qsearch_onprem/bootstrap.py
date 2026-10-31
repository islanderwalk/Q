"""一次性初始化（對應 Terraform 建立 bucket／topic／subscription／資料表）。重複執行是冪等的。

python -m qsearch_onprem.bootstrap
"""
from __future__ import annotations

import json

from . import common
from .config import settings


def main() -> int:
    common.setup_logging()
    s = settings()
    out: dict[str, object] = {}

    conn = common.pg_connect(s)
    with common.pg_tx(conn) as cur:
        cur.execute(common.PG_SCHEMA)
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_bigm'")
        out["pg_bigm"] = cur.fetchone()[0]
    conn.close()

    ch = common.ch_client(s)
    for stmt in common.CH_SCHEMA:
        ch.command(stmt.format(db=s.ch_database))
    out["clickhouse_version"] = ch.server_version

    osc = common.os_client(s)
    out["opensearch_index_created"] = common.ensure_os_index(osc, s.os_index)

    mc = common.minio_client(s)
    if not mc.bucket_exists(s.raw_bucket):
        mc.make_bucket(s.raw_bucket)
        out["bucket_created"] = s.raw_bucket
    else:
        out["bucket_created"] = None

    rc = common.redis_client(s)
    out["redis_appendonly"] = rc.config_get("appendonly").get("appendonly")

    rconn = common.rabbit_connect(s)
    chan = rconn.channel()
    out["rabbit_queues"] = common.declare_topology(chan, s)
    rconn.close()

    print(json.dumps({"bootstrap": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
