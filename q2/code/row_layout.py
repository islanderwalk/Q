"""QSearch 第 2 題：讀出一則貼文在 PostgreSQL 頁裡的逐 byte 配置。

從 fb_posts 第 0 頁第 1 格的原始 bytes（pageinspect 的 heap_page_items）照 PostgreSQL 的對齊規則
逐欄走一遍，輸出每一欄的起點、長度、前面補了幾個 byte、補的 byte 是什麼，並和 pg_column_size 對照。
只讀，不改資料。資料庫要先用 run_q2.py 建好（需要 pageinspect）。

用法：python q2/code/row_layout.py --db q2_bench --out q2/data/q2_row_layout_page0.json
"""
import argparse
import json
import struct

import psycopg

ALIGN = {"c": 1, "s": 2, "i": 4, "d": 8}


def align(off: int, n: int) -> int:
    return (off + n - 1) // n * n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=55432)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--db", default="q2_bench")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with psycopg.connect(host=a.host, port=a.port, user=a.user, dbname=a.db, autocommit=True) as conn:
        c = conn.cursor()
        c.execute("CREATE EXTENSION IF NOT EXISTS pageinspect")
        version = c.execute("SHOW server_version").fetchone()[0]
        lower, upper = c.execute("SELECT lower, upper FROM page_header(get_raw_page('fb_posts', 0))").fetchone()
        lps = c.execute("SELECT lp, lp_off, lp_len, t_hoff, t_infomask2 & 2047 FROM heap_page_items(get_raw_page('fb_posts', 0)) ORDER BY lp").fetchall()
        lp, lp_off, lp_len, t_hoff, natts, t_data, xmin, xmax, field3, ctid, im2, im = c.execute(
            "SELECT lp, lp_off, lp_len, t_hoff, t_infomask2 & 2047, t_data, t_xmin, t_xmax, t_field3, t_ctid::text, t_infomask2, t_infomask "
            "FROM heap_page_items(get_raw_page('fb_posts', 0)) WHERE lp = 1").fetchone()
        cols = c.execute(
            "SELECT attname, format_type(atttypid, atttypmod), attlen, attalign FROM pg_attribute "
            "WHERE attrelid = 'fb_posts'::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum").fetchall()
        sizes = c.execute(
            "SELECT " + ", ".join(f"pg_column_size({name})" for name, *_ in cols) + ", pg_column_size(t.*), page_name, post_id "
            "FROM fb_posts AS t WHERE ctid = '(0,1)'").fetchone()

    data = bytes(t_data)
    rows, off = [], 0  # off：從列資料開頭（t_hoff 之後）算起
    for (name, typ, attlen, attalign), colsize in zip(cols, sizes):
        start = off
        if attlen == -1 and data[off] != 0:
            pass  # 1 byte 長度標頭的短字串不需要對齊
        else:
            off = align(off, ALIGN[attalign])
        pad = data[start:off]
        if attlen == -1:
            b0 = data[off]
            if b0 & 1:
                size, header = (b0 >> 1) & 0x7F, 1
            else:
                size, header = (struct.unpack_from("<I", data, off)[0] >> 2) & 0x3FFFFFFF, 4
        else:
            size, header = attlen, 0
        rows.append({
            "column": name, "type": typ, "align": ALIGN[attalign],
            "pad_before": off - start, "pad_bytes_hex": pad.hex(" ") if pad else "",
            "start_in_row": t_hoff + off, "bytes": size, "length_header_bytes": header,
            "pg_column_size": colsize,
        })
        off += size
    total = t_hoff + off
    out = {
        "server_version": version,
        "source": "heap_page_items(get_raw_page('fb_posts', 0)) lp = 1；pg_column_size 取同一列 ctid (0,1)",
        "page0": {"lower": lower, "upper": upper, "free_bytes": upper - lower,
                  "line_pointers": [{"lp": r[0], "lp_off": r[1], "lp_len": r[2], "t_hoff": r[3], "natts": r[4]} for r in lps]},
        "row": {"lp": lp, "lp_off": lp_off, "lp_len": lp_len, "header_bytes_t_hoff": t_hoff, "natts": natts,
                "header_fields": {"t_xmin": xmin, "t_xmax": xmax, "t_cid_or_xvac": field3, "t_ctid": ctid,
                                  "t_infomask2": im2, "t_infomask": im,
                                  "note": "xmin 4 + xmax 4 + cid 4 + ctid 6 + infomask2 2 + infomask 2 + hoff 1 = 23 B，補 1 B 到 24 B"},
                "page_name": sizes[-2], "post_id_chars": len(sizes[-1]),
                "computed_total": total, "pg_column_size_row": sizes[-3]},
        "columns": rows,
        "checks": {
            "computed_total_equals_lp_len": total == lp_len,
            "every_column_matches_pg_column_size": all(r["bytes"] == r["pg_column_size"] for r in rows),
            "padding_is_zero_bytes": all(set(bytes.fromhex(r["pad_bytes_hex"].replace(" ", ""))) <= {0} for r in rows),
        },
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out["row"], ensure_ascii=False), json.dumps(out["checks"]))
    for r in rows:
        print(f'{r["column"]:16s} pad {r["pad_before"]}  start {r["start_in_row"]:4d}  bytes {r["bytes"]:4d}')


if __name__ == "__main__":
    main()
