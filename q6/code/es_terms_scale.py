#!/usr/bin/env python3
"""Q6 第 2 點追問：命中幾百萬篇時，文字雲的 terms 聚合還能不能在 3 分鐘內回來？

真實環境（q6/realenv）只有 42.5 萬篇。這支腳本把同一份 PTT 問答語料用新流程斷詞後，複製成數百萬篇
（換月份、渠道），灌進一個真的 Elasticsearch 單節點，然後量 terms 聚合（算「幾篇提到這個詞」）在不同
命中數下的 took，順便量 shard_size、execution_hint 與 random_sampler 抽樣的差異與誤差。

用法：
    python -I es_terms_scale.py --es http://127.0.0.1:59200 --base-dict dict.txt.big \
        --corpus Gossiping-QA-Dataset.txt --stages 1000000 2500000 5000000 --out q6/data/es_terms_scale

每個 stage 灌到該篇數後 refresh，跑一輪查詢；最後寫 results.json 與 summary.md。
全部在本機 Docker，不碰 GCP。
"""
import argparse
import json
import platform
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import segmenter as sg  # noqa: E402

CHANNELS = [("ptt", 70), ("facebook", 10), ("instagram", 6), ("youtube", 5), ("media", 6), ("threads", 3)]
MONTHS = [f"{y}-{m:02d}" for y in range(2023, 2027) for m in range(1, 13) if (y, m) <= (2026, 10)]  # 46 個月
MAPPING = {
    "settings": {"number_of_shards": 3, "number_of_replicas": 0, "refresh_interval": "-1"},
    "mappings": {"dynamic": "strict", "properties": {
        "post_id": {"type": "keyword"}, "channel": {"type": "keyword"}, "month": {"type": "keyword"},
        "published_at": {"type": "date"},
        "terms_v2": {"type": "keyword", "eager_global_ordinals": True},
        "seg_ver_v2": {"type": "long"},
    }},
}


def dedupe(tokens):
    seen, out = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


class ES:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        self.s.trust_env = False  # 本機容器，不走代理

    def req(self, method, path, body=None, raw=None, timeout=600):
        kw = {"timeout": timeout}
        if raw is not None:
            kw["data"] = raw
            kw["headers"] = {"Content-Type": "application/x-ndjson"}
        else:
            kw["json"] = body
        r = self.s.request(method, f"{self.url}{path}", **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}

    def wait(self):
        for _ in range(120):
            try:
                h = self.req("GET", "/_cluster/health")
                if h["status"] in ("yellow", "green"):
                    return h
            except Exception:
                pass
            time.sleep(2)
        raise RuntimeError("ES 沒有起來")


def segment_corpus(corpus: Path, base_dict: Path, limit=None):
    seg = sg.Segmenters.build(base_dict=base_dict)
    docs, chars = [], 0
    t0 = time.perf_counter()
    with open(corpus, encoding="utf-8") as fp:
        for n, line in enumerate(fp):
            if limit and n >= limit:
                break
            text = line.rstrip("\n").replace("\t", " ")
            chars += len(text)
            docs.append(dedupe(sg.segment_new(seg, text)))
    secs = time.perf_counter() - t0
    return docs, {"posts": len(docs), "chars": chars, "seconds": round(secs, 1),
                  "chars_per_sec": round(chars / secs), "avg_terms": round(sum(map(len, docs)) / len(docs), 2),
                  "distinct_terms": len({t for d in docs for t in d}), "dict_version": seg.dict_version,
                  "base_dict": seg.base_dict, "seg_build": seg.seg_build}


def gen_docs(seg_docs, start, stop, seed=20261031):
    rng = random.Random(seed + start)
    chan_pool = [c for c, w in CHANNELS for _ in range(w)]
    n_src = len(seg_docs)
    for i in range(start, stop):
        month = MONTHS[i % len(MONTHS)]
        y, m = int(month[:4]), int(month[5:7])
        day = rng.randint(1, 28)
        ts = datetime(y, m, day, rng.randint(0, 23), rng.randint(0, 59), tzinfo=timezone.utc)
        yield {"post_id": f"p{i}", "channel": chan_pool[rng.randrange(len(chan_pool))], "month": month,
               "published_at": ts.isoformat(timespec="seconds"), "terms_v2": seg_docs[i % n_src],
               "seg_ver_v2": 22026103101}


def bulk_load(es, index, seg_docs, start, stop, batch=5000, workers=3):
    def send(lo, hi):
        lines = []
        for d in gen_docs(seg_docs, lo, hi):
            lines.append(json.dumps({"index": {"_index": index, "_id": d["post_id"]}}))
            lines.append(json.dumps(d, ensure_ascii=False))
        r = es.req("POST", "/_bulk?filter_path=errors,took", raw=("\n".join(lines) + "\n").encode("utf-8"))
        if r.get("errors"):
            raise RuntimeError("bulk 有錯誤")
        return hi - lo

    t0 = time.perf_counter()
    ranges = [(lo, min(lo + batch, stop)) for lo in range(start, stop, batch)]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n in ex.map(lambda r: send(*r), ranges):
            done += n
            if done % 500000 < batch:
                print(f"  indexed {start + done:,} docs, {done / (time.perf_counter() - t0):,.0f} docs/s", flush=True)
    secs = time.perf_counter() - t0
    return {"docs": stop - start, "seconds": round(secs, 1), "docs_per_sec": round((stop - start) / secs)}


def month_range(months_back):
    """最近 N 個月：從 2026-10 往回數。"""
    end = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    start_month = MONTHS[-months_back]
    return {"range": {"published_at": {"gte": f"{start_month}-01T00:00:00Z", "lte": end.isoformat()}}}


def scopes():
    return {
        "all": {"match_all": {}},
        "last_12_months": {"bool": {"filter": [month_range(12)]}},
        "last_1_month": {"bool": {"filter": [month_range(1)]}},
        "keyword_颱風_all": {"bool": {"filter": [{"term": {"terms_v2": "颱風"}}]}},
        "channel_ptt_all": {"bool": {"filter": [{"term": {"channel": "ptt"}}]}},
        "keyword_颱風_last_12_months": {"bool": {"filter": [{"term": {"terms_v2": "颱風"}}, month_range(12)]}},
    }


def variants():
    base = {"field": "terms_v2", "size": 50}
    return {
        "exact_default": {"cloud": {"terms": dict(base)}},
        "exact_shard_size_500": {"cloud": {"terms": dict(base, shard_size=500)}},
        "exact_hint_map": {"cloud": {"terms": dict(base, execution_hint="map")}},
        "sampler_p0.1": {"sample": {"random_sampler": {"probability": 0.1, "seed": 7},
                                    "aggs": {"cloud": {"terms": dict(base)}}}},
        "sampler_p0.01": {"sample": {"random_sampler": {"probability": 0.01, "seed": 7},
                                     "aggs": {"cloud": {"terms": dict(base)}}}},
    }


def run_query(es, index, query, aggs, repeats):
    tooks, resp = [], None
    for _ in range(repeats):
        body = {"size": 0, "track_total_hits": True, "query": query, "aggs": aggs}
        t0 = time.perf_counter()
        resp = es.req("POST", f"/{index}/_search?request_cache=false", body)
        wall = (time.perf_counter() - t0) * 1000
        tooks.append((resp["took"], wall))
    agg = resp["aggregations"]
    cloud = agg["cloud"] if "cloud" in agg else agg["sample"]["cloud"]
    out = {
        "hits": resp["hits"]["total"]["value"],
        "took_ms_median": statistics.median(t for t, _ in tooks),
        "took_ms_max": max(t for t, _ in tooks),
        "wall_ms_median": round(statistics.median(w for _, w in tooks), 1),
        "top": [[b["key"], b["doc_count"]] for b in cloud["buckets"]],
        "doc_count_error_upper_bound": cloud.get("doc_count_error_upper_bound"),
        "sum_other_doc_count": cloud.get("sum_other_doc_count"),
    }
    if "sample" in agg:
        out["sampled_docs"] = agg["sample"]["doc_count"]
    return out


def compare_top(exact, other, k=50):
    a = [t for t, _ in exact[:k]]
    b = [t for t, _ in other[:k]]
    sa, sb = set(a), set(b)
    top10 = [t for t, _ in exact[:10]]
    return {"overlap_top50": len(sa & sb), "top10_in_other": sum(t in sb for t in top10),
            "same_order_top10": a[:10] == b[:10]}


def index_stats(es, index):
    st = es.req("GET", f"/{index}/_stats/store,docs,segments")["indices"][index]["primaries"]
    return {"docs": st["docs"]["count"], "store_bytes": st["store"]["size_in_bytes"], "segments": st["segments"]["count"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--es", default="http://127.0.0.1:59200")
    ap.add_argument("--index", default="q6_scale")
    ap.add_argument("--base-dict", type=Path, required=True)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--stages", type=int, nargs="+", default=[1_000_000, 2_500_000, 5_000_000])
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None, help="只讀語料前 N 行（測試用）")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    es = ES(a.es)
    health = es.wait()
    info = es.req("GET", "/")
    result = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "python": platform.python_version(),
              "es_version": info["version"]["number"], "cluster_health": health["status"], "params": {k: str(v) for k, v in vars(a).items()},
              "stages": []}
    print("segmenting corpus…", flush=True)
    seg_docs, seg_info = segment_corpus(a.corpus, a.base_dict, a.limit)
    result["segmentation"] = seg_info
    print(json.dumps(seg_info, ensure_ascii=False), flush=True)
    try:
        es.req("DELETE", f"/{a.index}")
    except RuntimeError:
        pass
    es.req("PUT", f"/{a.index}", MAPPING)
    loaded = 0
    for target in a.stages:
        print(f"stage {target:,}: loading…", flush=True)
        load = bulk_load(es, a.index, seg_docs, loaded, target)
        loaded = target
        es.req("POST", f"/{a.index}/_refresh")
        time.sleep(5)
        stage = {"target_docs": target, "load": load, "index": index_stats(es, a.index), "queries": {}}
        for sname, q in scopes().items():
            stage["queries"][sname] = {}
            exact = None
            for vname, aggs in variants().items():
                r = run_query(es, a.index, q, aggs, a.repeats)
                if vname == "exact_default":
                    exact = r["top"]
                else:
                    r["vs_exact"] = compare_top(exact, r["top"])
                stage["queries"][sname][vname] = r
                print(f"  {sname:28s} {vname:22s} hits={r['hits']:>10,} took p50={r['took_ms_median']:>8.0f} ms max={r['took_ms_max']:>8.0f} ms", flush=True)
        result["stages"].append(stage)
        (a.out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (a.out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (a.out / "summary.md").write_text(render(result), encoding="utf-8")
    print("done", flush=True)


def render(result):
    lines = [f"# terms 聚合延遲 vs 命中篇數（ES {result['es_version']}，單節點、3 分片）", "",
             f"語料：{result['segmentation']['posts']:,} 篇 PTT 問答用新流程斷詞（{result['segmentation']['base_dict']}，"
             f"平均 {result['segmentation']['avg_terms']} 個詞／篇、{result['segmentation']['distinct_terms']:,} 個不同詞），"
             f"複製到各 stage 的篇數（換月份、渠道）。每格是 {result['params']['repeats']} 次的 took 中位數（毫秒）。", ""]
    for st in result["stages"]:
        idx = st["index"]
        lines += [f"## {st['target_docs']:,} 篇（索引 {idx['store_bytes'] / 1e9:.2f} GB、{idx['segments']} 個 segment；灌入 {st['load']['docs_per_sec']:,} 篇/秒）", "",
                  "| 範圍 | 命中 | 精確（預設） | shard_size 500 | hint map | 抽樣 10% | 抽樣 1% | 抽樣 10% 前 50 重疊 | 抽樣 1% 前 50 重疊 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for sname, qs in st["queries"].items():
            e = qs["exact_default"]
            lines.append(f"| {sname} | {e['hits']:,} | {e['took_ms_median']:,.0f} | {qs['exact_shard_size_500']['took_ms_median']:,.0f} | "
                         f"{qs['exact_hint_map']['took_ms_median']:,.0f} | {qs['sampler_p0.1']['took_ms_median']:,.0f} | {qs['sampler_p0.01']['took_ms_median']:,.0f} | "
                         f"{qs['sampler_p0.1']['vs_exact']['overlap_top50']}/50 | {qs['sampler_p0.01']['vs_exact']['overlap_top50']}/50 |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
