"""Q2 答案字數：題目上限 600 字（不含算式）。答案本文是 q2/answer/answer_600.txt 中「【算式」那一行之前的部分。

算法與 Q4 相同、也是最嚴的一種：python len，空白與換行都算。算式依題目不計入字數。
"""
from pathlib import Path

ANSWER = Path(__file__).resolve().parents[2] / "answer" / "answer_600.txt"
LIMIT = 600


def split(text):
    body, _, formulas = text.partition("【算式")
    return body.strip(), formulas


def test_answer_within_limit():
    body, formulas = split(ANSWER.read_text(encoding="utf-8"))
    assert LIMIT * 0.6 < len(body) <= LIMIT
    assert formulas, "算式區不見了"
