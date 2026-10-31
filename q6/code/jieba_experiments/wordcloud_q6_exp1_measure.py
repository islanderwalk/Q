"""Q6 實驗：jieba 吞吐、症狀重現、加詞的影響範圍（不變量）。

用法: python -I wordcloud_q6_exp1_measure.py <dict.txt.big> <Gossiping-QA-Dataset.txt> <out_dir>
"""
import json
import math
import re
import sys
import time
from pathlib import Path

import jieba

BIG_DICT, CORPUS, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
jieba.setLogLevel(60)


def load_posts(path, limit=None):
    posts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            q, _, a = line.rstrip("\n").partition("\t")
            if a == "沒有資料":
                a = ""
            text = (q + " " + a).strip()
            if text:
                posts.append(text)
            if limit and len(posts) >= limit:
                break
    return posts


def new_tokenizer(dictionary=None):
    tk = jieba.Tokenizer(dictionary=dictionary) if dictionary else jieba.Tokenizer()
    tk.initialize()
    return tk


results = {}
posts = load_posts(CORPUS)
results["corpus"] = {
    "posts": len(posts),
    "chars": sum(len(p) for p in posts),
    "avg_chars": round(sum(len(p) for p in posts) / len(posts), 1),
}

# ---------- 1. 吞吐：單一行程，精確模式 ----------
bench = posts[:100_000]
bench_chars = sum(len(p) for p in bench)
tp = {}
for name, dictionary in [("default_dict", None), ("dict_big", BIG_DICT)]:
    tk = new_tokenizer(dictionary)
    for hmm in (True, False):
        t0 = time.perf_counter()
        for p in bench:
            tk.lcut(p, HMM=hmm)
        dt = time.perf_counter() - t0
        tp[f"{name}_hmm_{hmm}"] = {
            "seconds": round(dt, 2),
            "chars_per_sec": round(bench_chars / dt),
        }
results["throughput"] = {"bench_posts": len(bench), "bench_chars": bench_chars, **tp}

# ---------- 2. 加詞的影響範圍 ----------
base = new_tokenizer(BIG_DICT)
sample = posts[:200_000]
before = [tuple(base.lcut(p)) for p in sample]


def blast_radius(words, freq=None, label=""):
    tk = new_tokenizer(BIG_DICT)
    total_before = tk.total
    for w in words:
        tk.add_word(w, freq) if freq else tk.add_word(w)
    after = [tuple(tk.lcut(p)) for p in sample]
    contains = changed_with = changed_without = 0
    examples = []
    for p, b, a in zip(sample, before, after):
        has = any(w in p for w in words)
        contains += has
        if a != b:
            if has:
                changed_with += 1
                if len(examples) < 3:
                    examples.append({"text": p[:60], "before": "/".join(b)[:90], "after": "/".join(a)[:90]})
            else:
                changed_without += 1
    return {
        "label": label,
        "words": words,
        "freq": freq,
        "total_before": total_before,
        "total_after": tk.total,
        "sample_posts": len(sample),
        "posts_containing_word": contains,
        "changed_containing_word": changed_with,
        "changed_NOT_containing_word": changed_without,
        "examples": examples,
    }


# 先找出語料裡高頻、但 dict.txt.big 沒收的候選詞（示範用）
candidates = ["肥宅", "魯蛇", "柯粉", "鄉民", "八卦板", "小確幸", "白海豚", "台北捷運", "高雄市", "芒果乾", "塑膠袋"]
in_dict = {w: (w in base.FREQ and base.FREQ[w] > 0) for w in candidates}
freq_in_corpus = {w: sum(1 for p in sample if w in p) for w in candidates}
results["candidate_words"] = {"in_dict_big": in_dict, "posts_containing": freq_in_corpus}

new_words = [w for w in candidates if not in_dict[w] and freq_in_corpus[w] > 0]
radius = []
if new_words:
    radius.append(blast_radius([new_words[0]], label="新增 1 個詞（jieba 建議頻率）"))
    radius.append(blast_radius(new_words, label=f"一次發版新增 {len(new_words)} 個詞"))
    radius.append(blast_radius([new_words[0]], freq=10_000_000, label="壓力測試：給 1 個詞極端頻率 1e7"))
results["blast_radius"] = radius
results["logtotal_note"] = {
    "total_dict_big": base.total,
    "log_total": round(math.log(base.total), 6),
}

# ---------- 3. 症狀重現（自寫示意貼文，不是真實資料） ----------
demo_posts = [
    "高雄市今天熱爆了，晚上去瑞豐夜市吃東西 👉 https://example.com/9ZxW5k 訂位專線 0912-345-678",
    "#高雄美食 這家牛肉湯真的讚！老闆說週末要先預約 07-123-4567",
    "台積電法說會今天登場，護國神山的毛利率又創新高了",
    "蝦皮購物雙11優惠碼在這 example.com/3xYz9Ab 手刀搶購",
    "臺北市長今天在臺北車站宣布新政策，台北捷運週末加開班次",
    "我在Threads上看到大家都在討論這件事，真的是很誇張",
]

URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|cc|ly|tw|io|me|is|to|gl)(?:/\S*)?", re.I)
PHONE_RE = re.compile(r"(?:\+?886[-\s]?|0)(?:9\d{2}[-\s]?\d{3}[-\s]?\d{3}|\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4})")
HASHTAG_RE = re.compile(r"#([\w一-鿿]+)")
STOP = set("的 了 在 是 我 也 就 都 而 及 與 著 或 一個 沒有 我們 你們 他們 這 那 很 真的 今天 上 要 說 這件 事 大家 看到 這家 先 又 在這".split())
KEEP_SINGLE = set("讚爛貴醜爽慘熱冷")
NUM_KEEP = {"11", "101", "228", "520", "711"}
NORMALIZE = {"臺北市": "台北市", "臺北車站": "台北車站", "臺北": "台北", "臺灣": "台灣", "臺中": "台中"}
USER_WORDS = [
    ("高雄市", 3000, "ns"), ("瑞豐夜市", 500, "ns"), ("高雄美食", 300, "n"), ("牛肉湯", 800, "n"),
    ("台積電", 3000, "nt"), ("法說會", 800, "n"), ("護國神山", 500, "n"), ("毛利率", 800, "n"),
    ("蝦皮購物", 800, "nt"), ("雙11", 500, "n"), ("優惠碼", 500, "n"), ("手刀", 300, "v"),
    ("臺北市長", 500, "n"), ("臺北車站", 1000, "ns"), ("台北捷運", 1000, "nt"), ("加開", 300, "v"),
    ("Threads", 500, "nz"), ("熱爆", 300, "a"),
]


def v2_clean(text):
    text = URL_RE.sub(" ", text)
    text = PHONE_RE.sub(" ", text)
    tags = HASHTAG_RE.findall(text)
    text = HASHTAG_RE.sub(" ", text)
    return text, tags


def v2_filter(tok):
    tok = tok.strip()
    if not tok or re.fullmatch(r"[\W_]+", tok):
        return None
    if tok.isdigit() and tok not in NUM_KEEP:
        return None
    if re.fullmatch(r"[A-Za-z0-9]{5,}", tok) and re.search(r"\d", tok) and re.search(r"[A-Za-z]", tok):
        return None  # 亂碼型字串（短網址後綴、追蹤碼）
    if tok in STOP:
        return None
    if len(tok) == 1 and tok not in KEEP_SINGLE and not tok.isascii():
        return None
    return NORMALIZE.get(tok, tok)


v1_tk = new_tokenizer()  # 預設字典、不清洗：當作「沒處理」的基準
v2_tk = new_tokenizer(BIG_DICT)
for w, f, t in USER_WORDS:
    v2_tk.add_word(w, f, t)

demo = []
for p in demo_posts:
    v1 = [t for t in v1_tk.lcut(p) if t.strip()]
    cleaned, tags = v2_clean(p)
    raw2 = v2_tk.lcut(cleaned)
    v2 = [x for x in (v2_filter(t) for t in raw2) if x] + tags
    demo.append({"post": p, "v1_tokens": v1, "v2_raw_tokens": [t for t in raw2 if t.strip()], "v2_tokens": v2})
results["demo"] = demo

# v1 vs v2 在無清洗、同一字典下對地名的處理（只看斷詞器本身）
place_tests = ["高雄市今天很熱", "我住在臺北市大安區", "台北市長出席記者會", "去瑞豐夜市吃東西"]
results["place_tests"] = {
    p: {
        "default_dict": "/".join(v1_tk.lcut(p)),
        "dict_big": "/".join(base.lcut(p)),
        "dict_big_no_hmm": "/".join(base.lcut(p, HMM=False)),
    }
    for p in place_tests
}

(OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results, ensure_ascii=False, indent=2))
