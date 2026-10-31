"""重要詞召回評估（2026-10-31，回應審查第 1 點）。

金標：150 個 PTT 八卦板標題（q6/data/ptt_titles_7d.jsonl 隨機抽樣，seed 20261031），三位標註者各自獨立列出
「文字雲該出現的詞」，兩位以上同意的詞才算金標詞。標註者沒看過任何分詞結果。

比較三種流程，指標是「金標詞有沒有原封不動成為一個 token」（嚴格比對；拆開或被過濾都算漏）：
  legacy   舊流程：原文純字典斷詞，不過濾（參考線，雜訊多）
  rule1030 10/30 版新流程：方括號 1–8 字一律去掉、單字全刪、使用者字典 10.30.1、total 不固定
  rule1031 10/31 版新流程：只去白名單標籤與英數帳號、單字保留名單、使用者字典 10.31.1、total 固定
另外把樣本依序號奇偶拆成 A、B 兩半各算一次：保留名單與字典改動只參考審查的例子，B 半是沒看過的對照。

python -I q6/code/recall_eval.py --base-dict dict.txt.big --gold gold_sample_150.json \
    --annot annot_A.json annot_B.json annot_C.json --old-user-dict user_dict_1030.txt --out q6/data/recall_eval
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import segmenter as sg  # noqa: E402


def norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "").lower()


def gold_terms(annots: list, min_votes: int = 2) -> dict:
    out = {}
    for rows in zip(*annots):
        ids = {r["id"] for r in rows}
        assert len(ids) == 1, ids
        votes = Counter()
        for r in rows:
            votes.update({norm(t) for t in r["terms"] if norm(t)})
        out[rows[0]["id"]] = sorted(t for t, v in votes.items() if v >= min_votes)
    return out


def segment_1030(seg, text):
    clean = sg.preprocess(text, seg.ptt_tags, bracket_mode="all")
    return [t for t in seg.new.lcut(clean, HMM=True) if sg._keep_new(t.strip(), seg.stopwords)]


def evaluate(titles: dict, gold: dict, pipelines: dict) -> dict:
    res = {}
    for name, fn in pipelines.items():
        stats = {"all": [0, 0], "A": [0, 0], "B": [0, 0]}
        missed = Counter()
        n_out = 0
        for i, (doc_id, title) in enumerate(titles.items()):
            toks = {norm(t) for t in fn(title)}
            n_out += len(toks)
            half = "A" if i % 2 == 0 else "B"
            for g in gold[doc_id]:
                hit = g in toks
                for k in ("all", half):
                    stats[k][0] += hit
                    stats[k][1] += 1
                if not hit:
                    missed[g] += 1
        res[name] = {k: {"kept": a, "gold": b, "recall": round(a / b, 4) if b else None}
                     for k, (a, b) in stats.items()}
        res[name]["distinct_tokens_per_title"] = round(n_out / len(titles), 2)
        res[name]["missed_examples"] = [t for t, _ in missed.most_common(40)]
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dict", type=Path, required=True)
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--annot", type=Path, nargs=3, required=True)
    ap.add_argument("--old-user-dict", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    sample = json.loads(a.gold.read_text(encoding="utf-8"))
    titles = {r["id"]: r["title"] for r in sample}
    annots = [json.loads(p.read_text(encoding="utf-8")) for p in a.annot]
    gold = gold_terms(annots)
    seg31 = sg.Segmenters.build(base_dict=a.base_dict)
    seg30 = sg.Segmenters.build(base_dict=a.base_dict, user_dict=a.old_user_dict, pin_total=False)
    pipelines = {
        "legacy": lambda t: sg.segment_legacy(seg31, t),
        "rule1030": lambda t: segment_1030(seg30, t),
        "rule1031": lambda t: sg.segment_new(seg31, t),
    }
    res = evaluate(titles, gold, pipelines)
    agree = Counter(len(v) for v in gold.values())
    wen = {t: {"rule1030": segment_1030(seg30, t), "rule1031": sg.segment_new(seg31, t)}
           for t in ("颱風假", "癌", "[高雄市]今天下雨")}
    out = {"titles": len(titles), "gold_terms": sum(len(v) for v in gold.values()),
           "titles_with_no_gold_term": agree[0], "min_votes": 2, "base_dict": seg31.base_dict,
           "seg_build_1031": seg31.seg_build, "results": res, "wen_examples": wen, "gold": gold}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "recall.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "gold"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
