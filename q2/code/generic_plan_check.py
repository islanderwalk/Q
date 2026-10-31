#!/usr/bin/env python3
"""QSearch 第 2 題：查第一輪 query_bench 裡 pg_trgm 2 字詞 9.5、10 秒的原因。

第一輪 query_bench 在同一條連線上對每個詞各跑 5 次同一句 LIKE。psycopg 預設同一句 SQL 執行第 6 次起
改成伺服器端 prepared statement，PostgreSQL 對 prepared statement 前 5 次用 custom plan，之後若
generic plan（不看參數）估得比較便宜就改用它。generic plan 不知道查詢詞只有 2 個字，會走 pg_trgm 索引，
而 2 字詞抽不出三字詞，索引等於回傳全部列再逐列 recheck。EXPLAIN 是另一句 SQL，看到的仍是 custom plan。

這支腳本在已建好 pg_trgm 索引的 fb_posts 上：
  1. 依序對每個詞跑 run_q2.run_queries，一次用 psycopg 預設（prepare=None，第一輪的行為），一次用 prepare=False（修正後）；
  2. 用 PREPARE 加 plan_cache_mode 直接比 custom 與 generic plan 的 EXPLAIN ANALYZE。
資料庫裡有 pg_bigm 索引時（它也能處理 LIKE），每段都在交易內先拿掉、量完 rollback。

用法：
    python generic_plan_check.py --port 55433 --db q2_ptt_bigm --terms 台灣 高雄 颱風 政府 捷運站 柯文哲 中華民國 \\
        --explain-terms 颱風 政府 --out q2_generic_plan_check_ptt.json
"""
import argparse
import json
import platform
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import sql as psql

import run_q2


def drop_rival(c, rival):
    if rival:
        c.execute(psql.SQL("DROP INDEX {}").format(psql.Identifier(rival)))


def main():
    ap = argparse.ArgumentParser(description="QSearch Q2：psycopg 自動 prepare 與 generic plan 的影響")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=55432)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--db", required=True)
    ap.add_argument("--terms", nargs="+", required=True)
    ap.add_argument("--explain-terms", nargs="+", default=[])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    text = run_q2.read_sections(run_q2.SQL_DIR / "05_queries.sql", "query")["trgm_like"]
    result = {"started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "python": platform.python_version(), "psycopg": psycopg.__version__,
              "params": vars(args), "runs": {}, "explain": {}}
    with psycopg.connect(host=args.host, port=args.port, user=args.user, dbname=args.db, autocommit=True) as c:
        result["server"] = c.execute("SELECT version()").fetchone()[0]
        result["rows"] = c.execute("SELECT count(*) FROM fb_posts").fetchone()[0]
        rival = run_q2.LIKE_RIVAL["trgm_like"]
        rival = rival if c.execute("SELECT to_regclass(%s)", (rival,)).fetchone()[0] else None
        result["rival_dropped_in_tx"] = rival

        # 1. 同一個交易裡依序跑每個詞（中間沒有 DROP、ROLLBACK，psycopg 的 prepare 狀態不會被清掉）
        for label, prepare in (("psycopg_default", None), ("prepare_false", False)):
            runs = {}
            with c.transaction(force_rollback=True):
                drop_rival(c, rival)
                for term in args.terms:
                    runs[term] = run_q2.run_queries(c, {"trgm_like": text}, {"term": term},
                                                    args.repeats, prepare=prepare)["trgm_like"]
                    print(label, term, runs[term]["count"], runs[term]["median_ms"], runs[term]["plan_uses"],
                          flush=True)
            result["runs"][label] = runs

        # 2. 直接比 custom 與 generic plan（EXPLAIN 不能帶參數，查詢詞用 Literal）
        with c.transaction(force_rollback=True):
            drop_rival(c, rival)
            c.execute("PREPARE q2_trgm_like(text) AS SELECT count(*) FROM fb_posts WHERE message LIKE ('%' || $1 || '%')")
            for term in args.explain_terms:
                result["explain"][term] = {}
                for mode in ("force_custom_plan", "force_generic_plan"):
                    c.execute(f"SET LOCAL plan_cache_mode = {mode}")
                    plan = c.execute(psql.SQL("EXPLAIN (ANALYZE, FORMAT JSON) EXECUTE q2_trgm_like({})")
                                     .format(psql.Literal(term))).fetchone()[0]
                    result["explain"][term][mode] = {
                        "plan_uses": run_q2.find_index_names(plan),
                        "rows": plan[0]["Plan"]["Actual Rows"],
                        "execution_ms": round(plan[0]["Execution Time"], 3),
                    }
                    print("explain", term, mode, result["explain"][term][mode], flush=True)
            c.execute("DEALLOCATE q2_trgm_like")

    result["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
