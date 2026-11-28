"""FB 貼文搜尋一頁摘要：維持精簡（≤ 700 字，不含算式），細節放 README／RESULTS。

摘要本文是 q2/design/summary.txt 中「【算式」那一行之前的部分。一頁摘要比單段文字長，是因為要逐條寫瓶頸 1–5 並帶上數字；算法為 python len，空白與換行都算，算式不計入字數。
"""
from pathlib import Path

ANSWER = Path(__file__).resolve().parents[2] / "design" / "summary.txt"
LIMIT = 700


def split(text):
    body, _, formulas = text.partition("【算式")
    return body.strip(), formulas


def test_answer_within_limit():
    body, formulas = split(ANSWER.read_text(encoding="utf-8"))
    assert LIMIT * 0.6 < len(body) <= LIMIT
    assert formulas, "算式區不見了"
