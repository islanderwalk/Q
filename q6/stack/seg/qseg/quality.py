"""文字雲品質日報（計畫書 P0 第 9 項）：每天跑一次，寫 PostgreSQL quality_daily、/metrics 在 QUALITY_PORT。
「更乾淨」不等於「更好」：精確看洩漏，召回看金標與整詞，再做配對檢定；全部用 ES 裡的文章與目前載入的字典。

每個渠道（抽樣最近 QUALITY_WINDOW 內、有 terms_v2 的 QUALITY_SAMPLE 篇）：
- 洩漏率：詞表裡有網址殘片（job.url_leaks，看原文）、電話狀數字、純數字、硬停用詞的篇數 ÷ 抽樣篇數（與回補 G2 同一套定義）
- 必不可拆詞被切開的篇數比例（pipeline.must_keep_split，用目前字典重算斷點）；其中「地名」（使用者字典裡以縣市區鄉鎮村里結尾的詞）另計
- 前 50 詞：對昨天的 RBO（p=0.9，外推）與 JS 散度（排名與分布有沒有大變）；v1 對 v2 的 RBO（A/B 距離）
全庫：
- 查詢詞整詞召回：該詞原封不動成為 token 的篇數 ÷ content.bigram 片語比對篇數（≥ QUALITY_MIN_DOCS 篇才算）；詞來自 dict_releases.added_words、
  金標裡的使用者字典詞、QUALITY_PROBES
- 150 題金標召回（seg/quality/gold_150.json，與 q6/code/recall_eval.py 同一份）：v0／v1／v2 各算，v1→v2、v0→v2 做 McNemar 精確檢定
- 新詞暴增：今天 terms_v2 前 300 名對前 7 天平均 z > 3 且今天 ≥ QUALITY_SURGE_MIN 篇；整詞率 < 50% 的標成 fragment（新詞被切碎，該進字典）

用法：python -m qseg quality [--once] [--interval 86400]。門檻都是提案值，寫在 monitoring/rules/quality.yml。
"""
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, metrics
from .logs import log

SAMPLE = int(os.environ.get("QUALITY_SAMPLE", "300"))
WINDOW = os.environ.get("QUALITY_WINDOW", "1d")
MIN_DOCS = int(os.environ.get("QUALITY_MIN_DOCS", "20"))
SURGE_MIN = int(os.environ.get("QUALITY_SURGE_MIN", "20"))
SURGE_Z = float(os.environ.get("QUALITY_SURGE_Z", "3"))
TOP_N = int(os.environ.get("QUALITY_TOP_N", "50"))
PROBES = [w.strip() for w in os.environ.get("QUALITY_PROBES", "").split(",") if w.strip()]
GOLD_PATH = Path(os.environ.get("QUALITY_GOLD", str(Path(__file__).resolve().parent.parent / "quality" / "gold_150.json")))
TZ = timezone(timedelta(hours=8))          # 台灣（無日光節約）；ES 的 date_histogram 也用 +08:00 切日
PLACE_RE = re.compile(r".{1,5}[縣市區鄉鎮村里]$")
LEAK_KINDS = ("url", "phone", "number", "stopword", "must_keep_split")
PHONE_RE = re.compile(r"^[0-9][0-9\-]{6,}$")
NUMBER_RE = re.compile(r"^[0-9]+$")

DDL = """
CREATE TABLE IF NOT EXISTS quality_daily (
  day date NOT NULL,
  channel text NOT NULL,                             -- '(all)' 這一列放整詞召回、金標、McNemar、新詞暴增
  dict_version text,
  sample_window text,
  docs_sampled integer,
  leak_url numeric, leak_phone numeric, leak_number numeric, leak_stopword numeric, must_keep_split numeric,
  place_docs integer, place_split integer, place_split_ratio numeric,
  whole_word_recall numeric, whole_word_words integer, whole_word_worst jsonb,
  gold_recall jsonb, mcnemar jsonb, surges jsonb,
  top_v2 jsonb, top_v1 jsonb, rbo_prev numeric, js_prev numeric, rbo_v1v2 numeric,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, channel)
);
"""


# ---------- 純函式（測試直接打這些） ----------
def norm(t: str) -> str:
    return (t or "").replace(" ", "").replace("　", "").lower()


def rbo(S: list, T: list, p: float = 0.9) -> float:
    """Rank-biased overlap（Webber 2010，外推版 RBO_ext）：1 ＝ 兩個排名完全一樣，0 ＝ 完全沒交集；p=0.9 大約看前 10 名。"""
    S, T = list(S), list(T)
    if not S and not T:
        return 1.0
    if not S or not T:
        return 0.0
    L, Sh = (S, T) if len(S) >= len(T) else (T, S)
    s, l = len(L), len(Sh)
    seen_l, seen_s, X = set(), set(), [0] * (s + 1)
    for d in range(1, s + 1):
        seen_l.add(L[d - 1])
        if d <= l:
            seen_s.add(Sh[d - 1])
        X[d] = len(seen_l & seen_s)
    sum1 = sum(X[d] / d * p ** d for d in range(1, l + 1))
    sum2 = sum(X[l] * (d - l) / (d * l) * p ** d for d in range(l + 1, s + 1))
    sum3 = ((X[s] - X[l]) / s + X[l] / l) * p ** s
    return round(min(1.0, (1 - p) / p * (sum1 + sum2) + sum3), 4)


def js_divergence(a: dict, b: dict) -> float:
    """Jensen–Shannon 散度（底數 2，0～1）：兩個「詞→篇數」分布差多少。空分布回 None。"""
    ta, tb = sum(a.values()), sum(b.values())
    if not ta or not tb:
        return None
    keys = set(a) | set(b)
    out = 0.0
    for k in keys:
        pa, pb = a.get(k, 0) / ta, b.get(k, 0) / tb
        m = (pa + pb) / 2
        if pa:
            out += 0.5 * pa * math.log2(pa / m)
        if pb:
            out += 0.5 * pb * math.log2(pb / m)
    return round(out, 4)


def mcnemar(b: int, c: int) -> dict:
    """配對檢定（精確二項式、雙尾）：b＝只有新流程留住的金標詞數，c＝只有舊流程留住的。n=0 時 p=1。"""
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "n": 0, "p": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return {"b": b, "c": c, "n": n, "p": round(min(1.0, 2 * tail), 6)}


def gold_recall(seg_fn, items: list) -> dict:
    """金標召回：金標詞要原封不動成為一個 token（嚴格比對，拆開或被過濾都算漏）。回傳 kept／gold／recall／每個金標詞有沒有留住。"""
    hits, missed = [], {}
    for it in items:
        toks = {norm(t) for t in seg_fn(it["title"])}
        for g in it["gold"]:
            h = g in toks
            hits.append(h)
            if not h:
                missed[g] = missed.get(g, 0) + 1
    gold = len(hits)
    return {"kept": sum(hits), "gold": gold, "recall": round(sum(hits) / gold, 4) if gold else None,
            "hits": hits, "missed_examples": sorted(missed, key=lambda t: -missed[t])[:30]}


def paired(new: dict, old: dict) -> dict:
    b = sum(1 for n, o in zip(new["hits"], old["hits"]) if n and not o)
    c = sum(1 for n, o in zip(new["hits"], old["hits"]) if o and not n)
    return mcnemar(b, c)


def place_words(user_words) -> set:
    return {w for w in user_words if PLACE_RE.fullmatch(w)}


def leak_flags(content: str, terms: list, stopwords, url_leaks) -> dict:
    """一篇文章的洩漏旗標（與回補 G2 同一套：網址殘片看原文、電話狀、純數字、硬停用詞）。"""
    terms = terms or []
    return {"url": bool(url_leaks(content or "", terms)),
            "phone": any(PHONE_RE.match(t) for t in terms),
            "number": any(NUMBER_RE.match(t) for t in terms),
            "stopword": any(t in stopwords for t in terms)}


def channel_stats(docs: list, dic, url_leaks, segment_checked) -> dict:
    """一個渠道的抽樣統計。docs: [{content, terms_v2}]。must_keep_split／地名用目前字典重算斷點。"""
    n = len(docs)
    counts = {k: 0 for k in LEAK_KINDS}
    places = place_words(dic.user_words)
    place_docs = place_split = 0
    for d in docs:
        content, terms = d.get("content") or "", d.get("terms_v2") or []
        for k, v in leak_flags(content, terms, dic.stopwords, url_leaks).items():
            counts[k] += v
        _, bad = segment_checked(dic, d.get("channel", ""), content)
        if bad:
            counts["must_keep_split"] += 1
        if any(w in content for w in places):
            place_docs += 1
            if any(w in places for w in bad):
                place_split += 1
    out = {"docs_sampled": n, "place_docs": place_docs, "place_split": place_split,
           "place_split_ratio": round(place_split / place_docs, 5) if place_docs else None}
    for k in LEAK_KINDS:
        out[k] = round(counts[k] / n, 5) if n else None
    return out


def surges(buckets: list, today: str) -> list:
    """新詞暴增：buckets＝date_histogram（每天一個，底下 terms 子聚合）。今天的詞對前 7 天：z=(今天−平均)/max(標準差, √平均, 1)。"""
    by_day = {b["key_as_string"]: {t["key"]: t["doc_count"] for t in b["terms"]["buckets"]} for b in buckets}
    if today not in by_day:
        return []
    prev_days = sorted(d for d in by_day if d < today)[-7:]
    out = []
    for term, n in by_day[today].items():
        if n < SURGE_MIN:
            continue
        hist = [by_day[d].get(term, 0) for d in prev_days] + [0] * (7 - len(prev_days))
        mean = sum(hist) / 7
        std = math.sqrt(sum((x - mean) ** 2 for x in hist) / 7)
        z = (n - mean) / max(std, math.sqrt(mean), 1.0)
        if z > SURGE_Z:
            out.append({"term": term, "today": n, "mean7": round(mean, 2), "z": round(z, 2)})
    return sorted(out, key=lambda x: -x["z"])


def whole_word(aggs: dict, min_docs: int = None) -> dict:
    """filters 聚合 → 每個詞的整詞召回；片語比對 ≥ min_docs 篇的詞才算。回傳 micro recall、詞數、最差 10 個。"""
    min_docs = MIN_DOCS if min_docs is None else min_docs
    rows = []
    for w, b in aggs["probe"]["buckets"].items():
        phrase, whole = b["doc_count"], b["whole"]["doc_count"]
        if phrase >= min_docs:
            rows.append({"word": w, "phrase": phrase, "whole": whole, "recall": round(whole / phrase, 4)})
    tp, tw = sum(r["phrase"] for r in rows), sum(r["whole"] for r in rows)
    return {"recall": round(tw / tp, 5) if tp else None, "words": len(rows), "worst": sorted(rows, key=lambda r: r["recall"])[:10]}


# ---------- ES 查詢 ----------
def sample_body(channel: str, seed: int) -> dict:
    return {"size": SAMPLE, "_source": ["channel", "content", "terms_v2"],
            "query": {"function_score": {"query": {"bool": {"filter": [{"term": {"channel": channel}}, {"exists": {"field": "terms_v2"}},
                                                                        {"range": {"fetched_at": {"gte": f"now-{WINDOW}"}}}],
                                                             "must_not": [{"exists": {"field": "deleted_at"}}]}},
                                         "random_score": {"seed": seed, "field": "_seq_no"}}}}


def top_body(channel: str, field: str) -> dict:
    q = {"bool": {"must_not": [{"exists": {"field": "deleted_at"}}]}}
    if channel != "(all)":
        q["bool"]["filter"] = [{"term": {"channel": channel}}]
    return {"size": 0, "query": q, "aggs": {"top": {"terms": {"field": field, "size": TOP_N}}}}


def probe_bodies(words: list, field: str = "terms_v2", extra_filter: list = None) -> dict:
    """filters 子聚合每個詞的 term 條件不同，不能共用：改用 msearch 一詞一個小查詢（size 0，兩個 filter 聚合）。"""
    lines = []
    for w in words:
        q = {"bool": {"filter": [{"match_phrase": {"content.bigram": w}}] + (extra_filter or []), "must_not": [{"exists": {"field": "deleted_at"}}]}}
        lines.append({})
        lines.append({"size": 0, "query": q, "aggs": {"whole": {"filter": {"term": {field: w}}}}})
    return lines


def surge_body(days: int = 8) -> dict:
    return {"size": 0, "query": {"bool": {"filter": [{"range": {"published_at": {"gte": f"now-{days}d/d", "time_zone": "+08:00"}}}],
                                          "must_not": [{"exists": {"field": "deleted_at"}}]}},
            "aggs": {"days": {"date_histogram": {"field": "published_at", "calendar_interval": "day", "time_zone": "+08:00", "format": "yyyy-MM-dd"},
                              "aggs": {"terms": {"terms": {"field": "terms_v2", "size": 300}}}}}}


def msearch(es, lines: list) -> list:
    body = "\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n"
    r = es.s.post(f"{es.url}/_msearch", data=body.encode("utf-8"), headers={"Content-Type": "application/x-ndjson"}, timeout=300)
    r.raise_for_status()
    return r.json()["responses"]


def whole_word_from_msearch(words: list, responses: list, min_docs: int = None) -> dict:
    aggs = {"probe": {"buckets": {}}}
    for w, r in zip(words, responses):
        if "error" in r:
            continue
        aggs["probe"]["buckets"][w] = {"doc_count": r["hits"]["total"]["value"], "whole": {"doc_count": r["aggregations"]["whole"]["doc_count"]}}
    return whole_word(aggs, min_docs)


# ---------- 一天一次 ----------
def load_gold() -> list:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))["items"] if GOLD_PATH.exists() else []


def load_dicts():
    from . import dictionary
    out = {}
    for p in ("v0", "v1", "v2"):
        try:
            out[p] = dictionary.load(pipeline=p)
        except Exception as e:   # 例如 artifact 沒有 legacy.dict：那一條流程就不算
            log("quality_dict_unavailable", "WARNING", pipeline=p, error=str(e)[:200])
    return out


def probe_words(conn, dic, gold_items: list) -> list:
    words = set(PROBES)
    words |= {g for it in gold_items for g in it["gold"] if g in dic.user_words}
    if conn is not None:
        try:
            for (arr,) in conn.execute("SELECT added_words FROM dict_releases WHERE added_words IS NOT NULL").fetchall():
                words |= {w for w in (arr or []) if w}
        except Exception as e:
            log("quality_probe_words_pg_failed", "WARNING", error=str(e)[:200])
    return sorted(w for w in words if len(w) >= 2)[:400]


def prev_top(conn, channel: str, today):
    if conn is None:
        return None
    row = conn.execute("SELECT top_v2 FROM quality_daily WHERE channel=%s AND day < %s ORDER BY day DESC LIMIT 1", (channel, today)).fetchone()
    return row[0] if row else None


def run_once(es, conn, dicts: dict = None, gold_items: list = None) -> dict:
    from .job import url_leaks
    from .pipeline import segment, segment_checked
    t0 = time.perf_counter()
    dicts = dicts or load_dicts()
    dic = dicts["v2"]
    gold_items = load_gold() if gold_items is None else gold_items
    today = datetime.now(TZ).date()
    if conn is not None:
        conn.execute(DDL)
    channels = [b["key"] for b in es.req("POST", f"/{es.index}/_search?request_cache=false",
                                         {"size": 0, "aggs": {"ch": {"terms": {"field": "channel", "size": 20}}}}, timeout=120)["aggregations"]["ch"]["buckets"]]
    rows = {}
    seed = int(today.strftime("%Y%m%d"))
    for ch in channels:
        hits = es.req("POST", f"/{es.index}/_search?request_cache=false", sample_body(ch, seed), timeout=300)["hits"]["hits"]
        docs = [dict(h["_source"], channel=ch) for h in hits]
        st = channel_stats(docs, dic, url_leaks, segment_checked)
        top2 = [{"term": b["key"], "count": b["doc_count"]} for b in es.req("POST", f"/{es.index}/_search?request_cache=false", top_body(ch, "terms_v2"), timeout=300)["aggregations"]["top"]["buckets"]]
        top1 = [{"term": b["key"], "count": b["doc_count"]} for b in es.req("POST", f"/{es.index}/_search?request_cache=false", top_body(ch, "terms_v1"), timeout=300)["aggregations"]["top"]["buckets"]]
        prev = prev_top(conn, ch, today)
        st.update(top_v2=top2, top_v1=top1,
                  rbo_prev=rbo([t["term"] for t in prev], [t["term"] for t in top2]) if prev else None,
                  js_prev=js_divergence({t["term"]: t["count"] for t in prev}, {t["term"]: t["count"] for t in top2}) if prev else None,
                  rbo_v1v2=rbo([t["term"] for t in top1], [t["term"] for t in top2]))
        rows[ch] = st
    # 全庫：整詞召回、金標、McNemar、新詞暴增
    words = probe_words(conn, dic, gold_items)
    ww = whole_word_from_msearch(words, msearch(es, probe_bodies(words))) if words else {"recall": None, "words": 0, "worst": []}
    gold = {}
    for p, d in dicts.items():
        gold[p] = gold_recall(lambda t, d=d: segment(d, "ptt", t), gold_items) if gold_items else None
    mc = {}
    if gold.get("v2") and gold.get("v1"):
        mc["v1_v2"] = paired(gold["v2"], gold["v1"])
    if gold.get("v2") and gold.get("v0"):
        mc["v0_v2"] = paired(gold["v2"], gold["v0"])
    days = es.req("POST", f"/{es.index}/_search?request_cache=false", surge_body(), timeout=300)["aggregations"]["days"]["buckets"]
    sg = surges(days, today.strftime("%Y-%m-%d"))
    if sg:
        today_filter = [{"range": {"published_at": {"gte": "now/d", "time_zone": "+08:00"}}}]
        res = msearch(es, probe_bodies([s["term"] for s in sg], extra_filter=today_filter))
        for s, r in zip(sg, res):
            phrase = r.get("hits", {}).get("total", {}).get("value", 0) if "error" not in r else 0
            whole = r["aggregations"]["whole"]["doc_count"] if "error" not in r else 0
            s["whole_rate"] = round(whole / phrase, 4) if phrase else None
            s["fragment"] = s["whole_rate"] is not None and s["whole_rate"] < 0.5
    all_row = {"docs_sampled": sum(r["docs_sampled"] for r in rows.values()), "whole_word": ww, "gold": gold, "mcnemar": mc, "surges": sg}
    if conn is not None:
        save(conn, today, dic.version, rows, all_row)
    publish(rows, all_row)
    log("quality_daily", day=str(today), dict_version=dic.version, channels=len(rows), docs_sampled=all_row["docs_sampled"],
        whole_word_recall=ww["recall"], whole_word_words=ww["words"],
        gold_recall={p: (g["recall"] if g else None) for p, g in gold.items()}, mcnemar=mc,
        surges=len(sg), fragments=sum(1 for s in sg if s.get("fragment")), seconds=round(time.perf_counter() - t0, 1))
    return {"channels": rows, "all": all_row}


def save(conn, today, dict_version: str, rows: dict, all_row: dict) -> None:
    j = lambda x: json.dumps(x, ensure_ascii=False) if x is not None else None
    for ch, r in rows.items():
        conn.execute("""INSERT INTO quality_daily (day, channel, dict_version, sample_window, docs_sampled, leak_url, leak_phone, leak_number, leak_stopword,
                          must_keep_split, place_docs, place_split, place_split_ratio, top_v2, top_v1, rbo_prev, js_prev, rbo_v1v2, computed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                        ON CONFLICT (day, channel) DO UPDATE SET dict_version=EXCLUDED.dict_version, sample_window=EXCLUDED.sample_window, docs_sampled=EXCLUDED.docs_sampled,
                          leak_url=EXCLUDED.leak_url, leak_phone=EXCLUDED.leak_phone, leak_number=EXCLUDED.leak_number, leak_stopword=EXCLUDED.leak_stopword,
                          must_keep_split=EXCLUDED.must_keep_split, place_docs=EXCLUDED.place_docs, place_split=EXCLUDED.place_split,
                          place_split_ratio=EXCLUDED.place_split_ratio, top_v2=EXCLUDED.top_v2, top_v1=EXCLUDED.top_v1, rbo_prev=EXCLUDED.rbo_prev,
                          js_prev=EXCLUDED.js_prev, rbo_v1v2=EXCLUDED.rbo_v1v2, computed_at=now()""",
                     (today, ch, dict_version, WINDOW, r["docs_sampled"], r["url"], r["phone"], r["number"], r["stopword"], r["must_keep_split"],
                      r["place_docs"], r["place_split"], r["place_split_ratio"], j(r["top_v2"]), j(r["top_v1"]), r["rbo_prev"], r["js_prev"], r["rbo_v1v2"]))
    ww, gold = all_row["whole_word"], all_row["gold"]
    conn.execute("""INSERT INTO quality_daily (day, channel, dict_version, sample_window, docs_sampled, whole_word_recall, whole_word_words, whole_word_worst,
                      gold_recall, mcnemar, surges, computed_at)
                    VALUES (%s,'(all)',%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT (day, channel) DO UPDATE SET dict_version=EXCLUDED.dict_version, sample_window=EXCLUDED.sample_window, docs_sampled=EXCLUDED.docs_sampled,
                      whole_word_recall=EXCLUDED.whole_word_recall, whole_word_words=EXCLUDED.whole_word_words, whole_word_worst=EXCLUDED.whole_word_worst,
                      gold_recall=EXCLUDED.gold_recall, mcnemar=EXCLUDED.mcnemar, surges=EXCLUDED.surges, computed_at=now()""",
                 (today, dict_version, WINDOW, all_row["docs_sampled"], ww["recall"], ww["words"], j(ww["worst"]),
                  j({p: ({k: v for k, v in g.items() if k != "hits"} if g else None) for p, g in gold.items()}), j(all_row["mcnemar"]), j(all_row["surges"])))


def publish(rows: dict, all_row: dict) -> None:
    for ch, r in rows.items():
        metrics.QUALITY_SAMPLED.labels(ch).set(r["docs_sampled"])
        for k in LEAK_KINDS:
            metrics.QUALITY_LEAK.labels(ch, k).set(r[k] if r[k] is not None else 0)
        metrics.QUALITY_PLACE.labels(ch).set(r["place_split_ratio"] if r["place_split_ratio"] is not None else 0)
        metrics.QUALITY_RBO.labels(ch, "prev_day").set(r["rbo_prev"] if r["rbo_prev"] is not None else 1)
        metrics.QUALITY_RBO.labels(ch, "v1_v2").set(r["rbo_v1v2"] if r["rbo_v1v2"] is not None else 0)
        metrics.QUALITY_JS.labels(ch).set(r["js_prev"] if r["js_prev"] is not None else 0)
    ww = all_row["whole_word"]
    metrics.QUALITY_WHOLE.set(ww["recall"] if ww["recall"] is not None else 1)
    metrics.QUALITY_WHOLE_WORDS.set(ww["words"])
    for p, g in all_row["gold"].items():
        metrics.QUALITY_GOLD.labels(p).set(g["recall"] if g and g["recall"] is not None else 0)
    for pair, m in all_row["mcnemar"].items():
        metrics.QUALITY_MCNEMAR_P.labels(pair).set(m["p"])
    sg = all_row["surges"]
    metrics.QUALITY_SURGES.labels("all").set(len(sg))
    metrics.QUALITY_SURGES.labels("fragment").set(sum(1 for s in sg if s.get("fragment")))
    metrics.QUALITY_OK.set(time.time())


def main(args):
    import psycopg

    from .es import ES

    once = "--once" in args
    interval = 86400.0
    if "--interval" in args:
        interval = float(args[args.index("--interval") + 1])
    es = ES()
    if not once:
        metrics.serve(config.QUALITY_PORT, "quality")
    while True:
        try:
            with psycopg.connect(config.PG_DSN, autocommit=True) as conn:
                run_once(es, conn)
        except Exception as e:   # 失敗不能讓常駐程序死掉；QUALITY_OK 不更新 → QualityStale 會響
            metrics.QUALITY_ERR.inc()
            log("quality_failed", "ERROR", error=str(e)[:300])
            if once:
                return 1
        if once:
            return 0
        time.sleep(interval)
