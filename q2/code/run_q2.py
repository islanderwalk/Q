#!/usr/bin/env python3
"""QSearch 第 2 題：Postgres 16 實測（貼文表、粉絲頁 ID btree、三種中文全文索引）。

前置：PostgreSQL 16 叢集已在 127.0.0.1:55432 啟動（Windows：EDB 官方 binaries；Linux：Ubuntu postgresql-16，見 RESULTS.md）。

用法：
    python run_q2.py --rows 100000 --mode repeat
    python run_q2.py --rows 1000000 --mode repeat --bigram-timeout-min 30
    python run_q2.py --rows 100000 --mode distinct          # 敏感度：每列內文都不同
    python run_q2.py --rows 1000000 --mode corpus --corpus wiki_250.txt   # 真實中文（prepare_corpus.py 產生）

--mode repeat   重現頁面的造資料方式（1,000 段內文，每段連續重複）
--mode distinct 每列獨立隨機內文（均勻字頻，是體積的上限）
--mode corpus   內文取自真實中文語料檔，一行一則（齊夫字頻，最接近真實貼文）

輸出：--out（預設 q2/data/q2_run_<rows>_<mode>[-<語料檔名>].json）與 stdout 的摘要。
本腳本會 DROP/重建 fb_posts，只能在本機測試叢集上跑。
"""
import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql as psql

HERE = Path(__file__).resolve().parent
SQL_DIR = HERE / "sql"
DATA_DIR = HERE.parent / "data"
DEFAULT_TERM_LEN = 4
# LC_CTYPE 必須把 CJK 視為字母，pg_trgm 才抽得出中文三字詞（LC_CTYPE='C' 時一個都抽不出來，已實測）。
# Windows 的 locale 名稱是 zh-TW.UTF-8；Linux（glibc）沒有裝中文 locale 時用 C.UTF-8 即可。
DEFAULT_CTYPE = "zh-TW.UTF-8" if os.name == "nt" else "C.UTF-8"


def read_sections(path: Path, marker: str) -> dict:
    """依 '-- @<marker> <名稱>' 切段，回傳 {名稱: SQL 文字}。"""
    sections, name, buf = {}, None, []
    pat = re.compile(rf"^-- @{re.escape(marker)} (\S+)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if m:
            if name is not None:
                sections[name] = "\n".join(buf).strip()
            name, buf = m.group(1), []
        elif name is not None:
            buf.append(line)
    if name is not None:
        sections[name] = "\n".join(buf).strip()
    return sections


def generate_sql(mode: str, rows: int, corpus_rows: int = 0) -> str:
    text = (SQL_DIR / f"02_generate_{mode}.sql").read_text(encoding="utf-8")
    if "__ROWS__" not in text:
        raise ValueError("generate SQL 缺少 __ROWS__ 佔位符")
    text = text.replace("__ROWS__", str(int(rows)))
    if "__CORPUS_ROWS__" in text:
        if corpus_rows <= 0:
            raise ValueError("corpus 模式需要語料則數")
        text = text.replace("__CORPUS_ROWS__", str(int(corpus_rows)))
    return text


def read_corpus(path: Path) -> list:
    """語料檔：一行一則（只以 \\n 分行；tab、反斜線由 psycopg 的 COPY write_row 跳脫）。"""
    return [p for p in path.read_text(encoding="utf-8").split("\n") if p]


def load_corpus(c, posts: list) -> None:
    c.execute("DROP TABLE IF EXISTS q2_corpus")
    c.execute("CREATE TABLE q2_corpus (tid bigint PRIMARY KEY, body text NOT NULL)")
    with c.cursor().copy("COPY q2_corpus (tid, body) FROM STDIN") as cp:
        for i, body in enumerate(posts, start=1):
            cp.write_row((i, body))
    c.execute("ANALYZE q2_corpus")


def corpus_info(path: Path, posts: list) -> dict:
    return {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": len(posts),
        "avg_chars": round(sum(map(len, posts)) / len(posts), 1),
    }


def timed(fn):
    t0 = time.perf_counter()
    value = fn()
    return value, round(time.perf_counter() - t0, 2)


def find_index_names(plan) -> list:
    """從 EXPLAIN (FORMAT JSON) 結果遞迴找出用到的索引名稱與節點類型。"""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "Index Name" in node:
                found.append(node["Index Name"])
            if "Node Type" in node and node["Node Type"] in ("Seq Scan", "Bitmap Index Scan"):
                found.append(node["Node Type"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(plan)
    return sorted(set(found))


def ensure_database(host, port, user, db, ctype=DEFAULT_CTYPE):
    with psycopg.connect(host=host, port=port, user=user, dbname="postgres", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone():
            c.execute(psql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE {}")
                      .format(psql.Identifier(db), psql.Literal(ctype)))


def run_queries(c, queries: dict, params: dict, repeats: int = 3, prepare: bool | None = False) -> dict:
    # prepare=False：不讓 psycopg 自動 prepare。同一句 SQL 在同一條連線上執行到第 6 次會變成伺服器端的
    # prepared statement，再過 5 次 PostgreSQL 可能改用不看參數的 generic plan；query_bench 每個詞跑 5 次，
    # 第 3 個詞起計時量到的就可能是 generic plan，而 EXPLAIN 是另一句 SQL，看到的還是 custom plan。
    # prepare=None 是 psycopg 的預設行為，只給 generic_plan_check.py 重現舊結果用。
    out = {}
    for name, text in queries.items():
        p = {k: params[k] for k in re.findall(r"%\((\w+)\)s", text)}
        # 結果數
        count = c.execute(text, p, prepare=prepare).fetchone()[0]
        times = []
        c.execute(text, p, prepare=prepare).fetchall()  # 暖機
        for _ in range(repeats):
            t0 = time.perf_counter()
            c.execute(text, p, prepare=prepare).fetchall()
            times.append((time.perf_counter() - t0) * 1000)
        try:
            plan = c.execute("EXPLAIN (FORMAT JSON) " + text, p, prepare=prepare).fetchone()[0]
            indexes = find_index_names(plan)
            top = plan[0]["Plan"]["Node Type"]
        except psycopg.Error as exc:  # 計畫抓取失敗不影響計時結果
            indexes, top = [f"EXPLAIN error: {exc.__class__.__name__}"], None
        out[name] = {
            "count": int(count),
            "median_ms": round(statistics.median(times), 3),
            "all_ms": [round(t, 3) for t in times],
            "top_node": top,
            "plan_uses": indexes,
        }
    return out


# pg_trgm 與 pg_bigm 的索引都能處理 LIKE，兩個都在時規劃器只會挑其中一個（實測 pg_bigm 的查詢走了 pg_trgm 的索引）。
# 量其中一個時，在交易內先拿掉另一個，量完 rollback（索引不受影響）。
LIKE_RIVAL = {"trgm_like": "fb_posts_pg_bigm_gin", "pg_bigm_like": "fb_posts_trgm_gin"}


def run_queries_isolated(c, queries: dict, params: dict, repeats: int = 3) -> dict:
    out = {}
    for name, text in queries.items():
        rival = LIKE_RIVAL.get(name)
        if rival and c.execute("SELECT to_regclass(%s)", (rival,)).fetchone()[0]:
            with c.transaction(force_rollback=True):
                c.execute(psql.SQL("DROP INDEX {}").format(psql.Identifier(rival)))
                out.update(run_queries(c, {name: text}, params, repeats))
        else:
            out.update(run_queries(c, {name: text}, params, repeats))
    return out


def main():
    ap = argparse.ArgumentParser(description="QSearch 第 2 題 Postgres 實測")
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--mode", choices=["repeat", "distinct", "corpus"], default="repeat")
    ap.add_argument("--corpus", default=None, help="--mode corpus 的語料檔（prepare_corpus.py 的輸出）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=55432)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--db", default="q2_bench")
    ap.add_argument("--ctype", default=DEFAULT_CTYPE,
                    help=f"新建資料庫時的 LC_CTYPE（預設 {DEFAULT_CTYPE}）；資料庫已存在時沿用原設定")
    ap.add_argument("--maintenance-work-mem", default="1GB")
    ap.add_argument("--bigram-timeout-min", type=float, default=30.0,
                    help="bigram GIN 建立超過幾分鐘就取消（記為超時）")
    ap.add_argument("--skip-bigram", action="store_true")
    ap.add_argument("--pg-bigm", action="store_true",
                    help="另外建真的 pg_bigm 索引並查詢（sql/06_pg_bigm.sql；伺服器要裝 pg_bigm）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not re.fullmatch(r"\d+(MB|GB)", args.maintenance_work_mem):
        sys.exit("--maintenance-work-mem 只接受 如 512MB / 1GB")
    if (args.mode == "corpus") != bool(args.corpus):
        sys.exit("--mode corpus 與 --corpus 必須一起使用")
    tag = f"{args.mode}-{Path(args.corpus).stem}" if args.corpus else args.mode
    out_path = Path(args.out) if args.out else DATA_DIR / f"q2_run_{args.rows}_{tag}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn_kw = dict(host=args.host, port=args.port, user=args.user)
    ensure_database(**conn_kw, db=args.db, ctype=args.ctype)

    result = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "params": vars(args),
        "steps_seconds": {},
        "index_build": {},
        "measure": {},
        "queries": {},
    }

    with psycopg.connect(dbname=args.db, autocommit=True, **conn_kw) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        c.execute("CREATE EXTENSION IF NOT EXISTS pageinspect")
        c.execute(f"SET maintenance_work_mem = '{args.maintenance_work_mem}'")
        result["server"] = {
            "version": c.execute("SELECT version()").fetchone()[0],
            "shared_buffers": c.execute("SHOW shared_buffers").fetchone()[0],
            "maintenance_work_mem": c.execute("SHOW maintenance_work_mem").fetchone()[0],
            "max_parallel_maintenance_workers": c.execute("SHOW max_parallel_maintenance_workers").fetchone()[0],
            "block_size": c.execute("SHOW block_size").fetchone()[0],
            "datctype": c.execute("SELECT datctype FROM pg_database WHERE datname = current_database()").fetchone()[0],
            "os": f"{platform.system()} {platform.release()}",
        }
        print(f"[{result['started_at']}] {result['server']['version']}", flush=True)

        # 1) 建表
        c.execute((SQL_DIR / "01_schema.sql").read_text(encoding="utf-8"))

        # 2) 造資料
        corpus_rows = 0
        if args.corpus:
            posts = read_corpus(Path(args.corpus))
            result["corpus"] = corpus_info(Path(args.corpus), posts)
            _, secs = timed(lambda: load_corpus(c, posts))
            result["steps_seconds"]["load_corpus"] = secs
            corpus_rows = len(posts)
            del posts
            print(f"corpus {result['corpus']['file']}: {corpus_rows:,} rows, {secs} s", flush=True)
        _, secs = timed(lambda: c.execute(generate_sql(args.mode, args.rows, corpus_rows)))
        result["steps_seconds"]["generate_and_insert"] = secs
        print(f"generate {args.rows:,} rows ({args.mode}): {secs} s", flush=True)

        # 3) 索引（逐段計時）
        steps = read_sections(SQL_DIR / "03_indexes.sql", "step")
        for name, ddl in steps.items():
            if name == "bigram" and args.skip_bigram:
                result["index_build"][name] = {"skipped": True}
                continue
            if name == "bigram":
                c.execute(f"SET statement_timeout = '{int(args.bigram_timeout_min * 60000)}ms'")
            try:
                _, secs = timed(lambda: c.execute(ddl))
                result["index_build"][name] = {"seconds": secs, "timed_out": False}
                print(f"index {name}: {secs} s", flush=True)
            except psycopg.errors.QueryCanceled:
                result["index_build"][name] = {"timed_out": True,
                                               "limit_minutes": args.bigram_timeout_min}
                print(f"index {name}: TIMEOUT after {args.bigram_timeout_min} min（已取消）", flush=True)
            finally:
                if name == "bigram":
                    c.execute("SET statement_timeout = 0")
        bigm = read_sections(SQL_DIR / "06_pg_bigm.sql", "pg_bigm") if args.pg_bigm else {}
        if bigm:
            _, secs = timed(lambda: c.execute(bigm["index"]))
            result["index_build"]["pg_bigm"] = {"seconds": secs, "timed_out": False}
            result["server"]["pg_bigm"] = c.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'pg_bigm'").fetchone()[0]
            print(f"index pg_bigm: {secs} s (pg_bigm {result['server']['pg_bigm']})", flush=True)

        # 4) 量測（兩字詞索引沒建成時，--skip-bigram 或建立超時，不量它的 metapage、也不計時它的查詢）
        bigram_built = "seconds" in result["index_build"].get("bigram", {})
        measures = read_sections(SQL_DIR / "04_measure.sql", "query")
        if not bigram_built:
            measures.pop("gin_bigram_meta", None)
        if bigm:
            measures["gin_pg_bigm_meta"] = bigm["meta"]
        for name, text in measures.items():
            cur = c.execute(text)
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]
            result["measure"][name] = [
                {k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, row)} for row in rows
            ]
        print("measure:", json.dumps(result["measure"], ensure_ascii=False, default=str)[:600], flush=True)

        # 5) 查詢計時（同一個詞，三種索引；核對結果數一致）
        term = c.execute(f"SELECT substr(message, 1, {DEFAULT_TERM_LEN}) FROM fb_posts LIMIT 1").fetchone()[0]
        page_id = c.execute("SELECT page_id FROM fb_posts LIMIT 1").fetchone()[0]
        params = {"term": term, "page_id": page_id}
        queries = read_sections(SQL_DIR / "05_queries.sql", "query")
        if not bigram_built:
            queries.pop("bigram_contains", None)
        if bigm:
            queries["pg_bigm_like"] = bigm["like"]
        result["query_term"] = term
        result["queries"] = run_queries_isolated(c, queries, params)
        counts = {k: v["count"] for k, v in result["queries"].items()}
        # 鍵名沿用第一輪；有 --pg-bigm 時也比 pg_bigm_like（--skip-bigram 時比的就是 trgm_like 與 pg_bigm_like）
        same = len({counts[k] for k in ("bigram_contains", "trgm_like", "pg_bigm_like") if k in counts}) <= 1
        result["consistency"] = {"counts": counts, "bigram_equals_trgm_like": same}
        print("counts:", counts, "LIKE 類結果數一致:", same, flush=True)

    result["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
