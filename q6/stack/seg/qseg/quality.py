"""文字雲品質日報（計畫書 P0 第 9 項）：每天跑一次，寫 PostgreSQL quality_daily、/metrics 在 QUALITY_PORT。
「更乾淨」不等於「更好」：精確看洩漏，召回看金標與整詞，再做配對檢定；全部用 ES 裡的文章與目前載入的字典。

每個渠道（抽樣最近 QUALITY_WINDOW 內、有 terms_v2 的 QUALITY_SAMPLE 篇）：
- 洩漏率：詞表裡有網址殘片（job.url_leaks，看原文）、電話狀數字、純數字、硬停用詞的篇數 ÷ 抽樣篇數（與回補 G2 同一套定義）
- 必不可拆詞被切開的篇數比例（pipeline.must_keep_split，用目前字典重算斷點）；其中「地名」（使用者字典裡以縣市區鄉鎮村里結尾的詞）另計；
  被切開的地方留例子（post_id、詞、切法、清洗後原文前後 10 字，每渠道最多 QUALITY_SPLIT_EXAMPLES 筆）：quality_daily.split_examples 與日誌 quality_splits
- 前 50 詞：對昨天的 RBO（p=0.9，外推）與 JS 散度（排名與分布有沒有大變）；v1 對 v2 的 RBO（A/B 距離）
全庫：
- 查詢詞整詞召回：content.bigram 片語比對（≥ QUALITY_MIN_DOCS 篇才算）的篇數裡，沒有確實被切開的比例；詞來自 dict_releases.added_words、
  金標裡的使用者字典詞、QUALITY_PROBES。口徑與 must_keep_split 一致：片語有、terms_v2 沒有的「漏網」文章每詞隨機抽 QUALITY_MISS_SAMPLE 篇
  用目前字典重斷，只有 split 算漏，交集型歧義（台北／市長）、在更長的詞裡（台北市立）、詞表過期都不算（估計方法見 whole_word）。
  原始口徑（Σ整詞 ÷ Σ片語）留在 whole_word_worst 每個詞的 recall_raw 與日誌 quality_daily 的 whole_word_recall_raw
- 150 題金標召回（seg/quality/gold_150.json，與 q6/code/recall_eval.py 同一份）：v0／v1／v2 各算，v1→v2、v0→v2 做 McNemar 精確檢定
- 新詞暴增：今天 terms_v2 前 300 名對前 7 天平均 z > 3 且今天 ≥ QUALITY_SURGE_MIN 篇；整詞率 < 50% 的標成 fragment（新詞被切碎，該進字典）

用法：python -m qseg quality [--once] [--interval 86400]。門檻都是提案值，寫在 monitoring/rules/quality.yml。
唯讀診斷：python -m qseg quality --diagnose [--seed YYYYMMDD] [--examples 5]：同一套查詢與抽樣，印出每個詞的漏網分類與例子、
各渠道被切開的詞與上下文；不建表、不寫 PG／ES、不更新 /metrics（diagnose）。
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
# 整詞召回的漏網抽樣：漏最多的 MISS_WORDS 個詞、每詞最多 MISS_SAMPLE 篇，ES 每天最多多抓 MISS_WORDS × MISS_SAMPLE 篇原文
MISS_SAMPLE = int(os.environ.get("QUALITY_MISS_SAMPLE", "200"))
MISS_WORDS = int(os.environ.get("QUALITY_MISS_WORDS", "20"))
EXAMPLES = int(os.environ.get("QUALITY_SPLIT_EXAMPLES", "20"))     # 每個渠道最多記幾筆被切開的例子
CONTEXT = 10                                                         # 例子帶清洗後原文前後各幾個字
TZ = timezone(timedelta(hours=8))          # 台灣（無日光節約）；ES 的 date_histogram 也用 +08:00 切日
PLACE_RE = re.compile(r".{1,5}[縣市區鄉鎮村里]$")
LEAK_KINDS = ("url", "phone", "number", "stopword", "must_keep_split")
# 漏網文章用目前字典重斷後的分類（miss_kind），只有 split 算漏；順序就是判斷的優先序
MISS_KINDS = ("stale", "split", "overlap", "inside", "filtered", "absent")
WORST_LOG_KEYS = ("word", "phrase", "whole", "recall_raw", "misses", "sampled", "kinds", "split_est", "recall")   # 日誌只放這幾欄，例子看 PG
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
ALTER TABLE quality_daily ADD COLUMN IF NOT EXISTS split_examples jsonb;   -- 被切開的必不可拆詞例子（每渠道最多 20 筆）；舊表跑日報時自己補欄
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


def cut_of(toks: list, i: int, j: int) -> str:
    """蓋到原文 [i, j) 的那幾個詞用／接起來：鳳山／區、辦好／市多、台北／市長。"""
    return "／".join(t for t, s, e in toks if s < j and e > i)


def context_of(text: str, i: int, j: int) -> str:
    return re.sub(r"\s+", " ", text[max(0, i - CONTEXT): j + CONTEXT])


def split_examples(post_id, clean: str, toks: list, bad: list, places, freq=None) -> list:
    """被切開的必不可拆詞 → 例子：每個詞取第一處 split（pipeline.occurrence_kind），附切法 cut 與清洗後原文前後 CONTEXT 字。
    freq：斷詞器的詞頻表（pipeline.dict_freq），與 must_keep_split 同一條規則。"""
    from .pipeline import occurrences
    out = []
    for w in bad:
        i = next((i for i, k in occurrences(clean, toks, w, freq) if k == "split"), None)
        if i is not None:
            j = i + len(w)
            out.append({"post_id": post_id, "word": w, "place": w in places, "cut": cut_of(toks, i, j), "context": context_of(clean, i, j)})
    return out


def pick_examples(examples: list, k: int = None) -> list:
    """最多 k 筆（預設 EXAMPLES）：每個詞先各取一筆再輪下一輪，同一輪地名優先，不會被同一個詞佔滿。"""
    k = EXAMPLES if k is None else k
    seen, keyed = {}, []
    for n, e in enumerate(examples):
        r = seen.get(e["word"], 0)
        seen[e["word"]] = r + 1
        keyed.append(((r, not e.get("place"), n), e))
    return [e for _, e in sorted(keyed, key=lambda x: x[0])[:k]]


def channel_stats(docs: list, dic, url_leaks, segment_detail, examples: int = None) -> dict:
    """一個渠道的抽樣統計。docs: [{post_id, content, terms_v2}]。must_keep_split／地名用目前字典重算斷點：
    segment_detail(dic, 渠道, 原文) 回傳 (清洗後原文, 斷點, 詞表, 被切開的詞)，被切開的地方留例子（最多 examples 筆，預設 EXAMPLES）。"""
    from .pipeline import dict_freq
    n = len(docs)
    counts = {k: 0 for k in LEAK_KINDS}
    places = place_words(dic.user_words)
    place_docs = place_split = 0
    found = []
    for d in docs:
        content, terms = d.get("content") or "", d.get("terms_v2") or []
        for k, v in leak_flags(content, terms, dic.stopwords, url_leaks).items():
            counts[k] += v
        clean, toks, _, bad = segment_detail(dic, d.get("channel", ""), content)
        if bad:
            counts["must_keep_split"] += 1
            found += split_examples(d.get("post_id"), clean, toks, bad, places, dict_freq(dic))
        if any(w in content for w in places):
            place_docs += 1
            if any(w in places for w in bad):
                place_split += 1
    out = {"docs_sampled": n, "place_docs": place_docs, "place_split": place_split,
           "place_split_ratio": round(place_split / place_docs, 5) if place_docs else None,
           "split_docs": counts["must_keep_split"], "split_examples": pick_examples(found, examples)}
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


def miss_kind(word: str, clean: str, toks: list, terms: list, freq=None):
    """一篇漏網文章（片語比對有、ES 的 terms_v2 沒有 word）用目前字典重斷後是哪一類，回傳 (分類, 代表那一處的起點或 None)。
    stale＝目前字典會留住 word（ES 的詞表是舊字典斷的或還沒斷，交給定向重跑與對帳）；split＝有一處確實被切開，算漏；
    overlap＝交集型歧義（台北／市長）；inside＝整段在更長的詞裡（台北市立）；filtered＝剛好一個詞卻被過濾；
    absent＝清洗後原文沒有這個詞（網址、#／@ 標記、PTT 標頭與引言行被清掉）。split／overlap／inside 與 must_keep_split 同一條規則
    （freq：斷詞器的詞頻表 pipeline.dict_freq）。"""
    from .pipeline import occurrences
    occ = occurrences(clean, toks, word, freq)
    if word in terms:
        return "stale", next((i for i, k in occ if k == "whole"), occ[0][0] if occ else None)
    for kind in ("split", "overlap", "inside", "whole"):
        i = next((i for i, k in occ if k == kind), None)
        if i is not None:
            return ("filtered" if kind == "whole" else kind), i
    return "absent", None


def probe_rows(words: list, responses: list, min_docs: int = None) -> list:
    """msearch 回應 → 每個詞的片語篇數與整詞篇數；片語比對 ≥ min_docs 篇的詞才算，查詢失敗的詞跳過。"""
    min_docs = MIN_DOCS if min_docs is None else min_docs
    rows = []
    for w, r in zip(words, responses):
        if "error" in r:
            continue
        phrase, whole = r["hits"]["total"]["value"], r["aggregations"]["whole"]["doc_count"]
        if phrase >= max(min_docs, 1):
            rows.append({"word": w, "phrase": phrase, "whole": whole})
    return rows


def adjusted_rows(rows: list, samples: dict = None) -> list:
    """每個詞補上原始口徑 recall_raw、漏網篇數 misses、抽樣篇數 sampled 與分類 kinds、估計真漏 split_est、調整後 recall。"""
    out = []
    for r in rows:
        r = dict(r)
        misses = r["phrase"] - r["whole"]
        s = (samples or {}).get(r["word"])
        r.update(recall_raw=round(r["whole"] / r["phrase"], 4), misses=misses, sampled=0)
        if misses and s and s["n"]:
            r.update(sampled=s["n"], kinds=s["kinds"], split_est=round(misses * s["kinds"].get("split", 0) / s["n"], 1), exact=s["n"] >= misses)
            if s["examples"].get("split"):
                r["split_examples"] = s["examples"]["split"]
        else:
            r["split_est"] = float(misses)          # 沒有漏網，或沒抽到樣本：照原始口徑全部算漏（保守）
        r["recall"] = round(1 - r["split_est"] / r["phrase"], 4)
        out.append(r)
    return out


def _split_var(r: dict) -> float:
    """估計真漏的變異數（不放回簡單隨機抽樣：M²·(1 − n/M)·p(1 − p)/(n − 1)）；沒抽樣或抽完全部時是 0。"""
    m, n = r["misses"], r["sampled"]
    if n < 2 or n >= m:
        return 0.0
    p = r["kinds"].get("split", 0) / n
    return m * m * (1 - n / m) * p * (1 - p) / (n - 1)


def whole_word(rows: list, samples: dict = None) -> dict:
    """整詞召回（micro）：recall＝1 − Σ估計真漏 ÷ Σ片語；原始口徑 recall_raw＝Σ整詞 ÷ Σ片語（改口徑前的數字）。
    漏網文章（片語有、terms_v2 沒有）每詞隨機抽 n 篇用目前字典重斷（miss_samples）：只有 split 算漏，其餘分類（miss_kind）都不算。
    估計真漏＝漏網篇數 × 抽到的 split 篇數 ÷ n：簡單隨機抽樣的展開估計，不偏；n 等於漏網篇數時就是精確值。
    分母固定，所以 recall 也不偏；se 是它的標準誤。samples 沒有的詞（沒抽、超過 MISS_WORDS、查詢失敗）照原始口徑全部算漏，只會偏低。"""
    rows = adjusted_rows(rows, samples)
    tp = sum(r["phrase"] for r in rows)
    if not tp:
        return {"recall": None, "recall_raw": None, "se": None, "words": 0, "sampled": 0, "worst": []}
    return {"recall": round(1 - sum(r["split_est"] for r in rows) / tp, 5), "recall_raw": round(sum(r["whole"] for r in rows) / tp, 5),
            "se": round(math.sqrt(sum(_split_var(r) for r in rows)) / tp, 5), "words": len(rows), "sampled": sum(r["sampled"] for r in rows),
            "worst": sorted(rows, key=lambda r: (r["recall"], r["recall_raw"]))[:10]}


# ---------- ES 查詢 ----------
def sample_body(channel: str, seed: int) -> dict:
    """渠道抽樣。只有 filter 的 bool 分數是 0，function_score 預設 boost_mode=multiply 會把亂數乘成 0、退回索引順序，
    所以要 replace：分數直接用 random_score，前 SAMPLE 篇才是隨機抽樣。"""
    return {"size": SAMPLE, "_source": ["post_id", "channel", "content", "terms_v2"],
            "query": {"function_score": {"query": {"bool": {"filter": [{"term": {"channel": channel}}, {"exists": {"field": "terms_v2"}},
                                                                        {"range": {"fetched_at": {"gte": f"now-{WINDOW}"}}}],
                                                             "must_not": [{"exists": {"field": "deleted_at"}}]}},
                                         "random_score": {"seed": seed, "field": "_seq_no"}, "boost_mode": "replace"}}}


def top_body(channel: str, field: str) -> dict:
    q = {"bool": {"must_not": [{"exists": {"field": "deleted_at"}}]}}
    if channel != "(all)":
        q["bool"]["filter"] = [{"term": {"channel": channel}}]
    return {"size": 0, "query": q, "aggs": {"top": {"terms": {"field": field, "size": TOP_N}}}}


def probe_bodies(words: list, field: str = "terms_v2", extra_filter: list = None) -> dict:
    """filters 子聚合每個詞的 term 條件不同，不能共用：改用 msearch 一詞一個小查詢（size 0，兩個 filter 聚合）。
    track_total_hits：片語篇數取 hits.total，預設超過 1 萬篇就只回 10000（而整詞篇數是聚合、沒有上限），召回會被算錯。"""
    lines = []
    for w in words:
        q = {"bool": {"filter": [{"match_phrase": {"content.bigram": w}}] + (extra_filter or []), "must_not": [{"exists": {"field": "deleted_at"}}]}}
        lines.append({})
        lines.append({"size": 0, "track_total_hits": True, "query": q, "aggs": {"whole": {"filter": {"term": {field: w}}}}})
    return lines


def miss_body(word: str, seed: int, size: int = None) -> dict:
    """整詞召回的漏網文章（片語比對有、terms_v2 沒有這個詞；與 probe_bodies 同一組條件）隨機抽 size 篇。
    random_score 固定種子（與渠道抽樣同一個，按日期）：同一天重跑或 --diagnose 抽到同一批（_seq_no 在文章改寫時會換，改寫過的會變）；
    boost_mode=replace（理由見 sample_body），
    隨機排序取前 size 篇＝不放回的簡單隨機抽樣，whole_word 的展開估計才不偏。"""
    return {"size": MISS_SAMPLE if size is None else size, "_source": ["post_id", "channel", "content", "dict_version_v2"],
            "query": {"function_score": {"query": {"bool": {"filter": [{"match_phrase": {"content.bigram": word}}],
                                                             "must_not": [{"term": {"terms_v2": word}}, {"exists": {"field": "deleted_at"}}]}},
                                         "random_score": {"seed": seed, "field": "_seq_no"}, "boost_mode": "replace"}}}


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


def whole_word_from_msearch(words: list, responses: list, min_docs: int = None, samples: dict = None) -> dict:
    return whole_word(probe_rows(words, responses, min_docs), samples)


def raw_context(content: str, word: str):
    i = content.find(word)
    return context_of(content, i, i + len(word)) if i >= 0 else None


def miss_samples(es, dic, rows: list, seed: int, segment_detail, keep: int = 3) -> dict:
    """漏網文章抽樣重斷：漏最多的 MISS_WORDS 個詞各查一次（miss_body，最多 MISS_SAMPLE 篇），逐篇 miss_kind。
    ES 成本上限 MISS_WORDS 次查詢、MISS_WORDS × MISS_SAMPLE 篇原文。回傳 {詞: {"n": 抽到幾篇, "kinds": {分類: 篇數},
    "examples": {分類: [最多 keep 筆]}, "dict_versions": {這些文章 terms_v2 的字典版本: 篇數}}}。"""
    from .pipeline import dict_freq
    freq = dict_freq(dic)
    todo = sorted((r for r in rows if r["phrase"] > r["whole"]), key=lambda r: r["whole"] - r["phrase"])[:MISS_WORDS]
    out = {}
    for r in todo:
        w = r["word"]
        try:
            hits = es.req("POST", f"/{es.index}/_search?request_cache=false", miss_body(w, seed), timeout=300)["hits"]["hits"]
        except Exception as e:   # 抽不到就當沒抽樣：這個詞照原始口徑算漏（保守），整份日報照跑
            log("quality_miss_sample_failed", "WARNING", word=w, error=str(e)[:200])
            continue
        kinds, examples, versions = {}, {}, {}
        for h in hits:
            src = h.get("_source") or {}
            content = src.get("content") or ""
            clean, toks, terms, _ = segment_detail(dic, src.get("channel", ""), content)
            kind, i = miss_kind(w, clean, toks, terms, freq)
            kinds[kind] = kinds.get(kind, 0) + 1
            dv = src.get("dict_version_v2") or "(none)"
            versions[dv] = versions.get(dv, 0) + 1
            ex = examples.setdefault(kind, [])
            if len(ex) < keep:
                j = None if i is None else i + len(w)
                ex.append({"post_id": src.get("post_id"), "channel": src.get("channel"), "dict_version": src.get("dict_version_v2"),
                           "cut": cut_of(toks, i, j) if i is not None else None,
                           "context": context_of(clean, i, j) if i is not None else raw_context(content, w)})
        out[w] = {"n": len(hits), "kinds": {k: kinds[k] for k in MISS_KINDS if k in kinds}, "examples": examples, "dict_versions": versions}
    return out


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


def list_channels(es) -> list:
    return [b["key"] for b in es.req("POST", f"/{es.index}/_search?request_cache=false",
                                     {"size": 0, "aggs": {"ch": {"terms": {"field": "channel", "size": 20}}}}, timeout=120)["aggregations"]["ch"]["buckets"]]


def channel_docs(es, ch: str, seed: int) -> list:
    hits = es.req("POST", f"/{es.index}/_search?request_cache=false", sample_body(ch, seed), timeout=300)["hits"]["hits"]
    return [dict(h["_source"], channel=ch) for h in hits]


def whole_word_live(es, dic, words: list, seed: int, segment_detail, keep: int = 3):
    """查詢詞 → (整詞召回, 每詞片語／整詞篇數, 漏網抽樣)；run_once 與 diagnose 共用。"""
    if not words:
        return whole_word([]), [], {}
    rows = probe_rows(words, msearch(es, probe_bodies(words)))
    samples = miss_samples(es, dic, rows, seed, segment_detail, keep=keep)
    return whole_word(rows, samples), rows, samples


def run_once(es, conn, dicts: dict = None, gold_items: list = None) -> dict:
    from .job import url_leaks
    from .pipeline import segment, segment_detail
    t0 = time.perf_counter()
    dicts = dicts or load_dicts()
    dic = dicts["v2"]
    gold_items = load_gold() if gold_items is None else gold_items
    today = datetime.now(TZ).date()
    if conn is not None:
        conn.execute(DDL)
    rows = {}
    seed = int(today.strftime("%Y%m%d"))
    for ch in list_channels(es):
        st = channel_stats(channel_docs(es, ch, seed), dic, url_leaks, segment_detail)
        top2 = [{"term": b["key"], "count": b["doc_count"]} for b in es.req("POST", f"/{es.index}/_search?request_cache=false", top_body(ch, "terms_v2"), timeout=300)["aggregations"]["top"]["buckets"]]
        top1 = [{"term": b["key"], "count": b["doc_count"]} for b in es.req("POST", f"/{es.index}/_search?request_cache=false", top_body(ch, "terms_v1"), timeout=300)["aggregations"]["top"]["buckets"]]
        prev = prev_top(conn, ch, today)
        st.update(top_v2=top2, top_v1=top1,
                  rbo_prev=rbo([t["term"] for t in prev], [t["term"] for t in top2]) if prev else None,
                  js_prev=js_divergence({t["term"]: t["count"] for t in prev}, {t["term"]: t["count"] for t in top2}) if prev else None,
                  rbo_v1v2=rbo([t["term"] for t in top1], [t["term"] for t in top2]))
        rows[ch] = st
        if st["split_examples"]:   # QualityLeak（must_keep_split）／QualityPlaceSplit 響了，從這行就看得到是哪篇、哪個詞、怎麼切
            log("quality_splits", "WARNING", day=str(today), channel=ch, docs_sampled=st["docs_sampled"], split_docs=st["split_docs"],
                place_split=st["place_split"], place_docs=st["place_docs"], examples=st["split_examples"])
    # 全庫：整詞召回、金標、McNemar、新詞暴增
    ww, _, _ = whole_word_live(es, dic, probe_words(conn, dic, gold_items), seed, segment_detail)
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
        whole_word_recall=ww["recall"], whole_word_recall_raw=ww["recall_raw"], whole_word_se=ww["se"], whole_word_words=ww["words"],
        whole_word_sampled=ww["sampled"], whole_word_worst=[{k: r.get(k) for k in WORST_LOG_KEYS} for r in ww["worst"]],
        gold_recall={p: (g["recall"] if g else None) for p, g in gold.items()}, mcnemar=mc,
        surges=len(sg), fragments=sum(1 for s in sg if s.get("fragment")), seconds=round(time.perf_counter() - t0, 1))
    return {"channels": rows, "all": all_row}


def save(conn, today, dict_version: str, rows: dict, all_row: dict) -> None:
    j = lambda x: json.dumps(x, ensure_ascii=False) if x is not None else None
    for ch, r in rows.items():
        conn.execute("""INSERT INTO quality_daily (day, channel, dict_version, sample_window, docs_sampled, leak_url, leak_phone, leak_number, leak_stopword,
                          must_keep_split, place_docs, place_split, place_split_ratio, top_v2, top_v1, rbo_prev, js_prev, rbo_v1v2, split_examples, computed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                        ON CONFLICT (day, channel) DO UPDATE SET dict_version=EXCLUDED.dict_version, sample_window=EXCLUDED.sample_window, docs_sampled=EXCLUDED.docs_sampled,
                          leak_url=EXCLUDED.leak_url, leak_phone=EXCLUDED.leak_phone, leak_number=EXCLUDED.leak_number, leak_stopword=EXCLUDED.leak_stopword,
                          must_keep_split=EXCLUDED.must_keep_split, place_docs=EXCLUDED.place_docs, place_split=EXCLUDED.place_split,
                          place_split_ratio=EXCLUDED.place_split_ratio, top_v2=EXCLUDED.top_v2, top_v1=EXCLUDED.top_v1, rbo_prev=EXCLUDED.rbo_prev,
                          js_prev=EXCLUDED.js_prev, rbo_v1v2=EXCLUDED.rbo_v1v2, split_examples=EXCLUDED.split_examples, computed_at=now()""",
                     (today, ch, dict_version, WINDOW, r["docs_sampled"], r["url"], r["phone"], r["number"], r["stopword"], r["must_keep_split"],
                      r["place_docs"], r["place_split"], r["place_split_ratio"], j(r["top_v2"]), j(r["top_v1"]), r["rbo_prev"], r["js_prev"], r["rbo_v1v2"],
                      j(r.get("split_examples"))))
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


def _counts(d: dict) -> str:
    return "、".join(f"{k} {v}" for k, v in d.items()) or "—"


def diagnose(es, dic, conn, gold_items: list, seed: int, out=print, keep: int = 5) -> dict:
    """唯讀診斷（--diagnose）：與 run_once 同一套查詢、抽樣（同一天同一個種子）與重斷，但不建表、不寫 PG／ES、不更新 /metrics。
    印出每個查詢詞的片語／整詞篇數與漏網分類（每類 keep 個例子），以及每個渠道抽樣裡被切開的必不可拆詞（地名另標）與上下文。"""
    from .job import url_leaks
    from .pipeline import segment_detail
    words = probe_words(conn, dic, gold_items)
    ww, base, samples = whole_word_live(es, dic, words, seed, segment_detail, keep=keep)
    out(f"== 文字雲品質診斷（唯讀：不建表、不寫 PG／ES、不更新 /metrics）　字典 {dic.version}　種子 {seed} ==")
    out(f"查詢詞 {len(words)} 個，片語比對 ≥ {MIN_DOCS} 篇的 {ww['words']} 個；漏網文章每詞隨機抽 {MISS_SAMPLE} 篇（最多 {MISS_WORDS} 個詞），用目前字典重斷")
    out(f"整詞召回：原始口徑 {ww['recall_raw']} → 調整後 {ww['recall']}（標準誤 {ww['se']}，抽樣 {ww['sampled']} 篇；告警門檻 0.99）")
    out("分類：split＝確實被切開（算漏）　overlap＝交集型歧義，例如 台北／市長（允許）　inside＝在更長的詞裡，例如 台北市立　"
        "stale＝目前字典會留住（ES 的詞表是舊字典斷的或還沒斷）　filtered＝整詞但被過濾　absent＝清洗後原文沒有這個詞")
    for r in sorted(adjusted_rows(base, samples), key=lambda r: (r["recall"], r["recall_raw"])):
        out(f"-- {r['word']}　片語 {r['phrase']}　整詞 {r['whole']}（原始 {r['recall_raw']}）　漏網 {r['misses']}　抽 {r['sampled']} 篇："
            f"{_counts(r.get('kinds') or {})}　→ 估計真漏 {r['split_est']}　調整後 {r['recall']}")
        s = samples.get(r["word"])
        if s:
            out(f"   漏網文章 terms_v2 的字典版本：{_counts(s['dict_versions'])}")
            for k in MISS_KINDS:
                for e in s["examples"].get(k, []):
                    out(f"   {k:<8} {e['post_id']}（{e['channel']}）　{e['cut'] or '—'}　「{e['context'] or ''}」")
    out(f"== 洩漏與地名抽樣（每渠道最多 {SAMPLE} 篇、fetched_at ≥ now-{WINDOW}、種子 {seed}；與日報同一套 must_keep_split）==")
    chans = {}
    for ch in list_channels(es):
        st = channel_stats(channel_docs(es, ch, seed), dic, url_leaks, segment_detail, examples=10 ** 6)
        chans[ch] = st
        n = st["docs_sampled"]
        leaks = _counts({k: round((st[k] or 0) * n) for k in LEAK_KINDS})
        out(f"-- {ch}　抽樣 {n} 篇　洩漏篇數：{leaks}　地名被切開 {st['place_split']}／含地名 {st['place_docs']}")
        for e in st["split_examples"]:
            out(f"   {'地名 ' if e['place'] else ''}{e['word']}　{e['cut']}　「{e['context']}」　{e['post_id']}")
    return {"whole_word": ww, "rows": base, "samples": samples, "channels": chans}


def diagnose_main(args) -> int:
    """PG 用 default_transaction_read_only 連線（只讀 dict_releases.added_words 當查詢詞）；ES 只發 _search 與 _msearch。"""
    import psycopg

    from . import dictionary
    from .es import ES

    seed = int(args[args.index("--seed") + 1]) if "--seed" in args else int(datetime.now(TZ).strftime("%Y%m%d"))
    keep = int(args[args.index("--examples") + 1]) if "--examples" in args else 5
    dic = dictionary.load(pipeline="v2")
    conn = None
    try:
        conn = psycopg.connect(config.PG_DSN, autocommit=True, connect_timeout=10, options="-c default_transaction_read_only=on")
    except Exception as e:   # 連不上 PG 也能診斷，只是少了 dict_releases 的加詞
        print(f"（PG 連不上，查詢詞只用金標與 QUALITY_PROBES：{str(e)[:200]}）")
    try:
        diagnose(ES(), dic, conn, load_gold(), seed, keep=keep)
    finally:
        if conn is not None:
            conn.close()
    return 0


def main(args):
    if "--diagnose" in args:
        return diagnose_main(args)

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
