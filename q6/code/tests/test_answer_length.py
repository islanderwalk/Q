"""Q6 答案字數：題目上限 600 字，用最嚴的算法（空白與換行也算；read_text 會把 CRLF 轉成 LF）。

另外檢查 10/31 審查的四點有寫進答案：文字雲數篇數、金標詞召回閘門、版本指紋與內容雜湊分開、
新增詞只重跑含該字串的文章且固定 total。
"""
import re
from pathlib import Path

ANSWER = Path(__file__).resolve().parents[2] / "answer" / "answer_600.txt"


def test_answer_within_600_chars_including_whitespace():
    body = ANSWER.read_text(encoding="utf-8").strip()
    assert len(body) <= 600, len(body)
    assert [l for l in body.splitlines() if re.fullmatch(r"\([abc]\)", l)] == ["(a)", "(b)", "(c)"]


def test_answer_covers_review_points():
    body = ANSWER.read_text(encoding="utf-8")
    for s in ("terms 聚合數篇數", "金標詞召回", "seg_ver", "指紋", "content_sha", "字元片語", "固定 total"):
        assert s in body, s
    assert "dict_version" not in body and "external" not in body
