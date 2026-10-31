"""Q6 實驗二：為什麼不含新詞的句子也會變？固定 total 能不能讓影響範圍可算？
以及：常駐 process 逐步 add_word vs 每次從版本化字典檔整份重載，結果一不一致？

用法: python -I wordcloud_q6_exp2_pinned_total.py <dict.txt.big> <Gossiping-QA-Dataset.txt> <out_dir>
"""
import json
import sys
from pathlib import Path

import jieba

BIG_DICT, CORPUS, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
jieba.setLogLevel(60)


def load_posts(path, limit):
    posts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            q, _, a = line.rstrip("\n").partition("\t")
            text = (q + " " + ("" if a == "沒有資料" else a)).strip()
            if text:
                posts.append(text)
            if len(posts) >= limit:
                break
    return posts


def tok(dictionary):
    t = jieba.Tokenizer(dictionary=dictionary)
    t.initialize()
    return t


posts = load_posts(CORPUS, 200_000)
base = tok(BIG_DICT)
before = [tuple(base.lcut(p)) for p in posts]
T0 = base.total


def compare(tk, words):
    with_w = without_w = 0
    examples = []
    for p, b in zip(posts, before):
        a = tuple(tk.lcut(p))
        if a == b:
            continue
        if any(w in p for w in words):
            with_w += 1
        else:
            without_w += 1
            if len(examples) < 4:
                examples.append({"text": p[:50], "before": "/".join(b)[:80], "after": "/".join(a)[:80]})
    return {"changed_containing": with_w, "changed_NOT_containing": without_w, "examples_not_containing": examples}


release = ["肥宅", "魯蛇", "柯粉", "八卦板", "小確幸", "台北捷運"]
res = {"sample_posts": len(posts), "total_base": T0, "cases": []}

for label, words, freq, pin in [
    ("加 1 詞，total 照 jieba 預設累加", ["肥宅"], None, False),
    ("加 1 詞，total 固定", ["肥宅"], None, True),
    ("一次發版 6 詞，total 照預設累加", release, None, False),
    ("一次發版 6 詞，total 固定", release, None, True),
    ("壓力：1 詞頻率 1e7，total 照預設累加", ["肥宅"], 10_000_000, False),
    ("壓力：1 詞頻率 1e7，total 固定", ["肥宅"], 10_000_000, True),
]:
    tk = tok(BIG_DICT)
    for w in words:
        tk.add_word(w, freq) if freq else tk.add_word(w)
    if pin:
        tk.total = T0
    r = compare(tk, words)
    r.update({"label": label, "words": words, "freq": freq, "total_after": tk.total})
    res["cases"].append(r)

# 常駐 process 逐步熱更新 vs 從合併後字典檔整份重載
#   v2 版字典 = big dict + release 新詞 + 調高既有詞「鄉民」的頻率（覆寫）
bump_word, bump_freq = "鄉民", base.FREQ["鄉民"] * 20
merged = OUT / "dict_v2_merged.txt"
lines = []
with open(BIG_DICT, encoding="utf-8") as f:
    for line in f:
        w = line.split(" ", 1)[0]
        if w == bump_word:
            parts = line.rstrip("\n").split(" ")
            parts[1] = str(bump_freq)
            line = " ".join(parts) + "\n"
        lines.append(line)
fresh_probe = tok(BIG_DICT)
added = []
for w in release:
    added.append(f"{w} {fresh_probe.suggest_freq(w, False)} n\n")
merged.write_text("".join(lines) + "".join(added), encoding="utf-8")

hot = tok(BIG_DICT)            # 常駐 worker：在記憶體裡逐步 add_word
for w in release:
    hot.add_word(w)
hot.add_word(bump_word, bump_freq)
fresh = tok(str(merged))       # 批次 job：每次從版本化字典檔整份重載
diff = 0
examples = []
for p in posts:
    a, b = hot.lcut(p), fresh.lcut(p)
    if a != b:
        diff += 1
        if len(examples) < 4:
            examples.append({"text": p[:50], "hot_patched": "/".join(a)[:80], "fresh_load": "/".join(b)[:80]})
res["hot_vs_fresh"] = {
    "hot_total": hot.total,
    "fresh_total": fresh.total,
    "posts_differ": diff,
    "examples": examples,
    "bump": {"word": bump_word, "old_freq": base.FREQ[bump_word], "new_freq": bump_freq},
}

# 同一個字典檔、兩個獨立 process 載入：結果應完全一致（決定性）
fresh2 = tok(str(merged))
res["fresh_vs_fresh_differ"] = sum(1 for p in posts if fresh.lcut(p) != fresh2.lcut(p))

(OUT / "results2.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
