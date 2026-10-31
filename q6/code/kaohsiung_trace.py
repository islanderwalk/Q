#!/usr/bin/env python3
"""QSearch 第 6 題：解釋「高雄市」整詞次數為何新舊都是 6（字典詞頻與逐篇切分證據）。

不改 repo 字典，只讀 run_compare.py 用的同一份 6,685 篇標題，做三件事：
1. 字典詞頻：主字典（舊）與「主字典＋使用者字典」（新）裡 高雄市／高雄／雄風 等詞的 jieba FREQ
   （0 代表只是前綴佔位、不算詞；None 代表字典沒有）
2. 含「高雄市」的每一篇標題：舊流程（純字典）與新流程的切分
3. 試算：在新流程加「高雄」（jieba 建議詞頻、或與主字典「高雄市」同為 31；total 照預設累加或固定），
   含「高雄」標題的整詞／黏詞／拆開分布，以及含與不含「高雄」的標題各有幾篇切分改變

輸出：q6/data/kaohsiung_trace.json（主字典＝jieba 內建）；--base-dict 時為 kaohsiung_trace_<tag>.json
用法：python kaohsiung_trace.py [--base-dict dict.txt.big --tag dict_big]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_compare as rc  # noqa: E402
import segmenter as sg  # noqa: E402

WORDS = ("高雄市", "高雄", "高雄人", "高雄港", "雄風", "風力")
# (加入的詞頻, 是否固定 total)；None = jieba suggest_freq 建議值
SCENARIOS = ((None, False), (None, True), (31, False), (31, True))


def freq_table(seg: sg.Segmenters, words=WORDS) -> dict:
    return {k: {w: tk.FREQ.get(w) for w in words} for k, tk in (("legacy", seg.legacy), ("new", seg.new))}


def tokens_containing(seg: sg.Segmenters, docs: dict, term: str = rc.PLACE) -> dict:
    """含 term 的標題裡，舊／新流程切出的、含 term 的 token 次數。"""
    out = {"legacy": Counter(), "new": Counter()}
    for text in docs.values():
        if term in text:
            out["legacy"].update(t for t in sg.segment_legacy(seg, text) if term in t)
            out["new"].update(t for t in sg.segment_new(seg, text) if term in t)
    return {k: dict(v.most_common()) for k, v in out.items()}


def what_if_add_word(seg: sg.Segmenters, docs: dict, word: str = rc.PLACE, freq=None,
                     pin_total: bool = False) -> dict:
    """在 seg.new 加一個詞後重切全部文件。會改動 seg.new，呼叫端要傳新建的 Segmenters。
    「含該詞」一律看前處理後的文字（新流程實際切的內容），標籤裡的字不算。"""
    before = {d: sg.segment_new(seg, t) for d, t in docs.items()}
    total0 = seg.new.total
    seg.new.add_word(word, freq)
    if pin_total:
        seg.new.total = total0
    status, changed_with, changed_without, still_split = Counter(), 0, 0, []
    for d, text in docs.items():
        after, pre = sg.segment_new(seg, text), sg.preprocess(text)
        if after != before[d]:
            if word in pre:
                changed_with += 1
            else:
                changed_without += 1
        if word in pre:
            s = rc.place_status(after, seg.new.FREQ, term=word)
            status[s] += 1
            if s == "split":
                still_split.append({"text": text, "new_raw": seg.new.lcut(pre, HMM=True)})
    return {
        "word": word, "freq_arg": freq, "freq_used": seg.new.FREQ[word], "pin_total": pin_total,
        "total_before": total0, "total_after": seg.new.total,
        "status": {s: status.get(s, 0) for s in ("whole", "glued", "split")},
        "changed_containing": changed_with, "changed_not_containing": changed_without,
        "still_split": still_split,
    }


def trace(docs: dict, base_dict: Path = None, user_dict: Path = sg.USER_DICT_PATH) -> dict:
    seg = sg.Segmenters.build(user_dict=user_dict, base_dict=base_dict)
    titles = [{"text": t, "legacy": sg.segment_legacy(seg, t),
               "new_raw": seg.new.lcut(sg.preprocess(t), HMM=True), "new": sg.segment_new(seg, t)}
              for t in docs.values() if "高雄市" in t]
    scenarios = [what_if_add_word(sg.Segmenters.build(user_dict=user_dict, base_dict=base_dict), docs,
                                  freq=f, pin_total=p) for f, p in SCENARIOS]
    return {
        "base_dict": seg.base_dict, "base_dict_hash": seg.base_dict_hash,
        "dict_version": seg.dict_version, "dict_hash": seg.dict_hash, "docs": len(docs),
        "occurrences": {"高雄市": sum(t.count("高雄市") for t in docs.values()),
                        "高雄": sum(t.count("高雄") for t in docs.values())},
        "titles_containing": {"高雄市": sum("高雄市" in t for t in docs.values()),
                              "高雄": sum("高雄" in t for t in docs.values())},
        "total": {"legacy": seg.legacy.total, "new": seg.new.total},
        "freq": freq_table(seg),
        "tokens_containing_高雄": tokens_containing(seg, docs),
        "titles_高雄市": titles,
        "what_if_add_高雄": scenarios,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path, default=rc.DEFAULT_INDEX)
    ap.add_argument("--base-dict", type=Path, default=None, help="主字典檔（預設 jieba 內建）")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    tag = args.tag or ("default" if args.base_dict is None else args.base_dict.name.replace(".", "_"))
    out = args.out or rc.DATA / ("kaohsiung_trace.json" if tag == "default" else f"kaohsiung_trace_{tag}.json")
    res = trace(dict(rc.load_titles(args.index)), base_dict=args.base_dict)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    brief = {k: res[k] for k in ("base_dict", "occurrences", "titles_containing", "freq", "tokens_containing_高雄")}
    brief["what_if_add_高雄"] = [{k: s[k] for k in ("freq_used", "pin_total", "total_before", "total_after", "status",
                                                    "changed_containing", "changed_not_containing")}
                                for s in res["what_if_add_高雄"]]
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    print(f"wrote {out}")
    return res


if __name__ == "__main__":
    main()
