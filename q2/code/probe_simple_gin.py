#!/usr/bin/env python3
"""QSearch 第 2 題：追 `to_tsvector('simple')` GIN 體積在 Windows 比頁面大 43% 的原因。

在 run_q2.py 已建好的 fb_posts（repeat 模式）上重建 simple 索引，逐頁讀 GIN 頁（pageinspect）統計：
頁數、entry 數、每個 entry 葉頁放幾個 entry、葉頁空間使用率。

--nonalpha-from 用來驗證假設「Windows 不把較新的 CJK 統一漢字當字母」：把 U+<起點>..U+9FFF
先換成空白再建 simple 與 pg_trgm 索引，看體積是否和 Windows 實測（4,677,632 B／409,526,272 B）一致。

用法：
    python probe_simple_gin.py --db q2_linux --mem 64MB 1GB --nonalpha-from 9FBC 9FC4 9FCC --out probe.json
"""
import argparse
import json
import platform
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg

INDEX = "fb_posts_simple_probe"
DDL = f"CREATE INDEX {INDEX} ON fb_posts USING gin (to_tsvector('simple', message))"
CJK_END = 0x9FFF
# 把 [lo, hi] 的字換成空白；IMMUTABLE 才能放進索引運算式
STRIP_FN = """
CREATE OR REPLACE FUNCTION q2_strip_range(t text, lo int, hi int) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $f$
  SELECT translate(t, (SELECT string_agg(chr(c), '') FROM generate_series(lo, hi) c),
                      repeat(' ', hi - lo + 1))
$f$"""
BLOCK_BYTES = 8192
PAGE_HEADER_BYTES = 24
LINE_POINTER_BYTES = 4


def page_stats(rows: list) -> dict:
    """rows：每頁 (flags, lower, upper)。只統計 entry 樹的葉頁（flags 含 leaf、不含 data／meta）。"""
    leaf = [(lo, up) for flags, lo, up in rows
            if "leaf" in flags and "data" not in flags and "meta" not in flags]
    other = len(rows) - len(leaf)
    if not leaf:
        return {"entry_leaf_pages": 0, "other_pages": other}
    items = [(lo - PAGE_HEADER_BYTES) // LINE_POINTER_BYTES for lo, _ in leaf]
    used = [BLOCK_BYTES - (up - lo) for lo, up in leaf]
    return {
        "entry_leaf_pages": len(leaf),
        "other_pages": other,
        "entries_on_leaves": sum(items),
        "avg_entries_per_leaf": round(sum(items) / len(leaf), 3),
        "avg_leaf_fill": round(sum(used) / (len(leaf) * BLOCK_BYTES), 3),
        "entries_per_leaf_histogram": {str(k): items.count(k) for k in sorted(set(items))},
    }


def probe(c, mem: str, ddl: str = DDL) -> dict:
    c.execute(f"DROP INDEX IF EXISTS {INDEX}")
    c.execute(f"SET maintenance_work_mem = '{mem}'")
    t0 = time.perf_counter()
    c.execute(ddl)
    secs = round(time.perf_counter() - t0, 2)
    size = c.execute("SELECT pg_relation_size(%s)", (INDEX,)).fetchone()[0]
    meta = c.execute(
        "SELECT n_pending_pages, n_entries, n_total_pages, n_entry_pages, n_data_pages "
        "FROM gin_metapage_info(get_raw_page(%s, 0))", (INDEX,)).fetchone()
    rows = c.execute(
        "SELECT (gin_page_opaque_info(p)).flags, (page_header(p)).lower, (page_header(p)).upper "
        "FROM (SELECT get_raw_page(%s, b::int) AS p "
        "      FROM generate_series(1, pg_relation_size(%s) / 8192 - 1) AS b) AS s",
        (INDEX, INDEX)).fetchall()
    out = {
        "maintenance_work_mem": mem,
        "build_seconds": secs,
        "index_bytes": size,
        "pages": size // BLOCK_BYTES,
        "meta": dict(zip(["n_pending_pages", "n_entries", "n_total_pages", "n_entry_pages", "n_data_pages"], meta)),
    }
    out.update(page_stats([(list(f), lo, up) for f, lo, up in rows]))
    return out


def main():
    ap = argparse.ArgumentParser(description="QSearch Q2：simple GIN 體積差異追查")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=55432)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--db", default="q2_bench")
    ap.add_argument("--mem", nargs="+", default=["64MB", "1GB"])
    ap.add_argument("--nonalpha-from", nargs="*", default=[],
                    help="十六進位碼位（如 9FC4）：把該碼位到 U+9FFF 當成非字母再建索引")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    for m in args.mem:
        if not re.fullmatch(r"\d+(kB|MB|GB)", m):
            sys.exit(f"--mem 只接受如 64MB / 1GB：{m}")

    result = {"started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "python": platform.python_version(), "params": vars(args), "runs": []}
    with psycopg.connect(host=args.host, port=args.port, user=args.user, dbname=args.db, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS pageinspect")
        result["server"] = c.execute("SELECT version()").fetchone()[0]
        result["datctype"] = c.execute(
            "SELECT datctype FROM pg_database WHERE datname = current_database()").fetchone()[0]
        result["rows"] = c.execute("SELECT count(*) FROM fb_posts").fetchone()[0]
        result["lexemes_per_row"] = c.execute(
            "SELECT avg(length(to_tsvector('simple', message)))::float FROM fb_posts").fetchone()[0]
        for m in args.mem:
            r = probe(c, m)
            result["runs"].append(r)
            print(json.dumps({k: r[k] for k in ("maintenance_work_mem", "index_bytes", "pages",
                                                "build_seconds", "avg_entries_per_leaf", "avg_leaf_fill")},
                             ensure_ascii=False), flush=True)
        if args.nonalpha_from:
            c.execute(STRIP_FN)
            result["nonalpha"] = []
            for h in args.nonalpha_from:
                lo = int(h, 16)
                expr = f"q2_strip_range(message, {lo}, {CJK_END})"
                r = {"from": f"U+{lo:04X}", "chars_stripped": CJK_END - lo + 1}
                for kind, ddl in (("simple", f"CREATE INDEX {INDEX} ON fb_posts USING gin (to_tsvector('simple', {expr}))"),
                                  ("trgm", f"CREATE INDEX {INDEX} ON fb_posts USING gin ({expr} gin_trgm_ops)")):
                    run = probe(c, args.mem[-1], ddl)
                    r[kind] = {k: run[k] for k in ("index_bytes", "pages", "build_seconds", "meta")}
                result["nonalpha"].append(r)
                print(json.dumps({"from": r["from"], "simple": r["simple"]["index_bytes"],
                                  "trgm": r["trgm"]["index_bytes"]}), flush=True)
        c.execute(f"DROP INDEX IF EXISTS {INDEX}")
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
