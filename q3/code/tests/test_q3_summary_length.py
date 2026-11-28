"""PTT 全文搜尋：一頁設計摘要的字數檢查（不含架構圖與算式）。

摘要本文是 q3/design/summary.txt 中「【算式」那一行之前的部分。
字數不超過 850 字：一頁摘要維持精簡，細節放 README／RESULTS。
算法是 python len，空白與換行都算；算式不計入字數。
"""
from pathlib import Path

SUMMARY = Path(__file__).resolve().parents[2] / "design" / "summary.txt"
LIMIT = 850


def split(text):
    body, _, formulas = text.partition("【算式")
    return body.strip(), formulas


def test_summary_within_limit():
    body, formulas = split(SUMMARY.read_text(encoding="utf-8"))
    assert LIMIT * 0.6 < len(body) <= LIMIT
    assert formulas, "算式區不見了"
