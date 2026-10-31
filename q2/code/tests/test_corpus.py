"""Q2 真實語料模式的離線測試：不連資料庫、不連網。

涵蓋：250 字切段、CJK 比例過濾、PTT 問答解析、兩種組合方式、corpus 模式 SQL 佔位符、語料檔讀取。
"""
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

import prepare_corpus as pc  # noqa: E402
import run_q2 as r  # noqa: E402


def test_chunks_are_fixed_length_and_drop_tail():
    text = "甲" * 600
    out = pc.chunks(text, size=250)
    assert out == ["甲" * 250, "甲" * 250]  # 尾段 100 字丟掉


def test_chunks_skip_low_cjk_pieces_and_respect_limit():
    text = "1234567890" * 25 + "中" * 250 + "文" * 250
    assert pc.chunks(text, size=250) == ["中" * 250, "文" * 250]
    assert pc.chunks(text, size=250, limit=1) == ["中" * 250]


def test_normalize_collapses_newlines_and_tabs():
    assert pc.normalize(" 高雄\n\n市\t政府 ") == "高雄 市 政府"


def test_ptt_lines_drops_no_data_answer(tmp_path):
    f = tmp_path / "qa.txt"
    f.write_text("為什麼\t因為\n有人嗎\t沒有資料\n\t\n", encoding="utf-8")
    assert pc.ptt_lines(f) == ["為什麼 因為", "有人嗎"]


def test_concat_uses_each_line_once():
    lines = ["一二三四五", "六七八九十"]
    # 接起來是「一二三四五 六七八九十」（11 字），切 4 字兩段，尾段 3 字丟掉
    assert pc.concat_posts(lines, size=4) == ["一二三四", "五 六七"]


def test_recombine_is_deterministic_and_exact_length():
    lines = ["今天天氣很好", "八卦板", "有沒有高雄的八卦"]
    a = pc.recombine_posts(lines, rows=5, size=20, seed=1)
    b = pc.recombine_posts(lines, rows=5, size=20, seed=1)
    assert a == b
    assert all(len(p) == 20 for p in a)


def test_write_posts_reports_sha_and_distinct(tmp_path):
    info = pc.write_posts(["甲乙", "甲乙", "丙丁"], tmp_path / "o.txt")
    assert info["rows"] == 3 and info["distinct_rows"] == 2
    assert (tmp_path / "o.txt").read_text(encoding="utf-8") == "甲乙\n甲乙\n丙丁\n"
    assert len(info["sha256"]) == 64


def test_generate_sql_corpus_replaces_both_placeholders():
    sql = r.generate_sql("corpus", 1000, corpus_rows=438)
    assert "__ROWS__" not in sql and "__CORPUS_ROWS__" not in sql
    assert "generate_series(1::bigint, 1000::bigint)" in sql
    assert "% 438" in sql


def test_generate_sql_corpus_requires_corpus_rows():
    with pytest.raises(ValueError):
        r.generate_sql("corpus", 1000)


def test_read_corpus_splits_on_newline_only(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("第一則 仍是第一則\n第二則\t含tab\n\n", encoding="utf-8")
    assert r.read_corpus(f) == ["第一則 仍是第一則", "第二則\t含tab"]


def test_default_ctype_matches_platform():
    assert r.DEFAULT_CTYPE == ("zh-TW.UTF-8" if r.os.name == "nt" else "C.UTF-8")


def test_probe_page_stats_counts_only_entry_leaves():
    import probe_simple_gin as p
    rows = [
        (["leaf"], 24 + 4 * 2, 8192 - 2 * 3000),   # entry 葉頁：2 個 entry
        (["leaf"], 24 + 4 * 3, 8192 - 3 * 2000),   # entry 葉頁：3 個 entry
        ([], 24 + 4 * 2, 8000),                     # entry 內部頁
        (["data", "leaf", "compressed"], 24, 100),  # posting tree 葉頁
    ]
    s = p.page_stats(rows)
    assert s["entry_leaf_pages"] == 2 and s["other_pages"] == 2
    assert s["entries_on_leaves"] == 5
    assert s["avg_entries_per_leaf"] == 2.5
    assert s["entries_per_leaf_histogram"] == {"2": 1, "3": 1}
