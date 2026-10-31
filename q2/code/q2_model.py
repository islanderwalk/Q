"""QSearch 第 2 題：離線換算與頁面數字的一致性（不連資料庫）。

PAGE 是 q2/answer/fb_q2_posts_table.html 內 const D 的數字（PG 16.15 的實測）。
run_q2.py 的實測結果用 compare 對照，差異照實寫在 RESULTS.md。
"""
import math

BLOCK_BYTES = 8192        # PostgreSQL 預設 page（block）大小
PAGE_HEADER_BYTES = 24    # PageHeaderData 的固定大小
LINE_POINTER_BYTES = 4    # 每列在 page 內的 ItemId 大小

PAGE = {
    "table_bytes": 1_024_000_000,
    "table_pages": 125_000,
    "rows": 1_000_000,
    "lp_len": 918,
    "rows_per_page": 8,
    "bigram_bytes": 406_396_928,
    "bigram_pages": 49_609,
    "bigram_entries": 248_925,
    "trgm_bytes": 409_247_744,
    "simple_bytes": 3_268_608,
    "tokens_per_post": 249,
    "build_seconds": {"bigram": 7 * 60 + 39, "trgm": 77, "simple": 5.5},
}


def rows_per_page(lp_len: int) -> int:
    """每個 8 KB page 能放幾列（列長 lp_len，加上 ItemId）。"""
    return (BLOCK_BYTES - PAGE_HEADER_BYTES) // (lp_len + LINE_POINTER_BYTES)


def table_pages(rows: int, lp_len: int) -> int:
    return math.ceil(rows / rows_per_page(lp_len))


def table_bytes(rows: int, lp_len: int) -> int:
    return table_pages(rows, lp_len) * BLOCK_BYTES


def bigrams(text: str) -> list:
    """兩字詞切法：相鄰兩字一組，與 SQL 的 q2_bigrams() 相同（長度 < 2 回傳空）。"""
    return [text[i:i + 2] for i in range(max(len(text) - 1, 0))]


def postings(rows: int, tokens_per_post: int) -> int:
    """倒排索引的 posting 數 = 列數 × 每列的 bigram 數（未去重）。"""
    return rows * tokens_per_post


def extrapolate_linear(seconds_small: float, rows_small: int, rows_big: int) -> float:
    """線性外推（用於 bigram 建索引超時時）。"""
    return seconds_small * rows_big / rows_small


def bytes_per_posting(index_bytes: int, n_postings: int) -> float:
    return index_bytes / n_postings
