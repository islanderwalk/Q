"""Q1 答案字數：題目上限 400 字。答案段是 q1/README.md 從「## 答案」那一行到下一條「---」。

算法有好幾種（2026-10-31 第 3 點改字後實測）：不含空白與 Markdown 的 ** 是 373 字；連 ** 一起算 385 字；
再去掉清單編號 367 字；只算中文字與全形標點 361 字。測試用最嚴的算法（只去空白）檢查 ≤ 400。
"""
import re
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"
LIMIT = 400


def answer_section(markdown):
    m = re.search(r"^## 答案.*?$(.*?)^---\s*$", markdown, re.M | re.S)
    assert m, "q1/README.md 找不到「## 答案」段"
    return m.group(1)


def count_chars(section, keep_markdown=True):
    s = re.sub(r"\s", "", section)
    return len(s) if keep_markdown else len(s.replace("**", ""))


def test_count_rule_on_a_small_example():
    sec = answer_section("# 題目\n\n## 答案\n\n1. **處理**信件，\n放棄社群。\n\n---\n\n## 程式\n")
    # 「1.**處理**信件，」11 字＋「放棄社群。」5 字；去掉兩組 ** 少 4 字
    assert count_chars(sec) == 16 and count_chars(sec, keep_markdown=False) == 12


def test_answer_within_400_chars():
    sec = answer_section(README.read_text(encoding="utf-8"))
    assert 300 < count_chars(sec, keep_markdown=False) <= count_chars(sec) <= LIMIT
