"""Q6 的 ES 實驗（全部打真的 ES 9.5.5 單節點，結果寫 results/es/*.json）。

analyzers  ：同幾句話，ES 內建／外掛分析器 vs jieba 舊流程 v1／新流程 v2 怎麼切
aggs       ：terms 聚合的語意（doc_count 是篇數）、shard_size 與誤差上界、query 與 post_filter、_reindex 不必重算
guard      ：seg_ver 只能前進（舊版晚到、同版重送、force、parser 部分更新、還沒有文件就先寫斷詞、多寫者同時寫）
latency    ：文字雲查詢延遲（全期／一年／一個月／關鍵字 × v1／v2）
pg         ：同一份 v2 詞表放 PostgreSQL（GIN）算前 50 名，與 ES 結果、耗時對照
用法：DICT_BASE_URL=http://localhost:8081 python es_experiments.py <實驗名>...
"""
import json
import statistics
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent.parent / "code"), str(HERE.parent / "seg")]
ES = "http://localhost:9200"
OUT = HERE.parent / "results" / "es"
S = requests.Session()


def es(method, path, body=None, ok=(200, 201)):
    r = S.request(method, ES + path, json=body, timeout=600)
    if r.status_code not in ok:
        raise RuntimeError(f"{method} {path} -> {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


def save(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(name, json.dumps(obj, ensure_ascii=False)[:1500])


TEXTS = [
    "高雄市長陳其邁出席鳳山區活動",
    "短網址 https://reurl.cc/abc123 跟 ppt.cc/fXyz 點這裡",
    "客服電話 0912-345-678 或 (07)331-1234",
    "我在好市多買了台積電股票",
    "肥宅在新北投排隊買全聯福利中心",
    "[問卦] 有沒有臺北市跟台北市的八卦？",
]


def analyzers():
    from qseg import dictionary
    from qseg.pipeline import segment
    v1 = dictionary.load("qsearch-q6-2026.10.30.1", "v1")
    cur = es("GET", "/posts/_mapping")  # 確認索引存在
    v2 = dictionary.load(requests.get("http://localhost:8081/current/manifest.json").json()["dict_version"], "v2")
    rows = []
    for t in TEXTS:
        row = {"text": t}
        for name, body in [("standard", {"analyzer": "standard"}), ("smartcn", {"analyzer": "smartcn"}),
                           ("icu_analyzer", {"analyzer": "icu_analyzer"}), ("ik_smart", {"analyzer": "ik_smart"}),
                           ("ik_max_word", {"analyzer": "ik_max_word"})]:
            row[name] = [x["token"] for x in es("POST", "/_analyze", {**body, "text": t})["tokens"]]
        row["cjk_bigram (content.bigram)"] = [x["token"] for x in es("POST", "/posts/_analyze", {"analyzer": "cjk_bigram_idx", "text": t})["tokens"]]
        row["ik_search (查詢端＋同義詞)"] = [x["token"] for x in es("POST", "/posts/_analyze", {"analyzer": "ik_search", "text": t})["tokens"]]
        row["jieba v1 (舊流程)"] = segment(v1, "ptt", t)
        row[f"jieba v2 ({v2.version})"] = segment(v2, "ptt", t)
        rows.append(row)
    save("analyzers", {"dict_v2": v2.version, "rows": rows})


def aggs():
    res = {}
    # 1) doc_count 是篇數：同一篇裡同一個詞出現三次，只算一篇
    es("DELETE", "/agg-demo", ok=(200, 404))
    es("PUT", "/agg-demo", {"mappings": {"properties": {"terms": {"type": "keyword"}}}})
    es("POST", "/agg-demo/_bulk?refresh=true", None) if False else None
    body = "\n".join([json.dumps({"index": {"_id": "1"}}), json.dumps({"terms": ["颱風", "颱風", "颱風", "放假"]}),
                      json.dumps({"index": {"_id": "2"}}), json.dumps({"terms": ["颱風"]})]) + "\n"
    S.post(ES + "/agg-demo/_bulk?refresh=true", data=body.encode(), headers={"Content-Type": "application/x-ndjson"}).raise_for_status()
    b = es("POST", "/agg-demo/_search", {"size": 0, "aggs": {"t": {"terms": {"field": "terms"}}}})["aggregations"]["t"]["buckets"]
    res["doc_count_is_docs"] = {"docs": [["颱風", "颱風", "颱風", "放假"], ["颱風"]], "buckets": b}
    es("DELETE", "/agg-demo")
    # 2) shard_size 與誤差：預設 vs 放大，與逐詞精確篇數比
    exact = lambda term: es("POST", "/posts/_search?request_cache=false", {"size": 0, "track_total_hits": True,
                            "query": {"term": {"terms_v2": term}}})["hits"]["total"]["value"]
    out = {}
    for label, extra in [("size10_default_shard_size", {}), ("size10_shard_size_500", {"shard_size": 500})]:
        r = es("POST", "/posts/_search?request_cache=false", {"size": 0, "aggs": {"t": {"terms": {"field": "terms_v2", "size": 10, "show_term_doc_count_error": True, **extra}}}})
        a = r["aggregations"]["t"]
        out[label] = {"took_ms": r["took"], "doc_count_error_upper_bound": a["doc_count_error_upper_bound"],
                      "buckets": [{"term": x["key"], "count": x["doc_count"], "error": x.get("doc_count_error_upper_bound"), "exact": exact(x["key"])} for x in a["buckets"]]}
    res["shard_size"] = out
    # 3) query 範圍 vs post_filter：post_filter 不影響聚合（文字雲要用 query/filter 限範圍）
    q = {"bool": {"filter": [{"range": {"published_at": {"gte": "2026-10-01"}}}]}}
    a1 = es("POST", "/posts/_search?request_cache=false", {"size": 0, "query": q, "aggs": {"t": {"terms": {"field": "terms_v2", "size": 5}}}})
    a2 = es("POST", "/posts/_search?request_cache=false", {"size": 0, "post_filter": q, "aggs": {"t": {"terms": {"field": "terms_v2", "size": 5}}}})
    res["filter_vs_post_filter"] = {"query_filter": [(x["key"], x["doc_count"]) for x in a1["aggregations"]["t"]["buckets"]],
                                    "post_filter": [(x["key"], x["doc_count"]) for x in a2["aggregations"]["t"]["buckets"]]}
    # 4) _reindex：斷詞結果在 _source 裡，搬索引（改分片數、升級）不必重新斷詞
    es("DELETE", "/posts-reindex-demo", ok=(200, 404))
    m = es("GET", "/posts")["posts"]
    settings = {"number_of_shards": 1, "number_of_replicas": 0, "analysis": m["settings"]["index"]["analysis"]}
    es("PUT", "/posts-reindex-demo", {"settings": settings, "mappings": m["mappings"]})
    t0 = time.time()
    r = es("POST", "/_reindex?refresh=true", {"source": {"index": "posts", "query": {"term": {"month": "2026-10"}}}, "dest": {"index": "posts-reindex-demo"}})
    agg = lambda idx: [(x["key"], x["doc_count"]) for x in es("POST", f"/{idx}/_search?request_cache=false", {"size": 0, "query": {"term": {"month": "2026-10"}},
                       "aggs": {"t": {"terms": {"field": "terms_v2", "size": 50}}}})["aggregations"]["t"]["buckets"]]
    res["reindex"] = {"copied": r["created"], "seconds": round(time.time() - t0, 2), "top50_identical": agg("posts") == agg("posts-reindex-demo")}
    es("DELETE", "/posts-reindex-demo")
    save("aggs", res)


def guard():
    from qseg.es import ES as Client, GUARD_SCRIPT
    idx = "guard-demo"
    es("DELETE", f"/{idx}", ok=(200, 404))
    m = es("GET", "/posts")["posts"]
    es("PUT", f"/{idx}", {"settings": {"number_of_shards": 1, "number_of_replicas": 0, "analysis": m["settings"]["index"]["analysis"]}, "mappings": m["mappings"]})
    c = Client(ES, idx)
    c.put_guard_script()
    base = {"post_id": "g1", "channel": "ptt", "content": "肥宅在新北投", "month": "2026-10", "published_at": "2026-10-30T12:00:00+08:00"}
    seg = lambda ver, terms: {"terms_v2": terms, "seg_ver_v2": ver, "dict_version_v2": f"v{ver}", "seg_build_v2": "test", "seg_at_v2": "2026-10-30T00:00:00Z"}
    steps = []

    def step(name, fn):
        r = fn()
        doc = es("GET", f"/{idx}/_doc/g1", ok=(200, 404)).get("_source", {})
        steps.append({"step": name, "result": {k: v for k, v in r.items() if k in ("created", "updated", "noop", "errors")} if isinstance(r, dict) else r,
                      "seg_ver_v2": doc.get("seg_ver_v2"), "terms_v2": doc.get("terms_v2"), "content": doc.get("content"), "channel": doc.get("channel")})

    step("斷詞先到（文件還不存在）：scripted upsert 建出文件", lambda: c.bulk_seg([("g1", base, seg(22026103005, ["肥宅", "新北投"]), "v2")], refresh=True))
    step("parser 晚到：部分更新本體欄位，斷詞欄位不動", lambda: (c.upsert_base([("g1", {**base, "content": "肥宅在新北投（更新）"})], refresh=True), {"updated": 1})[1])
    step("同一版重送：noop", lambda: c.bulk_seg([("g1", base, seg(22026103005, ["肥宅", "新北投"]), "v2")], refresh=True))
    step("舊版晚到（r4）：noop，不倒退", lambda: c.bulk_seg([("g1", base, seg(22026103004, ["肥", "宅", "新北投"]), "v2")], refresh=True))
    step("新版（r6）：更新", lambda: c.bulk_seg([("g1", base, seg(22026103006, ["肥宅", "新北投", "r6"]), "v2")], refresh=True))
    step("force 同版（修程式重跑）：覆寫", lambda: c.bulk_seg([("g1", base, seg(22026103006, ["肥宅", "新北投", "r6-fixed"]), "v2")], refresh=True, force=True))
    step("force 舊版：仍然 noop", lambda: c.bulk_seg([("g1", base, seg(22026103005, ["舊"]), "v2")], refresh=True, force=True))
    # 多寫者同時寫同一篇：版本亂序，最後一定是最大版本
    vers = list(range(22026103001, 22026103041))
    import random
    random.Random(7).shuffle(vers)
    errs = []

    def writer(chunk):
        cc = Client(ES, idx)
        for v in chunk:
            try:
                cc.bulk_seg([("g2", base, seg(v, [str(v)]), "v2")])
            except Exception as e:
                errs.append(str(e)[:100])
    ths = [threading.Thread(target=writer, args=(vers[i::8],)) for i in range(8)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    es("POST", f"/{idx}/_refresh")
    g2 = es("GET", f"/{idx}/_doc/g2")["_source"]
    res = {"steps": steps, "concurrent": {"writers": 8, "versions_written_shuffled": len(vers), "final_seg_ver": g2["seg_ver_v2"],
                                            "expected": max(vers), "ok": g2["seg_ver_v2"] == max(vers), "errors": errs[:5]},
           "script": GUARD_SCRIPT.strip()}
    es("DELETE", f"/{idx}")
    save("guard", res)


def latency():
    scopes = {"全期": {}, "近一年": {"date_from": "2025-10-30"}, "近一個月": {"date_from": "2026-10-01"}, "關鍵字「颱風」": {"q": "颱風"}}
    out = {}
    for seg in ("v1", "v2"):
        for name, sc in scopes.items():
            ms, took = [], []
            for _ in range(20):
                qs = urllib.parse.urlencode({"seg": seg, "cache": 0, "size": 100, **sc})
                t0 = time.perf_counter()
                r = S.get(f"http://localhost:8080/api/wordcloud?{qs}").json()
                ms.append((time.perf_counter() - t0) * 1000)
                took.append(r["es_took_ms"])
            ms.sort()
            out[f"{seg} {name}"] = {"docs": r["total_docs"], "api_p50_ms": round(statistics.median(ms), 1), "api_p95_ms": round(ms[18], 1),
                                   "es_took_p50_ms": statistics.median(took), "cached_repeat_ms": None}
            qs = urllib.parse.urlencode({"seg": seg, "cache": 1, "size": 100, **sc})
            S.get(f"http://localhost:8080/api/wordcloud?{qs}")
            t0 = time.perf_counter()
            S.get(f"http://localhost:8080/api/wordcloud?{qs}")
            out[f"{seg} {name}"]["cached_repeat_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    save("latency_by_scope", out)


def pg():
    import psycopg
    conn = psycopg.connect("postgresql://qsearch:qsearch@localhost:55433/qsearch", autocommit=True)
    conn.execute("TRUNCATE posts_terms")
    t0 = time.time()
    pit = es("POST", "/posts/_pit?keep_alive=2m")["id"]
    after, n = None, 0
    with conn.cursor() as cur, cur.copy("COPY posts_terms (post_id, channel, published_at, terms) FROM STDIN") as cp:
        while True:
            body = {"size": 5000, "pit": {"id": pit, "keep_alive": "2m"}, "sort": [{"_shard_doc": "asc"}],
                    "_source": ["channel", "published_at", "terms_v2"], "query": {"exists": {"field": "terms_v2"}}}
            if after:
                body["search_after"] = after
            hits = es("POST", "/_search", body)["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                s = h["_source"]
                cp.write_row((h["_id"], s["channel"], s.get("published_at"), s.get("terms_v2") or []))
            n += len(hits)
            after = hits[-1]["sort"]
    es("DELETE", "/_pit", {"id": pit})
    load_s = round(time.time() - t0, 1)
    conn.execute("ANALYZE posts_terms")
    size = conn.execute("SELECT pg_size_pretty(pg_total_relation_size('posts_terms'))").fetchone()[0]
    res = {"rows": n, "load_seconds_from_es": load_s, "table_size_with_gin": size, "scopes": {}}
    for name, where, flt in [("全期", "", []), ("近一個月", "WHERE published_at >= '2026-10-01'", [{"range": {"published_at": {"gte": "2026-10-01"}}}])]:
        sql = f"SELECT t, count(*) FROM posts_terms, unnest(terms) t {where} GROUP BY t ORDER BY 2 DESC, 1 LIMIT 50"
        times = []
        for _ in range(3):
            t1 = time.perf_counter()
            rows = conn.execute(sql).fetchall()
            times.append((time.perf_counter() - t1) * 1000)
        r = es("POST", "/posts/_search?request_cache=false", {"size": 0, "query": {"bool": {"filter": flt}},
               "aggs": {"t": {"terms": {"field": "terms_v2", "size": 50, "shard_size": 2000}}}})
        eb = [(x["key"], x["doc_count"]) for x in r["aggregations"]["t"]["buckets"]]
        pg_counts = dict(rows)
        res["scopes"][name] = {"pg_ms_min": round(min(times), 1), "es_took_ms": r["took"],
                               "same_counts_for_es_top50": all(pg_counts.get(k) == v for k, v in eb),
                               "same_top50_set": {k for k, _ in eb} == set(pg_counts),
                               "top5_pg": rows[:5], "top5_es": eb[:5]}
        t1 = time.perf_counter()
        hit = conn.execute("SELECT count(*) FROM posts_terms WHERE terms @> ARRAY['颱風']").fetchone()[0]
        res["gin_contains_颱風"] = {"count": hit, "ms": round((time.perf_counter() - t1) * 1000, 1),
                                   "es_count": es("POST", "/posts/_count", {"query": {"term": {"terms_v2": "颱風"}}})["count"]}
    save("pg_vs_es", res)


if __name__ == "__main__":
    for name in sys.argv[1:]:
        globals()[name]()
