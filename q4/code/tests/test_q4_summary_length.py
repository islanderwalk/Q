"""Threads 爬蟲設計摘要字數：一頁摘要維持精簡（≤ 650 字），細節放 README／RESULTS。

摘要段是 q4/design/threads_q4_result.txt「[D. 設計摘要…]」那一行之後，到「驗證細節：」之前。

用最嚴的算法：空白與換行也算（2026-10-31 補 robots.txt 一句後不含空白 521 字；
2026-11-15 補 IPRoyal 10 TB 起購條件後，連空白與換行 641 字，不含空白 566 字）。
每個換行算 1 字：read_text 會把 CRLF 轉成 LF，所以 Windows 以 CRLF 取出時也是 641。
另外檢查標題行寫的兩個數字與實際一致，改摘要時要一起改。
"""
import re
from pathlib import Path

RESULT = Path(__file__).resolve().parents[2] / "design" / "threads_q4_result.txt"
LIMIT = 650


def test_summary_within_limit_and_header_matches():
    text = RESULT.read_text(encoding="utf-8")
    m = re.search(r"^\[D\. 設計摘要（(\d+) 字，連空白與換行；不含空白 (\d+) 字。.*?\]\n(.*?)\n\s*驗證細節：",
                  text, re.M | re.S)
    assert m, "threads_q4_result.txt 找不到設計摘要段"
    body = m.group(3).strip()
    strict, non_ws = len(body), len(re.sub(r"\s", "", body))
    assert (strict, non_ws) == (int(m.group(1)), int(m.group(2)))
    assert strict <= LIMIT
    assert "robots.txt" in body
