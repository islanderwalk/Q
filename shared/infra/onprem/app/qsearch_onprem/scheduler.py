"""排：scheduler tick（對應 Cloud Scheduler crawl-tick -> scheduler Cloud Run -> Cloud Tasks）。

python -m qsearch_onprem.scheduler tick [--window YYYYMMDD]   # 跑一次
python -m qsearch_onprem.scheduler loop                       # 常駐，每 TICK_INTERVAL_SECONDS 秒一次

冪等：job_id = job-<source>-<slug>-<window>，同一個時間窗重複 tick 不會產生新 job，也不會重送已送出的 job。
「抓」只讀 repo 的 fixture（FIXTURE_ROOT 底下），不連外。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone

from . import common
from .config import Settings, settings

TPE = timezone(timedelta(hours=8))

# 每個來源一條佇列（對應 Cloud Tasks crawl-fb / crawl-yt / crawl-news；這裡只有 PTT 有 fixture）。
SOURCES: dict[str, dict] = {
    "ptt": {
        "board": "Gossiping",
        "fixtures": [
            {"slug": "index-latest", "kind": "ptt_index", "path": "capture/ptt_index_latest.html"},
            {"slug": "index-39386", "kind": "ptt_index", "path": "capture/ptt_index_39386.html"},
            {"slug": "article-b5b", "kind": "ptt_article", "path": "capture/ptt_article.html"},
            {"slug": "titles-7d", "kind": "ptt_titles_jsonl", "path": "q6/ptt_titles_7d.jsonl"},
        ],
    },
}


def window_now() -> str:
    return datetime.now(TPE).strftime("%Y%m%d")


def tick(s: Settings, window: str | None = None) -> dict:
    window = window or window_now()
    conn = common.pg_connect(s)
    inserted = 0
    with common.pg_tx(conn) as cur:
        for source in s.sources:
            cfg = SOURCES.get(source)
            if not cfg:
                continue
            for fx in cfg["fixtures"]:
                job_id = f"job-{source}-{fx['slug']}-{window}"
                cur.execute(
                    """INSERT INTO crawl_jobs (job_id, source, board, kind, fixture, window_key)
                       VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (job_id) DO NOTHING""",
                    (job_id, source, cfg["board"], fx["kind"], fx["path"], window),
                )
                inserted += cur.rowcount
        cur.execute(
            "SELECT job_id, source, board, kind, fixture, window_key FROM crawl_jobs "
            "WHERE status = 'scheduled' ORDER BY job_id FOR UPDATE SKIP LOCKED"
        )
        pending = cur.fetchall()

    rconn = common.rabbit_connect(s)
    ch = rconn.channel()
    ch.confirm_delivery()
    published = []
    for job_id, source, board, kind, fixture, wkey in pending:
        msg = {"job_id": job_id, "source": source, "board": board, "kind": kind,
               "fixture": fixture, "window_key": wkey}
        common.publish_json(ch, common.EX_JOBS, source, msg, message_id=job_id)  # confirm 失敗會丟例外
        with common.pg_tx(conn) as cur:
            cur.execute("UPDATE crawl_jobs SET status='queued', queued_at=now() WHERE job_id=%s AND status='scheduled'",
                        (job_id,))
        published.append(job_id)
    rconn.close()
    conn.close()
    return {"window": window, "inserted": inserted, "published": len(published), "job_ids": published}


def main() -> int:
    common.setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["tick", "loop"])
    ap.add_argument("--window")
    args = ap.parse_args()
    s = settings()
    if args.mode == "tick":
        print(json.dumps(tick(s, args.window), ensure_ascii=False))
        return 0
    interval = int(os.environ.get("TICK_INTERVAL_SECONDS", "900"))  # Cloud Scheduler 每 15 分鐘
    if os.environ.get("TICK_ON_START", "false").lower() != "true":
        time.sleep(interval)
    while True:
        try:
            common.log.info("tick %s", json.dumps(tick(s), ensure_ascii=False))
        except Exception:  # noqa: BLE001 - 下次 tick 再試
            common.log.exception("tick failed")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
