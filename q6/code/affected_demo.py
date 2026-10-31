#!/usr/bin/env python3
"""示範：字典新增一個詞時，需要重算的文件數（只重算含該字元序列的文件）。

用 run_compare.py 產生的標題語料（q6/data/ptt_titles_7d.jsonl）。
候選詞是假設的「新增詞」，用來量測影響範圍，不代表字典已收錄。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import segmenter as sg  # noqa: E402

CORPUS = HERE.parent / "data" / "ptt_titles_7d.jsonl"
CANDIDATES = ["麥當勞", "柯文哲", "中國人", "八卦", "高雄", "漢堡", "投票"]


def main():
    docs = {}
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        docs[rec["doc_id"]] = rec["text"]
    index = sg.BigramIndex(docs)
    print(f"語料 {len(docs):,} 篇")
    for term in CANDIDATES:
        hit = sg.affected_doc_ids(index, {term})
        print(f"{term}\t含該字元序列 {len(hit):>5,} 篇\t{100 * len(hit) / len(docs):5.1f}%")


if __name__ == "__main__":
    main()
