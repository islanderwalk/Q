#!/usr/bin/env python3
"""QSearch 第 6 題：用 PTT 文本跑新舊斷詞前後對照。

語料：q3/data/ptt_q3_out/index_entries.csv 的 7 天內 6,685 列標題（2026/10/26 實抓，繁體文字；其中 425 列為已刪除文章，沒有 aid）。
注意：q3/data/ptt_q3_census_out/articles.jsonl 只有時間與計數，沒有內文，所以本題改用標題。

主字典：預設 jieba 內建字典（--tag default）；--base-dict 可換成繁體 dict.txt.big 等，新舊兩邊同時換。

輸出（tag=default）：
  q6/data/ptt_titles_7d.jsonl       語料（doc_id, text）
  q6/data/segmented_ab.jsonl        新舊並行輸出（seg_legacy, seg_new, dict_version）
  q6/data/top50.json                前 50 詞（舊／新）與檢查項
  q6/code/RESULTS.md                只改寫 <!-- AUTO:compare:<tag> --> 標記之間的區塊，其餘手寫段落不動
輸出（其他 tag）：q6/data/top50_<tag>.json；segmented_ab 預設 q6/data/segmented_ab_<tag>.jsonl，可用 --segmented-out 改到 repo 外。
  語料 ptt_titles_7d.jsonl 只在 tag=default 時寫出（換主字典不影響語料）。

用法：python run_compare.py [--index ...] [--base-dict dict.txt.big --tag dict_big --segmented-out /tmp/x.jsonl]
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import segmenter as sg  # noqa: E402

REPO = HERE.parents[1]
DATA = HERE.parent / "data"
DEFAULT_INDEX = REPO / "q3" / "data" / "ptt_q3_out" / "index_entries.csv"
RESULTS_PATH = HERE / "RESULTS.md"
LEGACY_FRAGMENTS = ("高", "雄", "市")  # 題目點名的「高雄市」被拆散
PLACE = "高雄"                          # 標題裡多半只寫「高雄」，不帶「市」
PLACE_WHOLE = {"高雄", "高雄市"}
TRACE_N = 3


def load_titles(index_csv: Path):
    """每列一篇。已刪除的文章沒有 aid（425 列），用「列號」當 doc_id，避免全部被合併成一篇。"""
    with open(index_csv, encoding="utf-8-sig", newline="") as fp:
        for n, row in enumerate(csv.DictReader(fp), 1):
            if row.get("title"):
                doc_id = row["aid"] or f"deleted-{row.get('page', '')}-{n}"
                yield doc_id, row["title"]


def place_status(tokens, freq: dict, term: str = PLACE, whole=frozenset(PLACE_WHOLE)) -> str:
    """含 term 的標題被切成什麼樣：whole（整詞，或字典裡本來就有的詞如「高雄人」）、
    glued（HMM 黏出字典沒有的詞，如「高雄為」）、split（沒有任何 token 含 term）。"""
    hits = [t for t in tokens if term in t]
    if not hits:
        return "split"
    if any(t in whole or freq.get(t) for t in hits):
        return "whole"
    return "glued"


def upsert_block(md: str, tag: str, body: str) -> str:
    """把 RESULTS.md 中 tag 對應的自動區塊換成 body；沒有就附加在檔尾。區塊外的手寫內容保持不變。
    只有 BEGIN、沒有對應的 END（手動編輯弄壞）時直接報錯，不再附加第二個 BEGIN。"""
    begin, end = f"<!-- AUTO:compare:{tag} BEGIN -->", f"<!-- AUTO:compare:{tag} END -->"
    block = f"{begin}\n{body.rstrip()}\n{end}"
    i = md.find(begin)
    j = md.find(end, max(i, 0))
    if i != -1 and j != -1:
        return md[:i] + block + md[j + len(end):]
    if i != -1 or end in md:
        raise ValueError(f"AUTO 區塊 {tag} 的 BEGIN／END 標記不成對，請先手動修好 RESULTS.md")
    return md.rstrip() + "\n\n" + block + "\n"


def _cell(s: str) -> str:
    return s.replace("|", "｜")  # 標題裡的直線會破壞 Markdown 表格


def _freq(tk, word):
    v = tk.FREQ.get(word)
    return "無" if v is None else f"{v:,}"


def render_block(tag, seg, docs, checks, top_old, top_new, cnt_old, cnt_new, traces, index_path):
    c = checks
    base = seg.base_dict if not seg.base_dict_hash else f"{seg.base_dict}（sha256 前 16 碼 `{seg.base_dict_hash}`）"
    st_l, st_n = c["titles_高雄_status"]["legacy"], c["titles_高雄_status"]["new"]
    rows = []
    for i in range(50):
        o = top_old[i] if i < len(top_old) else ""
        n = top_new[i] if i < len(top_new) else ""
        co = f"{cnt_old[o]:,}" if o in cnt_old else ""
        cn = f"{cnt_new[n]:,}" if n in cnt_new else ""
        rows.append(f"| {i + 1} | {_cell(o)} | {co} | {_cell(n)} | {cn} |")
    fr = c["dict_freq"]
    md = [
        f"### 對照：主字典 = {seg.base_dict}（tag `{tag}`，自動產生）",
        "",
        f"- 語料：{len(docs):,} 個 PTT 八卦板標題（7 天內，`{index_path}`）",
        f"- 主字典：{base}；新舊兩邊用同一份",
        f"- 使用者字典：`{seg.dict_version}`（sha256 前 16 碼 `{seg.dict_hash}`），只載入新流程",
        "- 舊：純字典（HMM 關閉、不做前處理、不過濾停用詞）。舊團隊字典檔不在 repo，此為模擬。",
        "- 新：前處理（PTT 標籤／短網址／網址／電郵／電話）→ jieba＋使用者字典＋HMM → 停用詞、長度 ≥ 2、純數字過濾",
        "",
        "| 檢查項 | 舊 | 新 |",
        "|---|---|---|",
        f"| 前 50 詞中的停用詞／助詞 | {c['top50_stopword_tokens']['legacy']} | {c['top50_stopword_tokens']['new']} |",
        f"| 前 50 詞中的純數字或符號 | {c['top50_pure_or_digit_tokens']['legacy']} | {c['top50_pure_or_digit_tokens']['new']} |",
        f"| 「高雄市」整詞次數 | {c['token_count_高雄市']['legacy']:,} | {c['token_count_高雄市']['new']:,} |",
        f"| 「高雄」整詞次數 | {c['token_count_高雄']['legacy']:,} | {c['token_count_高雄']['new']:,} |",
        f"| 單字「高」次數 | {c['token_count_高']['legacy']:,} | {c['token_count_高']['new']:,} |",
        f"| 含「高雄」的標題：整詞保留 | {st_l['whole']:,} | {st_n['whole']:,} |",
        f"| 含「高雄」的標題：HMM 黏成非字典詞（如「高雄為」） | {st_l['glued']:,} | {st_n['glued']:,} |",
        f"| 含「高雄」的標題：被拆開（沒有 token 含「高雄」） | {st_l['split']:,} | {st_n['split']:,} |",
        f"| 總詞數 | {c['total_tokens']['legacy']:,} | {c['total_tokens']['new']:,} |",
        f"| 不同詞數 | {c['distinct_tokens']['legacy']:,} | {c['distinct_tokens']['new']:,} |",
        "",
        f"含「高雄」的標題 {c['titles_containing_高雄']:,} 篇，其中含「高雄市」{c['titles_containing_高雄市']:,} 篇。",
        f"字典詞頻（jieba `FREQ`；0 代表只是前綴佔位、不算詞）：舊「高雄市」{fr['legacy']['高雄市']}、「高雄」{fr['legacy']['高雄']}；"
        f"新（載入使用者字典後）「高雄市」{fr['new']['高雄市']}、「高雄」{fr['new']['高雄']}。",
        "",
        "切分樣本（前 3 篇含「高雄市」、前 3 篇只含「高雄」；新流程為過濾後的 tokens）：",
        "",
        "| 標題 | 舊 | 新 |",
        "|---|---|---|",
        *[f"| {_cell(t['text'])} | {_cell(' / '.join(t['legacy']))} | {_cell(' / '.join(t['new']))} |" for t in traces],
        "",
        "前 50 詞（舊 vs 新）：",
        "",
        "| 排名 | 舊：詞 | 舊：次數 | 新：詞 | 新：次數 |",
        "|---|---|---|---|---|",
        *rows,
    ]
    return "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    ap.add_argument("--base-dict", type=Path, default=None,
                    help="主字典檔（預設 jieba 內建）；例如繁體 dict.txt.big")
    ap.add_argument("--tag", default=None, help="輸出檔與 RESULTS 區塊的標籤（預設 default 或主字典檔名）")
    ap.add_argument("--segmented-out", type=Path, default=None, help="segmented_ab 的輸出路徑")
    ap.add_argument("--results", type=Path, default=RESULTS_PATH)
    ap.add_argument("--data-dir", type=Path, default=DATA, help="語料與 top50 的輸出目錄（測試用）")
    args = ap.parse_args(argv)
    tag = args.tag or ("default" if args.base_dict is None else args.base_dict.name.replace(".", "_"))
    suffix = "" if tag == "default" else f"_{tag}"
    data = args.data_dir
    seg_out = args.segmented_out or data / f"segmented_ab{suffix}.jsonl"

    data.mkdir(parents=True, exist_ok=True)
    docs = dict(load_titles(args.index))
    if tag == "default":
        with open(data / "ptt_titles_7d.jsonl", "w", encoding="utf-8") as fp:
            for aid, text in docs.items():
                fp.write(json.dumps({"doc_id": aid, "text": text}, ensure_ascii=False) + "\n")

    seg = sg.Segmenters.build(base_dict=args.base_dict)
    cnt_old, cnt_new = Counter(), Counter()
    status = {"legacy": Counter(), "new": Counter()}
    traces_whole, traces_part = [], []
    seg_out.parent.mkdir(parents=True, exist_ok=True)
    with open(seg_out, "w", encoding="utf-8") as fp:
        for aid, text in docs.items():
            out = sg.dual_output(seg, aid, text)
            cnt_old.update(out["seg_legacy"])
            cnt_new.update(out["seg_new"])
            fp.write(json.dumps(out, ensure_ascii=False) + "\n")
            if PLACE in text:
                status["legacy"][place_status(out["seg_legacy"], seg.legacy.FREQ)] += 1
                status["new"][place_status(out["seg_new"], seg.new.FREQ)] += 1
                bucket = traces_whole if "高雄市" in text else traces_part
                if len(bucket) < TRACE_N:
                    bucket.append({"text": text, "legacy": out["seg_legacy"], "new": out["seg_new"]})

    top_old = [w for w, _ in cnt_old.most_common(50)]
    top_new = [w for w, _ in cnt_new.most_common(50)]
    stop = seg.stopwords

    checks = {
        "top50_stopword_tokens": {"legacy": sum(w in stop for w in top_old), "new": sum(w in stop for w in top_new)},
        "top50_pure_or_digit_tokens": {
            "legacy": sum(sg.PURE_NUMBER_RE.match(w) is not None for w in top_old),
            "new": sum(sg.PURE_NUMBER_RE.match(w) is not None for w in top_new)},
        "token_count_高雄市": {"legacy": cnt_old.get("高雄市", 0), "new": cnt_new.get("高雄市", 0)},
        "token_count_高雄": {"legacy": cnt_old.get("高雄", 0), "new": cnt_new.get("高雄", 0)},
        "token_count_高": {"legacy": cnt_old.get("高", 0), "new": cnt_new.get("高", 0)},
        "total_tokens": {"legacy": sum(cnt_old.values()), "new": sum(cnt_new.values())},
        "distinct_tokens": {"legacy": len(cnt_old), "new": len(cnt_new)},
        "titles_containing_高雄": sum(PLACE in t for t in docs.values()),
        "titles_containing_高雄市": sum("高雄市" in t for t in docs.values()),
        "titles_高雄_status": {k: {s: v.get(s, 0) for s in ("whole", "glued", "split")} for k, v in status.items()},
        "dict_freq": {k: {w: _freq(tk, w) for w in ("高雄市", "高雄")}
                      for k, tk in (("legacy", seg.legacy), ("new", seg.new))},
    }
    traces = traces_whole + traces_part

    (data / f"top50{suffix}.json").write_text(json.dumps(
        {"legacy": [[w, cnt_old[w]] for w in top_old], "new": [[w, cnt_new[w]] for w in top_new],
         "checks": checks, "traces": traces, "dict_version": seg.dict_version, "dict_hash": seg.dict_hash,
         "base_dict": seg.base_dict, "base_dict_hash": seg.base_dict_hash,
         "docs": len(docs)}, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        index_path = args.index.resolve().relative_to(REPO).as_posix()
    except ValueError:
        index_path = args.index.name
    body = render_block(tag, seg, docs, checks, top_old, top_new, cnt_old, cnt_new, traces, index_path)
    md = args.results.read_text(encoding="utf-8") if args.results.exists() else "# Q6 斷詞前後對照\n"
    args.results.write_text(upsert_block(md, tag, body), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=1))
    print(f"wrote {args.results} (block {tag}), {seg_out}")
    return checks


if __name__ == "__main__":
    main()
