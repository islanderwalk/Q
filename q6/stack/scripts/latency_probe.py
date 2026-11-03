"""查詢延遲探針：回補進行中，PM 文字雲的查詢會不會變慢？

輪流打 API（cache=0，ES request_cache 也關）四種範圍，每筆記下當下的回補狀態（PG 帳本：限速、跑中分區數），
寫成 JSON Lines，事後依「閒置／不限速回補／限速回補」分組算 p50／p95。只讀 terms_v1（回補不動它），量的是寫入對查詢的干擾。

用法：python latency_probe.py <輸出.jsonl> [秒數=600] [間隔秒=0.5]
"""
import json
import sys
import time
import urllib.parse
import urllib.request

import psycopg

API = "http://localhost:8080/api/wordcloud"
PG = "postgresql://qsearch:qsearch@localhost:55433/qsearch"
SCOPES = {
    "all": {},
    "1y": {"date_from": "2025-10-30"},
    "1m": {"date_from": "2026-10-01"},
    "kw_高雄": {"q": "高雄"},
}


def main():
    out, secs = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 600
    gap = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    conn = psycopg.connect(PG, autocommit=True)
    end = time.time() + secs
    names = list(SCOPES)
    i = 0
    with open(out, "a", encoding="utf-8") as f:
        while time.time() < end:
            name = names[i % len(names)]
            i += 1
            qs = urllib.parse.urlencode({"seg": "v1", "cache": 0, "size": 60, **SCOPES[name]})
            t0 = time.perf_counter()
            r = json.load(urllib.request.urlopen(f"{API}?{qs}", timeout=300))
            wall = (time.perf_counter() - t0) * 1000
            rps, paused = conn.execute("SELECT max_docs_per_sec, paused FROM backfill_control WHERE id=1").fetchone()
            running = conn.execute("SELECT count(*) FROM backfill_ledger WHERE status='running' AND heartbeat_at > now() - interval '30 seconds'").fetchone()[0]
            f.write(json.dumps({"ts": time.time(), "scope": name, "wall_ms": round(wall, 1), "es_took_ms": r["es_took_ms"],
                                "total_docs": r["total_docs"], "max_docs_per_sec": rps, "paused": paused, "running": running}) + "\n")
            f.flush()
            time.sleep(gap)


if __name__ == "__main__":
    main()
