"""QSearch 文字雲 API：讀 ES 的 terms 聚合，seg=v1|v2 切換新舊詞表，給 PM 並排比對與客戶文字雲。

- 一次請求一個 ES 搜尋：query 決定文章範圍，terms 聚合算「範圍內有幾篇文章提到這個詞」
- 軟停用詞（問卦、有沒有…）在查詢時用 terms 聚合的 exclude 處理，改清單不必重跑歷史
- 變體合併（臺北市→台北市）也在查詢時合併 bucket（篇數相加是上限：同一篇兩種寫法都有會算兩次）
- 快取鍵＝（查詢, 範圍, 渠道, seg, 目前最大 seg_ver）：字典發版、seg_ver 前進，快取自然失效
- 每次請求寫一行結構化 JSON（event=wordcloud_query, duration_ms, es_call_ms, seg_ver…），
  Cloud Run 會收進 Cloud Logging，可建日誌型指標「超過 180 秒的查詢數」；本機另有 Prometheus 指標
"""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

ES_URL = os.environ.get("ES_URL", "http://es:9200").rstrip("/")
INDEX = os.environ.get("ES_INDEX", "posts")
DICT_BASE_URL = os.environ.get("DICT_BASE_URL", "http://dictsrv").rstrip("/")
SLA_MS = 180_000
CHANNELS = ["facebook", "instagram", "youtube", "ptt", "media", "threads"]
HARD_STOP = set("的 了 在 是 和 與 及 或 也 都 就 而 等 啊 吧 呢 嗎 喔 哦 耶 呀 嘛 這 那 個 我 你 他 她 它 我們 你們 他們 有 會 很 被 把 給".split())
NUM_RE = re.compile(r"^[\d\W_]*\d[\d\W_]*$")
SYMBOL_RE = re.compile(r"^[\W_]+$")
ASCII_MIX_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{4,}$|^(?:http|https|www|com|cc|tw|ly|html|php)$", re.I)

DUR = Histogram("wordcloud_duration_seconds", "API 總耗時", ["seg", "cache"],
                buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 180, 300))
ES_CALL = Histogram("wordcloud_es_call_seconds", "API 呼叫 ES 的往返耗時", ["seg"],
                    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 180))
OVER = Counter("wordcloud_over_180s_total", "超過 180 秒的文字雲查詢（對應 Cloud Logging 日誌型指標 wordcloud_over_180s）", ["seg"])
ERR = Counter("wordcloud_errors_total", "文字雲查詢錯誤", ["seg"])

app = FastAPI(title="QSearch 文字雲 API（Q6 真實環境）")
S = requests.Session()
CACHE: dict = {}
_DICT = {"at": 0.0, "v": None}


def log(**rec):
    rec.setdefault("severity", "INFO")
    rec["time"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def es(method, path, body=None):
    t0 = time.perf_counter()
    r = S.request(method, f"{ES_URL}{path}", json=body, timeout=SLA_MS / 1000 + 30)
    ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    return r.json(), ms


def dict_overlay():
    """軟停用詞與合併規則：從字典 artifact 的 current 讀（每 30 秒更新一次），與斷詞服務同一份來源。"""
    if time.time() - _DICT["at"] > 30 or _DICT["v"] is None:
        try:
            man = S.get(f"{DICT_BASE_URL}/current/manifest.json", timeout=5).json()
            # 一定要自己用 UTF-8 解：nginx 回 text/plain 沒帶 charset，requests 的 .text 會當 Latin-1，中文全變亂碼、軟停用詞等於沒套
            txt = lambda name: S.get(f"{DICT_BASE_URL}/current/{name}", timeout=5).content.decode("utf-8")
            soft = [w for w in txt("soft_stopwords.txt").splitlines() if w.strip()]
            merges = dict(l.split("\t") for l in txt("merges.tsv").splitlines() if "\t" in l)
            _DICT["v"] = {"dict_version": man["dict_version"], "soft": soft, "merges": merges}
        except Exception as e:
            _DICT["v"] = _DICT["v"] or {"dict_version": None, "soft": [], "merges": {}, "error": str(e)}
        _DICT["at"] = time.time()
    return _DICT["v"]


def scope_query(q, channels, date_from, date_to):
    flt = []
    if channels:
        flt.append({"terms": {"channel": channels}})
    if date_from or date_to:
        rng = {}
        if date_from:
            rng["gte"] = date_from
        if date_to:
            rng["lte"] = date_to
        flt.append({"range": {"published_at": rng}})
    must = [{"match": {"content": {"query": q, "operator": "and"}}}] if q else []
    return {"bool": {"filter": flt, "must": must}}


def noise_of(term: str) -> str:
    if term in HARD_STOP:
        return "stopword"
    if NUM_RE.match(term):
        return "number"
    if SYMBOL_RE.match(term):
        return "symbol"
    if ASCII_MIX_RE.match(term):
        return "url_or_code"
    if len(term) == 1:
        return "single_char"
    return ""


def wordcloud(seg, q, channels, date_from, date_to, size, soft, merge, shard_size=None, use_cache=True):
    rid = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    ov = dict_overlay()
    excl = ov["soft"] if (soft and seg == "v2") else []
    key = json.dumps([seg, q, channels, date_from, date_to, size, soft, merge, shard_size, ov["dict_version"]], ensure_ascii=False)
    hit = CACHE.get(key) if use_cache else None
    if hit and time.time() - hit["at"] < 300:
        out = dict(hit["out"], cache="hit", request_id=rid)
        out["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        DUR.labels(seg, "hit").observe(out["duration_ms"] / 1000)
        log(event="wordcloud_query", request_id=rid, seg_ver=seg, cache="hit", duration_ms=out["duration_ms"], es_call_ms=0, status="ok")
        return out
    terms_agg = {"field": f"terms_{seg}", "size": size}
    if excl:
        terms_agg["exclude"] = excl
    if shard_size:
        terms_agg["shard_size"] = shard_size
    body = {
        "size": 0, "track_total_hits": True,
        "query": scope_query(q, channels, date_from, date_to),
        "aggs": {
            "cloud": {"terms": terms_agg},
            "versions": {"terms": {"field": f"dict_version_{seg}", "size": 10, "missing": "(未斷詞)"}},
            "channels": {"terms": {"field": "channel", "size": 10}},
        },
    }
    try:
        res, es_ms = es("POST", f"/{INDEX}/_search?request_cache=false", body)
    except Exception as e:
        ERR.labels(seg).inc()
        log(severity="ERROR", event="wordcloud_query", request_id=rid, seg_ver=seg, status="error", error=str(e)[:300])
        raise
    buckets = [{"term": b["key"], "count": b["doc_count"], "noise": noise_of(b["key"])} for b in res["aggregations"]["cloud"]["buckets"]]
    if merge and seg == "v2" and ov["merges"]:
        acc = {}
        for b in buckets:
            t = ov["merges"].get(b["term"], b["term"])
            if t in acc:
                acc[t]["count"] += b["count"]
                acc[t]["merged_from"] = acc[t].get("merged_from", []) + [b["term"]]
            else:
                acc[t] = dict(b, term=t, **({"merged_from": [b["term"]]} if t != b["term"] else {}))
        buckets = sorted(acc.values(), key=lambda x: -x["count"])
    dur = (time.perf_counter() - t0) * 1000
    out = {
        "request_id": rid, "seg": seg, "cache": "miss",
        "total_docs": res["hits"]["total"]["value"],
        "es_took_ms": res["took"], "es_call_ms": round(es_ms, 1), "duration_ms": round(dur, 1),
        "doc_count_error_upper_bound": res["aggregations"]["cloud"].get("doc_count_error_upper_bound"),
        "sum_other_doc_count": res["aggregations"]["cloud"].get("sum_other_doc_count"),
        "dict_versions": {b["key"]: b["doc_count"] for b in res["aggregations"]["versions"]["buckets"]},
        "channels": {b["key"]: b["doc_count"] for b in res["aggregations"]["channels"]["buckets"]},
        "excluded_soft_stopwords": excl, "overlay_dict_version": ov["dict_version"],
        "buckets": buckets,
        "noise_in_top": {k: sum(1 for b in buckets if b["noise"] == k) for k in ("stopword", "symbol", "number", "url_or_code", "single_char")},
    }
    CACHE[key] = {"at": time.time(), "out": out}
    DUR.labels(seg, "miss").observe(dur / 1000)
    ES_CALL.labels(seg).observe(es_ms / 1000)
    sev = "INFO"
    if dur > SLA_MS:
        OVER.labels(seg).inc()
        sev = "WARNING"
    log(severity=sev, event="wordcloud_query", request_id=rid, seg_ver=seg, cache="miss", q=q, channels=channels,
        total_docs=out["total_docs"], duration_ms=out["duration_ms"], es_call_ms=out["es_call_ms"], es_took_ms=res["took"], status="ok")
    return out


def _channels(ch: str):
    return [c for c in (ch or "").split(",") if c]


@app.get("/api/wordcloud")
def api_wordcloud(seg: str = "v2", q: str = "", channels: str = "", date_from: str = "", date_to: str = "",
                  size: int = Query(60, le=500), soft: int = 1, merge: int = 1, shard_size: int = 0, cache: int = 1):
    return wordcloud(seg, q, _channels(channels), date_from, date_to, size, bool(soft), bool(merge), shard_size or None, bool(cache))


@app.get("/api/compare")
def api_compare(q: str = "", channels: str = "", date_from: str = "", date_to: str = "", size: int = Query(60, le=300), soft: int = 1, merge: int = 1):
    ch = _channels(channels)
    a = wordcloud("v1", q, ch, date_from, date_to, size, False, False)
    b = wordcloud("v2", q, ch, date_from, date_to, size, bool(soft), bool(merge))
    ta = {x["term"]: (i + 1, x["count"]) for i, x in enumerate(a["buckets"])}
    tb = {x["term"]: (i + 1, x["count"]) for i, x in enumerate(b["buckets"])}
    rows = []
    for t in list(dict.fromkeys(list(tb) + list(ta))):
        rows.append({"term": t, "v1_rank": ta.get(t, (None, None))[0], "v1_count": ta.get(t, (None, None))[1],
                     "v2_rank": tb.get(t, (None, None))[0], "v2_count": tb.get(t, (None, None))[1], "noise": noise_of(t)})
    return {"v1": a, "v2": b, "diff": rows,
            "only_v1": [r for r in rows if r["v2_rank"] is None][:30],
            "only_v2": [r for r in rows if r["v1_rank"] is None][:30],
            "same_scope_docs": [a["total_docs"], b["total_docs"]]}


@app.get("/api/posts")
def api_posts(term: str, seg: str = "v2", q: str = "", channels: str = "", date_from: str = "", date_to: str = "", size: int = Query(20, le=100)):
    qry = scope_query(q, _channels(channels), date_from, date_to)
    qry["bool"]["filter"].append({"term": {f"terms_{seg}": term}})
    body = {"size": size, "track_total_hits": True,  # 預設只數到 10,000，PM 會看到「10,000 篇」以為就是全部
            "query": qry, "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
            "_source": ["post_id", "channel", "source", "url", "published_at", "content", "terms_v1", "terms_v2", "dict_version_v2", "seg_ver_v2"],
            "highlight": {"fields": {"content": {"fragment_size": 120, "number_of_fragments": 2,
                                                 "highlight_query": {"match_phrase": {"content.bigram": term}}},
                                     "content.bigram": {"fragment_size": 120, "number_of_fragments": 2}},
                          "pre_tags": ["<mark>"], "post_tags": ["</mark>"]}}
    res, ms = es("POST", f"/{INDEX}/_search", body)
    return {"term": term, "seg": seg, "total": res["hits"]["total"]["value"], "es_call_ms": round(ms, 1),
            "posts": [dict(h["_source"], highlight=(h.get("highlight", {}).get("content.bigram") or h.get("highlight", {}).get("content") or [])) for h in res["hits"]["hits"]]}


@app.get("/api/overview")
def api_overview():
    body = {"size": 0, "track_total_hits": True, "aggs": {
        "ch": {"terms": {"field": "channel", "size": 10}, "aggs": {
            "v1": {"filter": {"exists": {"field": "terms_v1"}}},
            "v2": {"filter": {"exists": {"field": "terms_v2"}}},
            "dv": {"terms": {"field": "dict_version_v2", "size": 5}},
            "latest": {"max": {"field": "published_at"}}, "earliest": {"min": {"field": "published_at"}}}},
        "dv": {"terms": {"field": "dict_version_v2", "size": 10}},
        "maxver": {"max": {"field": "seg_ver_v2"}}}}
    res, ms = es("POST", f"/{INDEX}/_search", body)
    info, _ = es("GET", "/")
    ag = res["aggregations"]
    return {"es_version": info["version"]["number"], "total": res["hits"]["total"]["value"], "es_call_ms": round(ms, 1),
            "overlay": dict_overlay(), "max_seg_ver_v2": ag["maxver"]["value"],
            "dict_versions": {b["key"]: b["doc_count"] for b in ag["dv"]["buckets"]},
            "channels": [{"channel": b["key"], "docs": b["doc_count"], "v1": b["v1"]["doc_count"], "v2": b["v2"]["doc_count"],
                          "dict_versions": {x["key"]: x["doc_count"] for x in b["dv"]["buckets"]},
                          "earliest": b["earliest"].get("value_as_string"), "latest": b["latest"].get("value_as_string")} for b in ag["ch"]["buckets"]]}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


WEB = Path(__file__).resolve().parent / "web"
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(str(WEB / "index.html"))
