#!/usr/bin/env python3
"""pilot 第 2 步：灌入 scale_pilot_* 索引，逐段量測（不碰 posts 索引、不碰 repo）。

每一段（stage）：
  1. 灌到目標篇數（bulk、refresh_interval=-1、replicas=0）
  2. refresh → 量 store.size、docs、segments、heap、fielddata
  3. force_merge(max_num_segments=1) → 再量一次 store.size（磁碟 bytes/doc）
  4. 查詢：4 種範圍 × 3 種聚合（精確 / random_sampler 1% / 0.1%），冷、熱各 5 次取中位數，
     並算前 50 詞與精確結果的重疊
  5. 寫 $SCALE_ROOT/out/stage_<目標>.json；進度寫 $SCALE_ROOT/progress.json

安全閘：
  - C: 剩餘 < STOP_GIB（預設 130 GiB）就停（每 100k 篇、每段開始前、merge 前都檢查）
  - 放 $SCALE_ROOT/STOP 這個檔案就會在下一個檢查點停下
文件組成（公開語料文字的重抽樣，見報告）：每篇 = 兩則公開來源（PTT 問答／標題／渠道線上貼文）拼接；
詞表 = 兩則各自斷詞結果取聯集去重；發佈時間在 2023-01 至 2026-10 的 46 個月內均勻抽樣。
"""
import json
import os
import pickle
import random
import statistics
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

import requests

SCALE_ROOT = Path(os.environ.get("SCALE_ROOT", "scale500m")).resolve()
sys.path.insert(0, str(SCALE_ROOT / "code"))
os.environ.setdefault("DICT_BASE_URL", "http://127.0.0.1:8081")
os.environ.setdefault("DICT_CACHE", str(SCALE_ROOT / "dictcache"))
from qseg import pipeline as pl  # noqa: E402

ES = os.environ.get("ES_URL", "http://127.0.0.1:9200")
IDX = os.environ.get("PILOT_INDEX", "scale_pilot_main")
BASE = SCALE_ROOT
OUT = BASE / "out"
OUT.mkdir(parents=True, exist_ok=True)
PROG = BASE / "progress.json"
STOP_FILE = BASE / "STOP"
STOP_GIB = float(os.environ.get("STOP_GIB", "130"))
TARGETS = [int(x) for x in os.environ.get("TARGETS", "1000000,5000000,20000000,50000000").split(",")]
BATCH = int(os.environ.get("BATCH", "5000"))
SEED = 20261031
MONTHS = [f"{y}-{m:02d}" for y in range(2023, 2027) for m in range(1, 13) if (y, m) <= (2026, 10)]  # 46 個月
CH = [("ptt", 70), ("facebook", 10), ("instagram", 6), ("youtube", 5), ("media", 6), ("threads", 3)]
CH_POOL = [c for c, w in CH for _ in range(w)]
KW = "中國"  # 文字雲關鍵字過濾用（會先確認它在 terms_v2 裡）

MAPPING = {
    "settings": {
        "number_of_shards": 3,
        "number_of_replicas": 0,
        "refresh_interval": "-1",
        "analysis": {
            "analyzer": {
                "ik_index": {"type": "custom", "tokenizer": "ik_max_word"},
                "ik_search": {"type": "custom", "tokenizer": "ik_smart", "filter": ["lowercase"]},
                "cjk_bigram_idx": {"type": "custom", "tokenizer": "standard",
                                   "filter": ["cjk_width", "lowercase", "cjk_bigram"]},
            }
        },
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "post_id": {"type": "keyword"}, "channel": {"type": "keyword"}, "sub_type": {"type": "keyword"},
            "source": {"type": "keyword"}, "url": {"type": "keyword", "index": False},
            "published_at": {"type": "date"}, "fetched_at": {"type": "date"}, "month": {"type": "keyword"},
            "content": {"type": "text", "analyzer": "ik_index", "search_analyzer": "ik_search",
                        "fields": {"bigram": {"type": "text", "analyzer": "cjk_bigram_idx"}}},
            "terms_v0": {"type": "keyword"},
            "terms_v1": {"type": "keyword"}, "seg_ver_v1": {"type": "long"}, "dict_version_v1": {"type": "keyword"},
            "seg_build_v1": {"type": "keyword"}, "seg_at_v1": {"type": "date"},
            "terms_v2": {"type": "keyword", "eager_global_ordinals": True}, "seg_ver_v2": {"type": "long"},
            "dict_version_v2": {"type": "keyword"}, "seg_build_v2": {"type": "keyword"}, "seg_at_v2": {"type": "date"},
        },
    },
}

SCOPES = {
    "all": {"match_all": {}},
    "ptt_2026-10": {"bool": {"filter": [{"term": {"channel": "ptt"}}, {"term": {"month": "2026-10"}}]}},
    f"kw_{KW}_all": {"bool": {"filter": [{"term": {"terms_v2": KW}}]}},
    f"kw_{KW}_ptt_2026-10": {"bool": {"filter": [{"term": {"terms_v2": KW}}, {"term": {"channel": "ptt"}},
                                                  {"term": {"month": "2026-10"}}]}},
}


def agg_terms():
    return {"t": {"terms": {"field": "terms_v2", "size": 50}}}


VARIANTS = {
    "exact": agg_terms(),
    "sampler_1pct": {"s": {"random_sampler": {"probability": 0.01, "seed": 7}, "aggs": agg_terms()}},
    "sampler_0.1pct": {"s": {"random_sampler": {"probability": 0.001, "seed": 7}, "aggs": agg_terms()}},
}

session = requests.Session()
session.trust_env = False


def es(method, path, body=None, raw=None, timeout=600, ndjson=False):
    kw = {"timeout": timeout}
    if raw is not None:
        kw["data"] = raw
        kw["headers"] = {"Content-Type": "application/x-ndjson"}
    elif body is not None:
        kw["json"] = body
    r = session.request(method, f"{ES}{path}", **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def c_free_gib():
    st = os.statvfs(os.environ.get("DISK_GUARD_PATH", str(BASE)))
    return st.f_bavail * st.f_frsize / 2 ** 30


def write_progress(**kw):
    kw["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    kw["c_free_gib"] = round(c_free_gib(), 2)
    PROG.write_text(json.dumps(kw, ensure_ascii=False, indent=1), encoding="utf-8")


class Stop(Exception):
    pass


def check_stop(where):
    if STOP_FILE.exists():
        raise Stop(f"STOP 檔案存在（{where}）")
    f = c_free_gib()
    if f < STOP_GIB:
        raise Stop(f"C: 剩 {f:.1f} GiB < 安全下限 {STOP_GIB} GiB（{where}）")


class Corpus:
    def __init__(self, path):
        with open(path, "rb") as fp:
            d = pickle.load(fp)
        self.texts = d["texts"]
        self.chans = d["chans"]
        self.V0, self.V1, self.V2 = d["V0"], d["V1"], d["V2"]
        self.vocab = d["vocab"]
        self.S = len(self.texts)
        self.dict_v1, self.dict_v2 = d["dict_v1"], d["dict_v2"]
        self.sv1 = pl.seg_ver("v1", self.dict_v1)
        self.sv2 = pl.seg_ver("v2", self.dict_v2)
        self.now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def union(self, lists_a, lists_b):
        voc = self.vocab
        return [voc[i] for i in dict.fromkeys([*lists_a, *lists_b])]

    def make_payload(self, lo, hi):
        lines = []
        S = self.S
        for i in range(lo, hi):
            r = random.Random(SEED * 1_000_003 + i)
            ch = CH_POOL[r.randrange(len(CH_POOL))]
            month = MONTHS[r.randrange(len(MONTHS))]
            a, b = r.randrange(S), r.randrange(S)
            day = r.randrange(1, 29)
            secs = r.randrange(86400)
            ts = f"{month}-{day:02d}T{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}+08:00"
            content = f"{self.texts[a]} {self.texts[b]}"
            doc = {
                "post_id": f"pilot{i}", "channel": ch, "sub_type": "post", "source": f"pilot-{ch}",
                "url": f"https://pilot.scale.local/p/{i}", "published_at": ts, "fetched_at": ts, "month": month,
                "content": content,
                "terms_v0": self.union(self.V0[a], self.V0[b]),
                "terms_v1": self.union(self.V1[a], self.V1[b]), "seg_ver_v1": self.sv1,
                "dict_version_v1": self.dict_v1, "seg_build_v1": "pilot", "seg_at_v1": self.now,
                "terms_v2": self.union(self.V2[a], self.V2[b]), "seg_ver_v2": self.sv2,
                "dict_version_v2": self.dict_v2, "seg_build_v2": "pilot", "seg_at_v2": self.now,
            }
            lines.append(json.dumps({"index": {"_index": IDX, "_id": f"pilot{i}"}}, separators=(",", ":")))
            lines.append(json.dumps(doc, ensure_ascii=False))
        return ("\n".join(lines) + "\n").encode("utf-8")


def post_bulk(payload, n):
    r = es("POST", "/_bulk?filter_path=errors,took", raw=payload, timeout=1800)
    if r.get("errors"):
        raise RuntimeError(f"bulk {n} 有錯誤")
    return n


def index_stats():
    cat = es("GET", f"/_cat/indices/{IDX}?format=json&bytes=b&h=docs.count,docs.deleted,store.size,pri.store.size")
    st = es("GET", f"/{IDX}/_stats/store,docs,segments")["indices"][IDX]["primaries"]
    return {"cat": cat[0] if cat else {}, "segments": st["segments"]["count"],
            "store_bytes_primaries": st["store"]["size_in_bytes"], "docs": st["docs"]["count"]}


def jvm_stats():
    j = es("GET", "/_nodes/stats/jvm,os?filter_path=nodes.*.jvm.mem,nodes.*.os.mem")
    node = next(iter(j["nodes"].values()))
    return {"heap_used_mb": round(node["jvm"]["mem"]["heap_used_in_bytes"] / 2 ** 20, 1),
            "heap_max_mb": round(node["jvm"]["mem"]["heap_max_in_bytes"] / 2 ** 20, 1),
            "os_used_pct": node["os"]["mem"]["used_percent"]}


def fielddata_stats():
    s = es("GET", f"/{IDX}/_stats/fielddata,segments,query_cache?fields=terms_v2")
    p = s["indices"][IDX]["primaries"]
    fd = p.get("fielddata", {})
    seg = p.get("segments", {})
    return {"fielddata_memory_bytes": fd.get("memory_size_in_bytes"),
            "fielddata_terms_v2_bytes": (fd.get("fields", {}) or {}).get("terms_v2", {}).get("memory_size_in_bytes"),
            "segments_memory_bytes": seg.get("memory_in_bytes"),
            "terms_memory_bytes": seg.get("terms_memory_in_bytes"),
            "query_cache_bytes": p.get("query_cache", {}).get("memory_size_in_bytes")}


def clear_caches():
    es("POST", f"/{IDX}/_cache/clear?fielddata=true&query=true&request=true", timeout=600)


def run_search(query, aggs):
    body = {"size": 0, "track_total_hits": True, "query": query, "aggs": aggs}
    t0 = time.perf_counter()
    r = es("POST", f"/{IDX}/_search?request_cache=false", body, timeout=900)
    wall = (time.perf_counter() - t0) * 1000
    agg = r["aggregations"]
    node = agg["t"] if "t" in agg else agg["s"]["t"]
    top = [[b["key"], b["doc_count"]] for b in node["buckets"]]
    sampled = agg["s"]["doc_count"] if "s" in agg else None
    return {"took": r["took"], "wall_ms": round(wall, 1), "hits": r["hits"]["total"]["value"], "top": top,
            "sampled_docs": sampled, "err_bound": node.get("doc_count_error_upper_bound")}


def query_battery(stage_dir_tag):
    out = {}
    for sname, q in SCOPES.items():
        out[sname] = {}
        exact_top = None
        for vname, aggs in VARIANTS.items():
            cold, hot, last = [], [], None
            for _ in range(5):
                clear_caches()
                last = run_search(q, aggs)
                cold.append(last["took"])
            run_search(q, aggs)  # 熱身
            for _ in range(5):
                h = run_search(q, aggs)
                hot.append(h["took"])
            rec = {"hits": last["hits"], "cold_took_ms": cold, "cold_median_ms": statistics.median(cold),
                   "hot_took_ms": hot, "hot_median_ms": statistics.median(hot),
                   "top50": last["top"][:50], "sampled_docs": last["sampled_docs"]}
            if vname == "exact":
                exact_top = [k for k, _ in last["top"][:50]]
            else:
                ks = [k for k, _ in last["top"][:50]]
                rec["overlap_top50_vs_exact"] = len(set(ks) & set(exact_top or []))
                rec["top10_same_order"] = ks[:10] == (exact_top or [])[:10]
            out[sname][vname] = rec
            print(f"  [{stage_dir_tag}] {sname:24s} {vname:15s} hits={rec['hits']:>12,} cold_med={rec['cold_median_ms']:>8.0f} "
                  f"hot_med={rec['hot_median_ms']:>8.0f}", flush=True)
        write_progress(state="querying", stage=stage_dir_tag, scope=sname)
    return out


def main():
    corpus = Corpus(BASE / "data" / "sources.pkl")
    print(f"corpus sources={corpus.S:,} dict_v1={corpus.dict_v1} dict_v2={corpus.dict_v2}", flush=True)
    # 關鍵字確認
    kid = corpus.vocab.index(KW) if KW in corpus.vocab else None
    kw_in_sources = sum(1 for ids in corpus.V2 if kid is not None and kid in ids)
    print(f"keyword {KW} in v2 vocab={kid is not None} sources_with_kw={kw_in_sources}", flush=True)

    es("GET", "/")  # 健康檢查
    if es("GET", "/_cluster/health")["status"] not in ("green", "yellow"):
        raise RuntimeError("cluster not healthy")
    try:
        es("HEAD", f"/{IDX}")
        existed = True
    except RuntimeError:
        existed = False
    # 只允許 scale_pilot_ 開頭的新索引
    assert IDX.startswith("scale_pilot_"), IDX
    if not existed:
        es("PUT", f"/{IDX}", MAPPING)
    print(f"index {IDX} ready (existed={existed})", flush=True)

    summary = []
    loaded = 0
    try:
        existing = es("GET", f"/{IDX}/_count")["count"]
        loaded = existing
    except RuntimeError:
        loaded = 0
    for target in TARGETS:
        if target <= loaded:
            continue
        check_stop(f"開始 stage {target}")
        t_stage = time.time()
        pool = ThreadPoolExecutor(max_workers=3)
        pending = set()
        lo = loaded
        t_load = time.time()
        bulk_ok = 0
        while lo < target:
            if lo // 100000 != (lo - BATCH) // 100000 or lo == loaded:
                check_stop(f"灌入中 {lo}")
            hi = min(lo + BATCH, target)
            payload = corpus.make_payload(lo, hi)
            pending.add(pool.submit(post_bulk, payload, hi - lo))
            lo = hi
            while len(pending) >= 3:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for f in done:
                    bulk_ok += f.result()
            if (lo // 100000) % 10 == 0:
                rate = (lo - loaded) / max(time.time() - t_load, 1e-6)
                write_progress(state="loading", target=target, loaded=lo, docs_per_sec=round(rate, 1),
                               index=IDX)
        for f in pending:
            bulk_ok += f.result()
        pool.shutdown(wait=True)
        load_secs = time.time() - t_load
        loaded = target
        es("POST", f"/{IDX}/_refresh", timeout=3600)
        pre = index_stats()
        jvm_pre = jvm_stats()
        fd_pre = fielddata_stats()
        print(f"stage {target:,}: loaded in {load_secs:.0f}s ({(target - (target - (bulk_ok))) / load_secs:,.0f} docs/s), "
              f"store={pre['store_bytes_primaries'] / 2 ** 30:.2f} GiB segs={pre['segments']}", flush=True)
        stage = {"target_docs": target, "docs_in_index": pre["docs"],
                 "load": {"seconds": round(load_secs, 1), "docs_per_sec": round(bulk_ok / load_secs, 1),
                          "docs_loaded_this_stage": bulk_ok},
                 "before_merge": {"index": pre, "jvm": jvm_pre, "fielddata": fd_pre,
                                  "bytes_per_doc": round(pre["store_bytes_primaries"] / pre["docs"], 2)},
                 "c_free_gib_before_merge": round(c_free_gib(), 2)}
        write_progress(state="pre_merge_stats_done", target=target, loaded=loaded, index=IDX)
        # force merge 前檢查磁碟（merge 會暫時多佔一份索引大小）
        need_gib = pre["store_bytes_primaries"] / 2 ** 30 * 1.2
        if c_free_gib() - need_gib < STOP_GIB:
            stage["force_merge"] = {"skipped": True, "reason": f"C: 剩 {c_free_gib():.1f} GiB，merge 需 ~{need_gib:.1f} GiB，低於安全下限"}
            print("  force_merge skipped (disk guard)", flush=True)
        else:
            t_m = time.time()
            es("POST", f"/{IDX}/_forcemerge?max_num_segments=1", timeout=21600)
            es("POST", f"/{IDX}/_refresh", timeout=3600)
            post = index_stats()
            stage["force_merge"] = {"skipped": False, "seconds": round(time.time() - t_m, 1),
                                    "index": post, "bytes_per_doc": round(post["store_bytes_primaries"] / post["docs"], 2)}
            print(f"  force_merge {stage['force_merge']['seconds']}s bytes/doc={stage['force_merge']['bytes_per_doc']}", flush=True)
        stage["jvm_after_merge"] = jvm_stats()
        stage["fielddata_after_merge"] = fielddata_stats()
        check_stop(f"查詢前 {target}")
        write_progress(state="querying", target=target, loaded=loaded, index=IDX)
        stage["queries"] = query_battery(str(target))
        stage["fielddata_after_queries"] = fielddata_stats()
        stage["jvm_after_queries"] = jvm_stats()
        stage["stage_seconds_total"] = round(time.time() - t_stage, 1)
        (OUT / f"stage_{target}.json").write_text(json.dumps(stage, ensure_ascii=False, indent=1), encoding="utf-8")
        summary.append({"target": target, "docs": pre["docs"], "load_docs_per_sec": stage["load"]["docs_per_sec"],
                        "bytes_per_doc_before_merge": stage["before_merge"]["bytes_per_doc"],
                        "bytes_per_doc_after_merge": (stage.get("force_merge") or {}).get("bytes_per_doc")})
        (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        write_progress(state="stage_done", target=target, loaded=loaded, index=IDX, summary=summary[-1])
        print(f"stage {target:,} done", flush=True)
    write_progress(state="finished", loaded=loaded, index=IDX, summary=summary)


if __name__ == "__main__":
    try:
        main()
    except Stop as e:
        write_progress(state="stopped", reason=str(e))
        print("STOPPED:", e, flush=True)
        sys.exit(0)
    except Exception as e:  # 記錄後再丟出
        write_progress(state="error", reason=repr(e))
        raise
