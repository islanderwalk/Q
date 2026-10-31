"""回放：地端對 Pub/Sub Seek 的替代方案。

RabbitMQ 已 ack 的訊息不會保留，所以不能「把訂閱倒回某個時間點」。
這裡改用 crawl_jobs 表（哪些 job 抓過、事件 ID、原始檔位置）＋ MinIO 原始檔重新產生事件：

  python -m qsearch_onprem.replay --mode redeliver [--since ISO8601] [--wait]
      重新投遞同一批事件（同 event_id），等同 Seek 後重播；下游靠 UNIQUE(event_id) 去重。
  python -m qsearch_onprem.replay --mode reparse [--since ISO8601] [--wait]
      要求 parser 從 MinIO 重新讀檔、重新解析並 upsert（Q6 換斷詞後的回朔就是這種）；不重複寫 ClickHouse。

每個事件送出前會先確認 MinIO 物件存在且 sha256 與 job 表一致，避免回放出不存在的資料。
"""
from __future__ import annotations

import argparse
import json
import time

from . import common
from .config import Settings, settings


def _snapshot(conn, event_ids: list[str]) -> dict[str, tuple[int, int, bool]]:
    with common.pg_tx(conn) as cur:
        cur.execute("SELECT event_id, deliveries, processed_count, (pg_done AND ch_done AND os_done) "
                    "FROM ingest_events WHERE event_id = ANY(%s)", (event_ids,))
        return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


def replay(s: Settings, mode: str, since: str | None = None, wait: bool = True, timeout: float = 300) -> dict:
    conn = common.pg_connect(s)
    mc = common.minio_client(s)
    with common.pg_tx(conn) as cur:
        q = ("SELECT job_id, source, event_id, raw_uri, content_sha256 FROM crawl_jobs "
             "WHERE status = 'fetched' AND event_id IS NOT NULL")
        params: list = []
        if since:
            q += " AND fetched_at >= %s"
            params.append(since)
        cur.execute(q + " ORDER BY job_id", params)
        jobs = cur.fetchall()
    before = _snapshot(conn, [j[2] for j in jobs])

    rconn = common.rabbit_connect(s)
    ch = rconn.channel()
    ch.confirm_delivery()
    sent, missing = [], []
    for job_id, source, event_id, raw_uri, digest in jobs:
        bucket, key = common.split_s3_uri(raw_uri)
        try:
            st = mc.stat_object(bucket, key)
        except Exception:  # noqa: BLE001
            missing.append(raw_uri)
            continue
        if (st.metadata or {}).get("x-amz-meta-sha256") != digest:
            missing.append(raw_uri)
            continue
        event = common.validate_event(
            common.build_event(event_id=event_id, job_id=job_id, platform=source, raw_uri=raw_uri), s.schema_path)
        common.publish_json(ch, common.EX_FETCHED, "", event, message_id=event_id,
                            headers={"schema": "qsearch-crawl-event-v1", "x-replay": mode})
        sent.append(event_id)
    rconn.close()

    result = {"mode": mode, "since": since, "jobs": len(jobs), "replayed": len(sent), "missing_raw": missing,
              "inbox_rows_before": len(before)}
    if wait and sent:
        # 每個重送的事件：inbox 列必須存在、三個 done 旗標為真；原本就有的列 deliveries 要 +1，
        # reparse 模式另外要求 processed_count +1（證明真的從 MinIO 重讀重解析）。
        deadline = time.time() + timeout
        while True:
            now = _snapshot(conn, sent)
            pending = []
            for eid in sent:
                cur_row = now.get(eid)
                old = before.get(eid)
                if cur_row is None or not cur_row[2]:
                    pending.append(eid)
                elif old is not None and cur_row[0] <= old[0]:
                    pending.append(eid)
                elif mode == "reparse" and old is not None and cur_row[1] <= old[1]:
                    pending.append(eid)
            if not pending:
                break
            if time.time() > deadline:
                raise TimeoutError(f"replay not drained, pending={pending}")
            time.sleep(0.5)
        result.update({
            "deliveries_before": sum(v[0] for v in before.values()),
            "deliveries_after": sum(v[0] for v in now.values()),
            "processed_before": sum(v[1] for v in before.values()),
            "processed_after": sum(v[1] for v in now.values()),
        })
    conn.close()
    return result


def main() -> int:
    common.setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["redeliver", "reparse"], default="redeliver")
    ap.add_argument("--since", help="只回放 fetched_at >= 此時間的 job（ISO-8601），對應 Seek 到時間點")
    ap.add_argument("--wait", action="store_true", help="等 parser 處理完才結束")
    ap.add_argument("--timeout", type=float, default=300)
    args = ap.parse_args()
    print(json.dumps(replay(settings(), args.mode, args.since, args.wait, args.timeout), ensure_ascii=False,
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
