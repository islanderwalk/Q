"""Q4 答案字數：題目上限 600 字。答案段是 q4/answer/threads_q4_result.txt「[D. 第 4 題答案…]」那一行之後，到「驗證細節：」之前。

用最嚴的算法：空白與換行也算（2026-10-31 補 robots.txt 一句後是 600 字，不含空白是 521 字）。
每個換行算 1 字：read_text 會把 CRLF 轉成 LF，所以 Windows 以 CRLF 取出時也是 600。
另外檢查標題行寫的兩個數字與實際一致，改答案時要一起改。
"""
import re
from pathlib import Path

RESULT = Path(__file__).resolve().parents[2] / "answer" / "threads_q4_result.txt"
LIMIT = 600


def test_answer_within_600_chars_and_header_matches():
    text = RESULT.read_text(encoding="utf-8")
    m = re.search(r"^\[D\. 第 4 題答案（(\d+) 字，連空白與換行；不含空白 (\d+) 字。.*?\]\n(.*?)\n\s*驗證細節：",
                  text, re.M | re.S)
    assert m, "threads_q4_result.txt 找不到第 4 題答案段"
    body = m.group(3).strip()
    strict, non_ws = len(body), len(re.sub(r"\s", "", body))
    assert (strict, non_ws) == (int(m.group(1)), int(m.group(2)))
    assert strict <= LIMIT
    assert "robots.txt" in body
