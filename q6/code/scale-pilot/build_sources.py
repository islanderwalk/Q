#!/usr/bin/env python3
"""pilot 第 1 步：公開語料文字 → 斷詞（不碰 ES）。

- 文字來源（全部取自公開語料，沒有自己編）：
  * Gossiping-QA-Dataset.txt（PTT 八卦板問答，418,202 行）
  * repo q6/data/ptt_titles_7d.jsonl（6,685 則 PTT 標題）
  * repo q6/stack/data/channels/*.jsonl（六渠道公開頁樣本，共約 267 則）
  * 歷史爬蟲 YouTube 樣本 hist_youtube_2026-10.jsonl（13 則）
- 斷詞：直接呼叫 repo 的 qseg（q6/stack/seg 的 qseg 複製到 $SCALE_ROOT/code，不在 repo 內執行、不寫 repo）。
  * terms_v2 = qseg.pipeline.segment(dict v2)，字典 = dictsrv 的 current（= qsearch-q6-2026.10.30.2）
  * terms_v1 = qseg.pipeline.segment(dict v1)，字典 = qsearch-q6-2026.10.30.1（舊流程：純字典、HMM 關、不清洗）
  * terms_v0 = 近似 ES standard analyzer（CJK 逐字、西文依 \\w+、小寫、去重）；用 Python 重現，不是 ES 本身跑出來的
- 輸出 pickle：每個來源一筆（文字、渠道、三組詞的 vocab id），之後灌入時組合成文件。
"""
import json
import os
import pickle
import re
import sys
import time
from array import array
from pathlib import Path

os.environ.setdefault("DICT_BASE_URL", "http://127.0.0.1:8081")
SCALE_ROOT = Path(os.environ.get("SCALE_ROOT", "scale500m")).resolve()
os.environ.setdefault("DICT_CACHE", str(SCALE_ROOT / "dictcache"))
sys.path.insert(0, str(SCALE_ROOT / "code"))
from qseg import dictionary as dd  # noqa: E402
from qseg import pipeline as pl  # noqa: E402

DATA = SCALE_ROOT / "data"
OUT = DATA / "sources.pkl"
STATS = DATA / "sources_stats.json"
STD_RE = re.compile(r"[㐀-鿿豈-﫿]|\w+")


def std_terms(text):
    seen, out = set(), []
    for t in STD_RE.findall(text.lower()):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def load_sources():
    src = []  # (text, channel, origin)
    with open(DATA / "Gossiping-QA-Dataset.txt", encoding="utf-8") as fp:
        for line in fp:
            t = line.rstrip("\n").replace("\t", " ").strip()
            if t:
                src.append((t, "ptt", "gossiping_qa"))
    with open(DATA / "ptt_titles_7d.jsonl", encoding="utf-8") as fp:
        for line in fp:
            t = (json.loads(line).get("text") or "").strip()
            if t:
                src.append((t, "ptt", "ptt_title_7d"))
    for name in ["ptt.jsonl", "instagram.jsonl", "threads.jsonl", "media.jsonl",
                 "youtube.jsonl", "facebook.jsonl", "hist_youtube_2026-10.jsonl"]:
        with open(DATA / name, encoding="utf-8") as fp:
            for line in fp:
                if not line.strip():
                    continue
                d = json.loads(line)
                t = (d.get("text") or "").replace("\n", " ").strip()
                if t:
                    src.append((t, d.get("channel") or "ptt", "stack:" + name))
    return src


def main():
    t0 = time.time()
    src = load_sources()
    print(f"sources={len(src)} load_s={time.time() - t0:.1f}", flush=True)
    dic1 = dd.load("qsearch-q6-2026.10.30.1", pipeline="v1")
    dic2 = dd.load("current", pipeline="v2")
    print(f"dict v1={dic1.version} v2={dic2.version} total_pinned={dic2.manifest.get('total_pinned')}", flush=True)

    vocab, vlist = {}, []

    def ids(terms):
        out = array("I")
        for t in terms:
            i = vocab.get(t)
            if i is None:
                i = len(vlist)
                vocab[t] = i
                vlist.append(t)
            out.append(i)
        return out

    texts, chans, origins, V0, V1, V2 = [], [], [], [], [], []
    t1 = time.time()
    for n, (t, ch, org) in enumerate(src):
        texts.append(t)
        chans.append(ch)
        origins.append(org)
        V0.append(ids(std_terms(t)))
        V1.append(ids(pl.segment(dic1, ch, t)))
        V2.append(ids(pl.segment(dic2, ch, t)))
        if n and n % 50000 == 0:
            rate = n / (time.time() - t1)
            print(f"  seg {n:,}/{len(src):,} {rate:,.0f} src/s vocab={len(vlist):,}", flush=True)
    seg_s = time.time() - t1

    n = len(src)
    stats = {
        "sources": n,
        "unique_texts": len(set(texts)),
        "by_origin": {},
        "channels": {},
        "avg_terms_v0": round(sum(len(x) for x in V0) / n, 2),
        "avg_terms_v1": round(sum(len(x) for x in V1) / n, 2),
        "avg_terms_v2": round(sum(len(x) for x in V2) / n, 2),
        "avg_chars": round(sum(len(t) for t in texts) / n, 1),
        "vocab_size": len(vlist),
        "dict_v1": dic1.version,
        "dict_v2": dic2.version,
        "seg_seconds": round(seg_s, 1),
        "src_per_sec": round(n / seg_s, 1),
    }
    for o in origins:
        stats["by_origin"][o] = stats["by_origin"].get(o, 0) + 1
    for c in chans:
        stats["channels"][c] = stats["channels"].get(c, 0) + 1
    with open(OUT, "wb") as fp:
        pickle.dump({"texts": texts, "chans": chans, "origins": origins, "V0": V0, "V1": V1, "V2": V2,
                     "vocab": vlist, "dict_v1": dic1.version, "dict_v2": dic2.version}, fp, protocol=4)
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print("stats", json.dumps(stats, ensure_ascii=False), flush=True)
    print(f"wrote {OUT} {OUT.stat().st_size / 1e6:.1f} MB total_s={time.time() - t0:.1f}", flush=True)


if __name__ == "__main__":
    main()
