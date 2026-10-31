"""Q6 佐證用的快速重跑：環境、吞吐、大字典收錄、加詞影響範圍（total 預設累加 vs 固定）。

用法: python -I wordcloud_q6_verify.py <dict.txt.big> <Gossiping-QA-Dataset.txt>
語料: https://github.com/zake7749/Gossiping-Chinese-Corpus （data/Gossiping-QA-Dataset.txt）
字典: https://github.com/fxsjy/jieba/tree/master/extra_dict （dict.txt.big）
"""
import hashlib
import platform
import sys
import time
from datetime import datetime, timezone

import jieba

BIG_DICT, CORPUS = sys.argv[1], sys.argv[2]
jieba.setLogLevel(60)


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def cpu_model():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


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


log(f"環境：Python {platform.python_version()} · jieba {jieba.__version__} · CPU {cpu_model()} · 單一行程")
log(f"檔案 sha256（前 16 碼）：dict.txt.big={sha256(BIG_DICT)} · Gossiping-QA-Dataset.txt={sha256(CORPUS)}")
posts = load_posts(CORPUS, 200_000)
chars = sum(len(p) for p in posts)
log(f"語料：前 {len(posts):,} 篇，{chars:,} 字，平均 {chars / len(posts):.1f} 字")

base = tok(BIG_DICT)
bench = posts[:50_000]
bench_chars = sum(len(p) for p in bench)
t0 = time.perf_counter()
for p in bench:
    base.lcut(p)
dt = time.perf_counter() - t0
log(f"1) 吞吐：dict.txt.big＋HMM，{len(bench):,} 篇／{bench_chars:,} 字，{dt:.2f} 秒 → {bench_chars / dt:,.0f} 字/秒")

words = ["台積電", "法說會", "蝦皮購物", "臺北車站", "台北捷運", "瑞豐夜市", "護國神山", "高雄市", "臺北市"]
log("2) dict.txt.big 是否收錄：" + "、".join(f"{w}={'有' if base.FREQ.get(w) else '無'}" for w in words))

before = [tuple(base.lcut(p)) for p in posts]
T0 = base.total
for pin in (False, True):
    tk = tok(BIG_DICT)
    tk.add_word("肥宅")
    if pin:
        tk.total = T0
    with_w = without_w = 0
    for p, b in zip(posts, before):
        if tuple(tk.lcut(p)) != b:
            if "肥宅" in p:
                with_w += 1
            else:
                without_w += 1
    mode = "total 固定" if pin else "total 預設累加"
    log(f"3) 新增「肥宅」（{mode}，total {T0:,} → {tk.total:,}）：含該詞且改變 {with_w:,} 篇；不含該詞卻改變 {without_w:,} 篇")
log("完成")
