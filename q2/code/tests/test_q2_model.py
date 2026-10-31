"""Q2 離線煙霧測試：不連資料庫、不連網。

涵蓋：頁面數字的內部一致性、8 KB page 換算、兩字詞切法、SQL 檔切段與佔位符。
"""
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

import q2_model as m  # noqa: E402
import run_q2 as r  # noqa: E402


def test_page_constants_are_self_consistent():
    p = m.PAGE
    assert p["table_bytes"] == p["table_pages"] * m.BLOCK_BYTES
    assert p["table_pages"] == m.table_pages(p["rows"], p["lp_len"])
    assert p["rows_per_page"] == m.rows_per_page(p["lp_len"]) == 8
    assert p["tokens_per_post"] * p["rows"] == 249_000_000


def test_eight_rows_fit_but_nine_do_not_at_918_bytes():
    # 918 B 列：8 列剛好放得下，9 列不行 → 頁面的 125,000 頁才成立
    assert m.rows_per_page(918) == 8
    assert m.rows_per_page(880) == 9  # 敏感度：列寬少 38 B 就會變成 9 列/頁


def test_bigram_split_matches_definition():
    assert m.bigrams("栢潎难颢") == ["栢潎", "潎难", "难颢"]
    assert m.bigrams("a") == []
    assert m.bigrams("") == []


def test_bytes_per_posting_matches_page_claim():
    bpp = m.bytes_per_posting(m.PAGE["bigram_bytes"], m.postings(1_000_000, m.PAGE["tokens_per_post"]))
    assert 1.6 < bpp < 1.7  # RESULTS.md 與頁面 bpp 的依據


def test_linear_extrapolation():
    assert m.extrapolate_linear(60.0, 100_000, 1_000_000) == pytest.approx(600.0)


def test_sql_sections_parse_and_cover_all_steps():
    steps = r.read_sections(r.SQL_DIR / "03_indexes.sql", "step")
    assert list(steps) == ["btree_page_id", "simple_tsv", "trgm", "bigram"]
    assert "USING gin (q2_bigrams(message))" in steps["bigram"]

    queries = r.read_sections(r.SQL_DIR / "05_queries.sql", "query")
    assert set(queries) == {"bigram_contains", "trgm_like", "simple_tsvector", "btree_page_id"}
    assert "%(term)s" in queries["bigram_contains"]

    measures = r.read_sections(r.SQL_DIR / "04_measure.sql", "query")
    assert {"distinct_check", "table_size", "heap_page0_lp", "index_sizes"} <= set(measures)


def test_pg_bigm_sql_has_index_query_and_meta():
    bigm = r.read_sections(r.SQL_DIR / "06_pg_bigm.sql", "pg_bigm")
    assert list(bigm) == ["index", "like", "meta"]
    assert "CREATE EXTENSION IF NOT EXISTS pg_bigm" in bigm["index"]
    assert "USING gin (message gin_bigm_ops)" in bigm["index"]
    assert "likequery(%(term)s)" in bigm["like"]
    assert "fb_posts_pg_bigm_gin" in bigm["meta"]


def test_like_rival_names_match_the_index_ddl():
    # 兩個 LIKE 查詢各自量時要拿掉的對手索引，名稱必須和建索引的 SQL 一致，否則拿不掉、量到的是對手
    ddl = (r.SQL_DIR / "03_indexes.sql").read_text(encoding="utf-8") + \
        (r.SQL_DIR / "06_pg_bigm.sql").read_text(encoding="utf-8")
    assert set(r.LIKE_RIVAL) == {"trgm_like", "pg_bigm_like"}
    # 方向也要對：量 pg_trgm 時拿掉 pg_bigm 的索引，反之亦然
    for query, opclass in (("trgm_like", "gin_bigm_ops"), ("pg_bigm_like", "gin_trgm_ops")):
        assert f"CREATE INDEX {r.LIKE_RIVAL[query]} ON fb_posts USING gin (message {opclass})" in ddl


class _FakeConn:
    """只記錄 execute 收到的 prepare 參數，回傳固定結果。"""

    def __init__(self):
        self.prepare_args = []

    def execute(self, text, params=None, prepare=None):
        self.prepare_args.append(prepare)
        plan = [{"Plan": {"Node Type": "Aggregate"}}]
        return type("Cur", (), {"fetchone": lambda _: [plan if text.startswith("EXPLAIN") else 7],
                                "fetchall": lambda _: [(7,)]})()


def test_run_queries_never_lets_psycopg_auto_prepare():
    # 自動 prepare 會讓第 3 個詞起改用 generic plan（generic_plan_check.py），計時與 EXPLAIN 對不上
    c = _FakeConn()
    out = r.run_queries(c, {"trgm_like": "SELECT count(*) WHERE m LIKE %(term)s"}, {"term": "政府"}, repeats=3)
    assert out["trgm_like"]["count"] == 7
    assert c.prepare_args == [False] * 6  # 結果數、暖機、3 次計時、EXPLAIN


@pytest.mark.parametrize("mode", ["repeat", "distinct"])
def test_generate_sql_has_rows_placeholder_replaced(mode):
    sql = r.generate_sql(mode, 1234)
    assert "__ROWS__" not in sql
    assert "generate_series(1::bigint, 1234::bigint)" in sql


def test_find_index_names_walks_nested_plan():
    plan = [{"Plan": {"Node Type": "Aggregate", "Plans": [
        {"Node Type": "Bitmap Heap Scan", "Plans": [
            {"Node Type": "Bitmap Index Scan", "Index Name": "fb_posts_bigram_gin"}]}]}}]
    assert r.find_index_names(plan) == ["Bitmap Index Scan", "fb_posts_bigram_gin"]
