#!/usr/bin/env python3
"""PTT 文章 → Q2 粉絲頁貼文表：重灌、量測，並和 q2/answer/fb_q2_posts_table.html 的估算公式對照。

【使用者驗證步驟】
前置：PTT 爬蟲可以一直在跑（它只寫 q2_ptt.ptt_articles）。本腳本會整張重灌 fb_posts，
但只讀 ptt_articles，不改它。執行環境是 WSL（Postgres 16，unix socket，root 用 peer 認證，不用密碼）。

1. 重灌 + 量測（最常用）。在 Windows PowerShell 執行：
     wsl -u root -- /root/q2ptt-venv/bin/python <repo>/q2/code/ptt_q2_verify.py
   它會先執行 q2/code/sql/ptt_q2_load.sql（交易內：TRUNCATE fb_posts → 全量重灌 → 建 btree 與 bigram GIN → ANALYZE），
   再量測，印出對照表，並寫入 q2/data/q2_ptt_real_verify.json。
   爬蟲跑到一半也可以隨時重跑，每次都是目前 ptt_articles 的全部內容。
2. 只量測、不重灌（fb_posts 已經灌好時）：在上面的指令最後加上 --skip-load。
3. 怎麼看對照表：
   - 「估計」欄是把實測的筆數 N、平均字數 W 代入頁面的公式算出的值；「實測」欄是 Postgres 量到的值。
   - 「誤差 %」= (估計 − 實測) ÷ 實測 × 100；正值是估高，負值是估低。
   - 「每個 posting」那兩列，對照頁面的 1.63 B。前者的分母和公式一樣是 N×(W−1)，後者是每列不重複的兩字詞數。
   - 下方的「字數分布」與「TOAST」區塊說明長文對 (a) 的影響。
   - 筆數少於 300 時，數字只能用來確認流程能跑，不能拿來和公式比。
   - 「參考值」是 RESULTS.md 裡 100 萬則 PTT 模擬的數字，不是這次量的。
4. 自己在 psql 裡查（WSL，連 q2_ptt）：
     psql -d q2_ptt -c "SELECT count(*) AS 可用文章數 FROM ptt_articles WHERE status = 200 AND body <> ''"
     psql -d q2_ptt -c "SELECT count(*) AS 列數, avg(char_length(message)) AS 平均字數, avg(octet_length(message)) AS 平均位元組 FROM fb_posts"
     psql -d q2_ptt -c "SELECT pg_size_pretty(pg_relation_size('fb_posts')) AS 表, pg_size_pretty(pg_relation_size('fb_posts_page_id_btree')) AS btree, pg_size_pretty(pg_relation_size('fb_posts_bigram_gin')) AS gin"
     psql -d q2_ptt -c "SELECT pg_size_pretty(pg_relation_size(reltoastrelid)) AS toast FROM pg_class WHERE oid = 'fb_posts'::regclass"
     psql -d q2_ptt -c "SELECT avg(pg_column_size(t.*)) AS 每列位元組 FROM fb_posts AS t"
     psql -d q2_ptt -c "SELECT page_name, count(*) FROM fb_posts GROUP BY 1 ORDER BY 2 DESC"
     psql -d q2_ptt -c "SELECT status, count(*) FROM ptt_articles GROUP BY status"
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
LOAD_SQL = HERE / "sql" / "ptt_q2_load.sql"
OUT_JSON = HERE.parent / "data" / "q2_ptt_real_verify.json"
TZ8 = timezone(timedelta(hours=8))

# 頁面公式的常數（q2/answer/fb_q2_posts_table.html 的 JS 與 fa-* 區塊）
OTHER = 164              # 內文以外的列內容（含列標頭與對齊）
PAGE_USABLE = 8168       # 8192 − 24 B 頁標頭
BLOCK = 8192
TOAST_TUPLE_THRESHOLD = 2032   # PostgreSQL 預設：整列超過此大小才會觸發 TOAST
BTREE_PER_ROW = 6
BTREE_PER_PAGE = 40
BTREE_FILL = 0.9
BPP = 1.63               # bigram GIN 每個 posting 的 bytes（頁面的數字）
MIN_ROWS = 300           # 低於這個筆數，數字只用來確認流程
PAGE_TARGET_N = 1_000_000
PAGE_TARGET_W = 250

# 參考值：不是這次量的。來源 q2/code/RESULTS.md 第三節（PTT 語料、100 萬則、模擬重組）。
REFERENCE = {
    "source": "q2/code/RESULTS.md 第三節（未重新量測）",
    "ptt_1m_bigram_gin_bytes_per_row": 947,
    "ptt_1m_bigram_bytes_per_posting": 3.80,
    "ptt_1m_avg_content_bytes": 680,
    "repeat_1m_bigram_bytes_per_posting": 1.63,
    "repeat_1m_avg_content_bytes": 754,
}

Q_SRC = r"""
SELECT count(*) FILTER (WHERE status = 200 AND body <> '')::bigint,
       count(*) FILTER (WHERE status = 200 AND body <> '' AND published_at IS NULL)::bigint,
       count(*) FILTER (WHERE status = 404)::bigint,
       count(*)::bigint
FROM ptt_articles
"""

Q_BASE = r"""
SELECT count(*)::bigint,
       coalesce(avg(char_length(message)), 0)::float8,
       coalesce(avg(octet_length(message)), 0)::float8,
       coalesce(avg(pg_column_size(message)), 0)::float8,
       coalesce(avg(pg_column_size(t.*)), 0)::float8,
       count(*) FILTER (WHERE pg_column_size(message) < octet_length(message))::bigint,
       coalesce(sum(char_length(message)), 0)::bigint,
       coalesce(sum(greatest(char_length(message) - 1, 0)), 0)::bigint,
       count(DISTINCT page_id)::bigint,
       coalesce(sum(length(regexp_replace(message, '[^一-鿿]', '', 'g'))), 0)::bigint,
       coalesce(sum(length(regexp_replace(message, '[\u0001-\u007f]', '', 'g'))), 0)::bigint,
       coalesce(avg(char_length(post_id)), 0)::float8,
       count(*) FILTER (WHERE pg_column_size(t.*) > 2032)::bigint,
       coalesce(avg(pg_column_size(t.*)) FILTER (WHERE pg_column_size(t.*) <= 2032), 0)::float8
FROM fb_posts AS t
"""

Q_PCT = r"""
SELECT percentile_cont(ARRAY[0.5, 0.9, 0.99]) WITHIN GROUP (ORDER BY char_length(message)),
       percentile_cont(ARRAY[0.5, 0.9, 0.99]) WITHIN GROUP (ORDER BY octet_length(message)),
       min(char_length(message)), max(char_length(message)),
       count(*) FILTER (WHERE octet_length(message) > %(thr)s)::bigint
FROM fb_posts
"""

Q_PAGES = r"""
WITH pp AS (
    SELECT split_part(trim(both '()' FROM ctid::text), ',', 1)::int AS blk, count(*) AS c
    FROM fb_posts GROUP BY 1
), last AS (
    SELECT max(blk) AS b FROM pp
)
SELECT count(*)::bigint,
       avg(c) FILTER (WHERE blk < (SELECT b FROM last))::float8,
       min(c) FILTER (WHERE blk < (SELECT b FROM last))::bigint,
       max(c) FILTER (WHERE blk < (SELECT b FROM last))::bigint,
       avg(c)::float8,
       mode() WITHIN GROUP (ORDER BY c) FILTER (WHERE blk < (SELECT b FROM last))::bigint
FROM pp
"""

Q_SIZES = r"""
SELECT pg_relation_size('fb_posts')::bigint,
       CASE WHEN c.reltoastrelid = 0 THEN 0 ELSE pg_relation_size(c.reltoastrelid::regclass) END::bigint,
       CASE WHEN c.reltoastrelid = 0 THEN 0 ELSE pg_total_relation_size(c.reltoastrelid::regclass) END::bigint,
       pg_relation_size('fb_posts_page_id_btree')::bigint,
       pg_relation_size('fb_posts_bigram_gin')::bigint,
       pg_total_relation_size('fb_posts')::bigint
FROM pg_class AS c
WHERE c.oid = 'fb_posts'::regclass
"""

Q_DISTINCT = r"""
SELECT coalesce(sum(cardinality(ARRAY(SELECT DISTINCT u FROM unnest(q2_bigrams(message)) AS u))), 0)::bigint
FROM fb_posts
"""


def row_bytes(raw_content: float) -> tuple[float, float]:
    """頁面公式：回傳 (內文含長度標頭的 bytes, 整列 bytes)。raw_content 是內文原始 bytes（公式裡是 3·W）。"""
    header = 1 if raw_content <= 126 else 4
    text = raw_content + header
    return text, OTHER + text


def slot_and_perpage(row: float) -> tuple[int, int]:
    slot = math.ceil(row / 8) * 8 + 4
    return slot, max(1, PAGE_USABLE // slot)


def table_bytes(n: int, row: float) -> tuple[int, int]:
    _, per = slot_and_perpage(row)
    return math.ceil(n / per) * BLOCK, per


def err_pct(est, meas):
    if meas in (None, 0) or est is None:
        return None
    return round((est - meas) / meas * 100, 1)


def connect(a):
    kw = {"dbname": a.db, "user": a.user, "autocommit": True}
    if a.host:
        kw.update(host=a.host, port=a.port)
    return psycopg.connect(**kw)


def run_load(conn) -> float:
    sql = LOAD_SQL.read_text(encoding="utf-8")
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute(sql)
    return round(time.time() - t0, 1)


def fetch_one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def measure(conn) -> dict:
    (src_ok, src_null_pub, src_404, src_all) = fetch_one(conn, Q_SRC)
    n, w, bytes_avg, msg_stored_avg, row_avg, compressed, sum_chars, sum_occ, pages, cjk, nonascii, post_len, over_heap, row_small_avg = fetch_one(conn, Q_BASE)
    result = {
        "source_counts": {
            "ptt_articles_total": src_all,
            "status_404": src_404,
            "usable_status200_nonempty_body": src_ok,
            "excluded_null_published_at": src_null_pub,
            "loaded_into_fb_posts": n,
        },
        "sample": {"n": n},
    }
    if n == 0:
        return result

    W = w
    nonascii_share = nonascii / sum_chars if sum_chars else 0.0
    cjk_share = cjk / sum_chars if sum_chars else 0.0

    p_chars, p_bytes, c_min, c_max, over_thr = fetch_one(
        conn, Q_PCT, {"thr": TOAST_TUPLE_THRESHOLD - OTHER - 4})
    p50c, p90c, p99c = p_chars
    p50b, p90b, p99b = p_bytes

    # 每頁列數：只看「非最後一頁」（最後一頁通常沒裝滿）；只有一頁時退回全部頁的平均
    (blocks, avg_full, min_full, max_full, avg_all, mode_full) = fetch_one(conn, Q_PAGES)
    pages_avg = avg_full if avg_full is not None else avg_all

    (heap, toast, toast_total, btree, gin, total) = fetch_one(conn, Q_SIZES)
    (distinct_postings,) = fetch_one(conn, Q_DISTINCT)

    # 公式估計
    text_f, row_f = row_bytes(3 * W)
    _, per_f = slot_and_perpage(row_f)
    heap_f, _ = table_bytes(n, row_f)
    text_b, row_b = row_bytes(bytes_avg)
    heap_b, per_b = table_bytes(n, row_b)
    btree_f = (BTREE_PER_ROW * n + BTREE_PER_PAGE * pages) / BTREE_FILL
    occ = n * max(W - 1, 0)
    gin_f = occ * BPP

    comparison = [
        {"item": "每列大小（頁面公式，3·W）", "unit": "B", "est": round(row_f, 1), "meas": round(row_avg, 1)},
        {"item": "每列大小（公式，內文改用實測位元組）", "unit": "B", "est": round(row_b, 1), "meas": round(row_avg, 1)},
        {"item": "每頁列數", "unit": "列/頁", "est": per_f, "meas": round(pages_avg, 2) if pages_avg else None},
        {"item": "表 heap（不含 TOAST）", "unit": "B", "est": heap_f, "meas": heap},
        {"item": "表 heap + TOAST（公式沒算 TOAST）", "unit": "B", "est": heap_f, "meas": heap + toast},
        {"item": "粉絲頁 ID btree", "unit": "B", "est": round(btree_f), "meas": btree},
        {"item": "中文全文 bigram GIN（分母 N·(W−1)）", "unit": "B", "est": round(gin_f), "meas": gin},
        {"item": "每個 posting（分母 N·(W−1)）", "unit": "B/posting", "est": BPP,
         "meas": round(gin / occ, 3) if occ else None},
        {"item": "每個 posting（分母 = 每列不重複兩字詞數）", "unit": "B/posting", "est": BPP,
         "meas": round(gin / distinct_postings, 3) if distinct_postings else None},
    ]
    for r in comparison:
        r["err_pct"] = err_pct(r["est"], r["meas"])

    result.update({
        "sample": {
            "n": n,
            "avg_chars_W": round(W, 2),
            "avg_bytes": round(bytes_avg, 2),
            "avg_bytes_per_char": round(bytes_avg / W, 3) if W else None,
            "cjk_char_share": round(cjk_share, 4),
            "nonascii_char_share": round(nonascii_share, 4),
            "avg_post_id_chars": round(post_len, 2),
            "avg_pg_column_size_message": round(msg_stored_avg, 2),
            "avg_row_bytes_pg_column_size": round(row_avg, 2),
            "avg_row_bytes_rows_under_2032": round(row_small_avg, 2),
            "measured_other_bytes_row_minus_message": round(row_avg - msg_stored_avg, 2),
            "W_minus_250": round(W - PAGE_TARGET_W, 2),
            "page_count_boards_as_pages": pages,
        },
        "distribution": {
            "chars_p50": p50c, "chars_p90": p90c, "chars_p99": p99c,
            "bytes_p50": p50b, "bytes_p90": p90b, "bytes_p99": p99b,
            "chars_min": c_min, "chars_max": c_max,
            "rows_over_toast_threshold_est": over_thr,
            "share_over_toast_threshold_est": round(over_thr / n, 4),
            "toast_threshold_message_bytes": TOAST_TUPLE_THRESHOLD - OTHER - 4,
            "rows_compressed_message": compressed,
            "share_compressed_message": round(compressed / n, 4),
            "rows_heap_tuple_over_2032": over_heap,
        },
        "pages": {
            "blocks": blocks,
            "rows_per_page_avg_full_pages": round(avg_full, 2) if avg_full is not None else None,
            "rows_per_page_min_full": min_full,
            "rows_per_page_max_full": max_full,
            "rows_per_page_mode_full": mode_full,
            "rows_per_page_avg_all": round(avg_all, 2),
            "rows_per_page_formula_est": per_f,
            "rows_per_page_formula_est_with_measured_bytes": per_b,
        },
        "sizes": {
            "heap_bytes": heap,
            "toast_bytes": toast,
            "toast_total_bytes_with_index": toast_total,
            "btree_page_id_bytes": btree,
            "bigram_gin_bytes": gin,
            "total_relation_bytes": total,
            "distinct_postings": distinct_postings,
            "posting_occurrences_N_times_W_minus_1": occ,
        },
        "comparison": comparison,
        "formula": {
            "row": "OTHER 164 + text；text = 3·W + (1 或 4 B 長度標頭)",
            "slot": "ceil(row/8)·8 + 4",
            "perPage": "floor(8168 / slot)",
            "table": "ceil(N / perPage) · 8192",
            "btree": "(6·N + 40·粉絲頁數) / 0.9",
            "gin": "N · (W − 1) · 1.63",
            "target_page_example": {
                "N": PAGE_TARGET_N, "W": PAGE_TARGET_W,
                "row": row_bytes(3 * PAGE_TARGET_W)[1],
                "table_bytes": table_bytes(PAGE_TARGET_N, row_bytes(3 * PAGE_TARGET_W)[1])[0],
            },
        },
    })
    return result


def print_report(result: dict) -> None:
    sc = result["source_counts"]
    print("=== PTT 文章 → fb_posts 對照（q2_ptt）===")
    print(f"ptt_articles 共 {sc['ptt_articles_total']} 列；404 {sc['status_404']} 列；"
          f"可用（200 且內文非空）{sc['usable_status200_nonempty_body']} 列；"
          f"因 published_at 為空被排除 {sc['excluded_null_published_at']} 列；"
          f"灌進 fb_posts {sc['loaded_into_fb_posts']} 列。")
    if sc["loaded_into_fb_posts"] == 0:
        print("fb_posts 是空的，目前沒有可以量的資料。等爬蟲寫入後再跑一次。")
        return
    s = result["sample"]
    if s["n"] < MIN_ROWS:
        print(f"注意：只有 {s['n']} 則（少於 {MIN_ROWS}），下表只用來確認流程能跑，不能拿來和公式比。")
    print()
    print(f"實測：N={s['n']}，平均字數 W={s['avg_chars_W']}（與 250 相差 {s['W_minus_250']}），"
          f"平均位元組 {s['avg_bytes']}（每字 {s['avg_bytes_per_char']} B），"
          f"中文字占比 {s['cjk_char_share']*100:.1f}%，非 ASCII 字占比 {s['nonascii_char_share']*100:.1f}%。")
    print(f"粉絲頁（看板）數 {s['page_count_boards_as_pages']}。"
          f"實測 OTHER（整列 − 內文）{s['measured_other_bytes_row_minus_message']} B，公式 164 B。")
    print()
    print(f"{'項目':<36}{'估計':>16}{'實測':>16}{'誤差 %':>10}  單位")
    print("-" * 90)
    for r in result["comparison"]:
        est = r["est"]
        meas = r["meas"]
        est_s = f"{est:,.2f}" if isinstance(est, float) else f"{est:,}"
        meas_s = "—" if meas is None else (f"{meas:,.2f}" if isinstance(meas, float) else f"{meas:,}")
        err_s = "—" if r["err_pct"] is None else f"{r['err_pct']:+.1f}"
        print(f"{r['item']:<36}{est_s:>16}{meas_s:>16}{err_s:>10}  {r['unit']}")
    print()
    d = result["distribution"]
    print("字數分布（字）：p50 {0}、p90 {1}、p99 {2}，最短 {3}、最長 {4}".format(
        round(d["chars_p50"]), round(d["chars_p90"]), round(d["chars_p99"]), d["chars_min"], d["chars_max"]))
    print("內文位元組分布：p50 {0}、p90 {1}、p99 {2}".format(
        round(d["bytes_p50"]), round(d["bytes_p90"]), round(d["bytes_p99"])))
    print(f"超過 TOAST 門檻（內文 > {d['toast_threshold_message_bytes']} B，整列約 2 KB）的列："
          f"{d['rows_over_toast_threshold_est']} 列（{d['share_over_toast_threshold_est']*100:.2f}%）；"
          f"內文被壓縮儲存的列：{d['rows_compressed_message']} 列（{d['share_compressed_message']*100:.1f}%）；"
          f"heap 內整列仍 > 2032 B 的列：{d['rows_heap_tuple_over_2032']} 列。")
    p = result["pages"]
    print(f"每頁列數：非最後頁平均 {p['rows_per_page_avg_full_pages']}（最少 {p['rows_per_page_min_full']}、"
          f"最多 {p['rows_per_page_max_full']}），全部頁平均 {round(p['rows_per_page_avg_all'], 2)}，共 {p['blocks']} 頁。")
    print()
    print("參考（RESULTS.md，100 萬則 PTT 模擬，未重新量測）：每則 GIN 947 B，每 posting 3.80 B；"
          "頁面 1.63 B 來自 repeat（重複段落）的合成資料。")
    print()
    print("影響說明：")
    for line in result["caveats"]:
        print("- " + line)


def build_caveats(result: dict) -> list:
    out = []
    s = result.get("sample", {})
    d = result.get("distribution", {})
    n = s.get("n", 0)
    if n and n < MIN_ROWS:
        out.append(f"樣本只有 {n} 則，每頁列數、索引體積的誤差都還不穩定，不能下結論。")
    out.append("公式假設每列未壓縮、全部放進頁裡。超過約 2 KB 的列，PostgreSQL 會先壓縮內文，"
               "壓完還大就搬到 TOAST 表，列內只留約 18 B 的指標。所以 (a) 的 heap 估計會高估這些長文的占用，"
               "但 TOAST 表另外占空間，公式沒有算。(a) 的總量要看「表 heap + TOAST」那一列。")
    if n:
        out.append(f"PTT 內文的字數很散（p50 {round(d['chars_p50'])} 字、p99 {round(d['chars_p99'])} 字），"
                   f"超過 TOAST 門檻的比例 {d['share_over_toast_threshold_est']*100:.2f}%。"
                   "頁面的 (a) 把所有列當成同一個長度（每列 W 字），長文比例不為零時，平均字數會被拉高，"
                   "而且 250 字的假設不一定貼近線上貼文。")
    out.append("(c) 的 1.63 B/posting 是重複段落的合成資料量出來的。PTT 文字在 RESULTS.md 量到的是約 3.80 B/posting，"
               "所以用 PTT 文字時，全文索引會比頁面的估計高很多。這次的實測數字要和這個參考值一起看。")
    out.append("每個 posting 的 B 數會隨資料量下降（posting list 越長，壓縮越好），小樣本量到的值通常偏高，不能線性外推到 100 萬則。")
    out.append("(b) 的 40 B·粉絲頁數 那一項，這次把每個 PTT 看板當成一個粉絲頁，粉絲頁數很少，所以這一項幾乎可以忽略。"
               "btree 只有 6 B·N 那一項有意義；實測時 btree 會做重複鍵壓縮，所以實際可能比估計小。")
    out.append(f"post_id 實測平均 {result.get('sample', {}).get('avg_post_id_chars')} 字"
               "（頁面 schema 註解寫 31 字），會讓 OTHER 略有不同；實測的 OTHER 已列在上面。")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="PTT 文章 → Q2 fb_posts 重灌與量測")
    ap.add_argument("--host", default="/var/run/postgresql",
                    help="Postgres 主機或 unix socket 目錄（預設 socket，WSL 內以 root 執行即可）")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--user", default="root")
    ap.add_argument("--db", default="q2_ptt")
    ap.add_argument("--skip-load", action="store_true", help="不重灌，只量測")
    ap.add_argument("--out", default=str(OUT_JSON))
    a = ap.parse_args()

    now = datetime.now(TZ8).isoformat(timespec="seconds")
    with connect(a) as conn:
        load = {"ran": False}
        if not a.skip_load:
            secs = run_load(conn)
            load = {"ran": True, "seconds": secs, "sql": str(LOAD_SQL.relative_to(HERE.parent.parent))}
        result = measure(conn)

    result = {
        "generated_at": now,
        "db": a.db,
        "source": "q2_ptt.ptt_articles（status=200、body 非空、published_at 非空）→ fb_posts",
        "load": load,
        "reference": REFERENCE,
        **result,
    }
    if result["source_counts"]["loaded_into_fb_posts"] == 0:
        result["status"] = "no_rows"
        result["caveats"] = ["fb_posts 是空的，目前沒有可以量的資料。"]
    else:
        result["status"] = "ok" if result["sample"]["n"] >= MIN_ROWS else "ok_small_sample"
        result["caveats"] = build_caveats(result)

    print_report(result)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"已寫入 {out}")


if __name__ == "__main__":
    main()
