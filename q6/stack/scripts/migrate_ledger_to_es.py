"""一次性遷移：把 PostgreSQL 的 backfill_ledger 表搬進 ES 索引 backfill_ledger（_id＝partition_id）。

- 只寫帳本索引；PG 表保留不動（不刪、不改），搬完用 --verify-only 或預設的對帳步驟比對各狀態數量。
- 用 op_type=create（ledger.create_many）：已存在的 partition_id 不覆寫、只回報 exists 數，所以重跑安全。
- 不碰 posts／posts_bq／scale_pilot_main 任何索引，也不 reset。

用法（在 q6 目錄下）：
  ES_URL=http://localhost:9200 PG_DSN=postgresql://qsearch:qsearch@127.0.0.1:55433/qsearch \
    python stack/scripts/migrate_ledger_to_es.py [--index backfill_ledger] [--verify-only]
"""
import argparse
import collections
import os
import pathlib
import sys
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "seg"))

import psycopg  # noqa: E402

from qseg import config  # noqa: E402
from qseg.ledger import Ledger  # noqa: E402

COLS = ("partition_id channel month target_seg_ver dict_version status raw_count raw_lines "
        "checkpoint_line written noop errors worker heartbeat_at started_at finished_at "
        "docs_per_sec gates attempts").split()


def pg_rows(dsn):
    with psycopg.connect(dsn) as c:
        cur = c.execute(f"SELECT {', '.join(COLS)} FROM backfill_ledger ORDER BY partition_id")
        out = []
        for r in cur.fetchall():
            d = dict(zip(COLS, r))
            if isinstance(d["docs_per_sec"], Decimal):
                d["docs_per_sec"] = float(d["docs_per_sec"])
            out.append(d)
        return out


def pg_counts(dsn):
    with psycopg.connect(dsn) as c:
        return dict(c.execute("SELECT status, count(*) FROM backfill_ledger GROUP BY status").fetchall())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=config.LEDGER_INDEX)
    ap.add_argument("--verify-only", action="store_true")
    a = ap.parse_args(argv)
    es_url = os.environ.get("ES_URL", config.ES_URL)
    dsn = os.environ.get("PG_DSN", config.PG_DSN)
    led = Ledger(es_url, a.index)
    if not a.verify_only:
        led.ensure_index()
        rows = pg_rows(dsn)
        res = led.create_many(rows)
        led.refresh()
        print(f"migrate: pg_rows={len(rows)} created={res['created']} exists={res['exists']}")
    pg = collections.Counter(pg_counts(dsn))
    es = led.counts_by_status()
    total_pg, total_es = sum(pg.values()), sum(es.values())
    print(f"{'status':<12}{'pg':>6}{'es':>6}")
    for st in sorted(set(pg) | set(es)):
        print(f"{st:<12}{pg.get(st, 0):>6}{es.get(st, 0):>6}")
    print(f"{'total':<12}{total_pg:>6}{total_es:>6}")
    ok = dict(pg) == dict(es)
    print("verify: " + ("OK, per-status counts match" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
