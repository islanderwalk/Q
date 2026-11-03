#!/usr/bin/env python3
"""QSearch 第 2 題：在已建好索引的 fb_posts 上，用指定的中文詞比較兩字詞索引與 pg_trgm 的查詢。

run_q2.py 只用第 1 列內文的前 4 字當查詢詞；這支腳本補上線上查詢最常見的 2 字、3 字詞，
看兩種索引的結果數是否一致、查詢時間差多少（中文 2 字詞湊不出完整三字詞，是 pg_trgm 的弱點）。

用法：
    python query_bench.py --db q2_wiki --terms 台灣 颱風 捷運站 中華民國 --out q2_query_bench_wiki.json
"""
import argparse
import json
import platform
from datetime import datetime
from pathlib import Path

import psycopg

import run_q2

QUERIES = ("bigram_contains", "trgm_like")
OPTIONAL = ("pg_bigm_like",)  # pg_bigm 擴充套件（sql/06_pg_bigm.sql；run_q2.py --pg-bigm 建的資料庫）


def main():
    ap = argparse.ArgumentParser(description="QSearch Q2：指定詞的查詢比較")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=55432)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--db", required=True)
    ap.add_argument("--terms", nargs="+", required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--queries", nargs="+", default=list(QUERIES), choices=[*QUERIES, *OPTIONAL])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sections = run_q2.read_sections(run_q2.SQL_DIR / "05_queries.sql", "query")
    sections["pg_bigm_like"] = run_q2.read_sections(run_q2.SQL_DIR / "06_pg_bigm.sql", "pg_bigm")["like"]
    queries = {k: sections[k] for k in args.queries}
    result = {"started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "python": platform.python_version(), "params": vars(args), "terms": {}}
    with psycopg.connect(host=args.host, port=args.port, user=args.user, dbname=args.db, autocommit=True) as c:
        result["server"] = c.execute("SELECT version()").fetchone()[0]
        result["rows"] = c.execute("SELECT count(*) FROM fb_posts").fetchone()[0]
        for term in args.terms:
            r = run_q2.run_queries_isolated(c, queries, {"term": term}, repeats=args.repeats)
            r["counts_equal"] = len({v["count"] for v in r.values()}) == 1
            result["terms"][term] = r
            print(term, {k: (v["count"], v["median_ms"]) for k, v in r.items() if k in queries},
                  "equal" if r["counts_equal"] else "DIFFERENT", flush=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
