"""Q6 實驗三：產生研究頁用的示意資料（v1/v2 斷詞、問題標記、搜尋粒度對照）。

用法: python -I wordcloud_q6_exp3_demo_tokens.py <dict.txt.big> <out_dir>
"""
import json
import re
import sys
from pathlib import Path

import jieba

BIG_DICT, OUT = sys.argv[1], Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
jieba.setLogLevel(60)

DEMO = [
    "高雄市今天熱爆了，晚上去瑞豐夜市吃東西 👉 https://example.com/9ZxW5k 訂位專線 0912-345-678",
    "#高雄美食 這家牛肉湯真的讚！老闆說週末要先預約 07-123-4567",
    "台積電法說會今天登場，護國神山的毛利率又創新高了",
    "蝦皮購物雙11優惠碼在這 example.com/3xYz9Ab 手刀搶購",
    "臺北市長今天在臺北車站宣布新政策，台北捷運週末加開班次",
    "我在Threads上看到大家都在討論這件事，是很誇張",
]
URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|cc|ly|tw|io|me|is|to|gl)(?:/\S*)?", re.I)
PHONE_RE = re.compile(r"(?:\+?886[-\s]?|0)(?:9\d{2}[-\s]?\d{3}[-\s]?\d{3}|\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4})")
HASHTAG_RE = re.compile(r"#([\w一-鿿]+)")
HARD_STOP = set("的 了 在 是 我 也 就 都 而 及 與 著 或 這 那 很 上 要 說 先 又 這件 事 這家 在這".split())
SOFT_STOP = set("今天 真的 大家 看到 晚上 東西".split())  # 查詢時 exclude，PM 可調
KEEP_SINGLE = set("讚爛貴醜爽慘熱冷")
NUM_KEEP = {"11", "101", "228", "520", "711"}
VARIANT = str.maketrans({"臺": "台"})
USER_WORDS = [
    ("高雄市", 3000, "ns"), ("瑞豐夜市", 500, "ns"), ("高雄美食", 300, "n"), ("牛肉湯", 800, "n"),
    ("台積電", 3000, "nt"), ("法說會", 800, "n"), ("護國神山", 500, "n"), ("毛利率", 800, "n"),
    ("蝦皮購物", 800, "nt"), ("雙11", 500, "n"), ("優惠碼", 500, "n"), ("手刀", 300, "v"),
    ("臺北市長", 500, "n"), ("臺北車站", 1000, "ns"), ("台北捷運", 1000, "nt"), ("加開", 300, "v"),
    ("Threads", 500, "nz"), ("熱爆", 300, "a"), ("老闆", 3000, "n"),
]
ENTITIES = [w for w, _, _ in USER_WORDS if len(w) >= 2]


def tk(dictionary=None, user=False):
    t = jieba.Tokenizer(dictionary=dictionary) if dictionary else jieba.Tokenizer()
    t.initialize()
    if user:
        for w, f, tag in USER_WORDS:
            t.add_word(w, f, tag)
    return t


v1 = tk()
v2 = tk(BIG_DICT, user=True)


def spans(regex, text):
    return [m.span() for m in regex.finditer(text)]


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def label_v1(text):
    url_s, phone_s = spans(URL_RE, text), spans(PHONE_RE, text)
    ent_s = []
    for e in ENTITIES:
        for m in re.finditer(re.escape(e), text):
            ent_s.append((m.span(), e))
    out = []
    for word, s, e in v1.tokenize(text):
        if not word.strip():
            continue
        kind = "ok"
        if any(overlap((s, e), sp) for sp in url_s):
            kind = "url"
        elif any(overlap((s, e), sp) for sp in phone_s):
            kind = "phone"
        elif re.fullmatch(r"[\W_]+", word):
            kind = "punct"
        elif word in HARD_STOP or word in SOFT_STOP:
            kind = "stop"
        else:
            for sp, ent in ent_s:
                if overlap((s, e), sp) and word != ent and not (s <= sp[0] and e >= sp[1]):
                    kind = "split"
                    break
        out.append([word, kind])
    return out


def run_v2(text):
    cleaned = URL_RE.sub(" ", text)
    cleaned = PHONE_RE.sub(" ", cleaned)
    tags = HASHTAG_RE.findall(cleaned)
    cleaned = HASHTAG_RE.sub(" ", cleaned)
    raw = [w for w in v2.lcut(cleaned) if w.strip()]
    kept, dropped = [], []
    for w in raw:
        reason = None
        if re.fullmatch(r"[\W_]+", w):
            reason = "punct"
        elif w.isdigit() and w not in NUM_KEEP:
            reason = "number"
        elif w in HARD_STOP:
            reason = "hard_stop"
        elif len(w) == 1 and not w.isascii() and w not in KEEP_SINGLE:
            reason = "single"
        if reason:
            dropped.append([w, reason])
        else:
            kept.append([w.translate(VARIANT), "soft_stop" if w in SOFT_STOP else "kept"])
    kept += [[t.translate(VARIANT), "hashtag"] for t in tags]
    return {"cleaned": re.sub(r"\s+", " ", cleaned).strip(), "kept": kept, "dropped": dropped}


demo = [{"post": p, "v1": label_v1(p), "v2": run_v2(p)} for p in DEMO]

granularity = {}
for s in ["台北捷運週末加開班次", "台積電法說會今天登場", "高雄市瑞豐夜市"]:
    granularity[s] = {
        "precise": "/".join(v2.lcut(s)),
        "search": "/".join(v2.lcut_for_search(s)),
    }

big = tk(BIG_DICT)
checks = {w: bool(big.FREQ.get(w)) for w in ["加開班次", "加開", "班次", "護國神山", "台積電", "法說會", "蝦皮購物", "瑞豐夜市", "老闆", "臺北車站", "台北捷運"]}

res = {"demo": demo, "granularity": granularity, "in_dict_big": checks, "jieba": jieba.__version__}
(OUT / "results3.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
for d in demo:
    print(d["post"])
    print("  v1:", " ".join(f"{w}[{k}]" if k != "ok" else w for w, k in d["v1"]))
    print("  v2:", " ".join(f"{w}" + ("" if k == "kept" else f"[{k}]") for w, k in d["v2"]["kept"]), "| dropped:", d["v2"]["dropped"])
print(json.dumps(granularity, ensure_ascii=False, indent=1))
print(checks)
