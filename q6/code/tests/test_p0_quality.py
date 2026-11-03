"""文字雲品質日報（計畫書 P0 第 9 項）：RBO／JS／McNemar 純函式、金標召回嚴格比對、洩漏旗標與渠道統計、
新詞暴增 z 分數、整詞召回解析、ES 查詢形狀、設定與資料表一致。全部用對應元件，不連 ES／PG。"""
import json
import sys

import pytest

from _p0_fakes import MON, PG, REALENV, SEG, ensure_prometheus_client, read_text, read_value

ensure_prometheus_client()
if str(SEG) not in sys.path:
    sys.path.insert(0, str(SEG))
quality = pytest.importorskip("qseg.quality")
metrics = pytest.importorskip("qseg.metrics")
yaml = pytest.importorskip("yaml")


# ---------- 純函式 ----------
def test_rbo_bounds_and_order_sensitivity():
    a = ["颱風", "高雄", "選舉", "台積電", "柯文哲"]
    assert quality.rbo(a, a) == 1.0 and quality.rbo(a, ["x", "y", "z"]) == 0.0 and quality.rbo([], []) == 1.0 and quality.rbo(a, []) == 0.0
    swapped_tail = ["颱風", "高雄", "選舉", "柯文哲", "台積電"]
    swapped_head = ["高雄", "颱風", "選舉", "台積電", "柯文哲"]
    assert 0 < quality.rbo(a, swapped_head) < quality.rbo(a, swapped_tail) < 1.0     # 前面變動比後面變動扣更多（p=0.9）
    assert quality.rbo(a, a + ["多一個"]) > 0.9                                          # 長度不同也能比（外推）


def test_js_divergence_bounds():
    p = {"颱風": 10, "高雄": 5}
    assert quality.js_divergence(p, p) == 0.0
    assert quality.js_divergence(p, {"x": 3}) == 1.0                                  # 完全不重疊 → 1（底數 2）
    assert quality.js_divergence(p, {}) is None
    assert 0 < quality.js_divergence(p, {"颱風": 5, "高雄": 10}) < 1


def test_mcnemar_exact_binomial():
    assert quality.mcnemar(0, 0) == {"b": 0, "c": 0, "n": 0, "p": 1.0}
    assert quality.mcnemar(3, 3)["p"] == 1.0
    m = quality.mcnemar(56, 9)                                                         # 10/31 版對舊流程：新版才留住 56、舊版才留住 9
    assert m["n"] == 65 and m["p"] < 1e-6
    assert quality.mcnemar(4, 0)["p"] == 0.125                                         # 10/30 → 10/31：不顯著（計畫書 p = 0.0625 是單尾）


def test_gold_recall_is_strict_whole_token_match():
    items = [{"id": "1", "title": "蔣萬安拋遷移松機", "gold": ["蔣萬安", "松機"]}, {"id": "2", "title": "颱風假", "gold": ["颱風假"]}, {"id": "3", "title": "x", "gold": []}]
    good = quality.gold_recall(lambda t: ["蔣萬安", "拋", "遷移", "松機"] if "松機" in t else ["颱風假"], items)
    assert (good["kept"], good["gold"], good["recall"]) == (3, 3, 1.0)
    split = quality.gold_recall(lambda t: ["蔣", "萬安", "松機"] if "松機" in t else ["颱風", "假"], items)
    assert (split["kept"], split["recall"]) == (1, 0.3333) and split["missed_examples"] == ["蔣萬安", "颱風假"] or split["missed_examples"] == ["颱風假", "蔣萬安"]
    pair = quality.paired(good, split)
    assert (pair["b"], pair["c"]) == (2, 0)


def test_leak_flags_and_channel_stats():
    from qseg.job import url_leaks

    class Dic:
        user_words = frozenset({"鳳山區", "高雄市", "肥宅", "台北市長"})
        stopwords = frozenset({"的", "了"})
        pipeline = "v2"
    assert quality.place_words(Dic.user_words) == {"鳳山區", "高雄市"}
    f = quality.leak_flags("看 https://example.com/abc 0912-345678", ["example", "0912-345678", "2024", "的", "颱風"], Dic.stopwords, url_leaks)
    assert f == {"url": True, "phone": True, "number": True, "stopword": True}
    assert quality.leak_flags("颱風假", ["颱風假"], Dic.stopwords, url_leaks) == {"url": False, "phone": False, "number": False, "stopword": False}

    def segment_checked(dic, channel, text):          # 對應元件：鳳山區 被切開；其他沒事
        return [], (["鳳山區"] if "鳳山區" in text else [])
    docs = [{"channel": "ptt", "content": "鳳山區 今天下雨", "terms_v2": ["鳳山", "區", "今天", "下雨"]},
            {"channel": "ptt", "content": "高雄市 肥宅", "terms_v2": ["高雄市", "肥宅"]},
            {"channel": "ptt", "content": "純聊天 0912-345678", "terms_v2": ["聊天", "0912-345678"]},
            {"channel": "ptt", "content": "沒有地名", "terms_v2": ["沒有", "地名"]}]
    st = quality.channel_stats(docs, Dic(), url_leaks, segment_checked)
    assert st["docs_sampled"] == 4 and st["phone"] == 0.25 and st["url"] == 0 and st["number"] == 0 and st["stopword"] == 0
    assert st["must_keep_split"] == 0.25 and st["place_docs"] == 2 and st["place_split"] == 1 and st["place_split_ratio"] == 0.5
    empty = quality.channel_stats([], Dic(), url_leaks, segment_checked)
    assert empty["docs_sampled"] == 0 and empty["url"] is None and empty["place_split_ratio"] is None   # 沒抽到 → NULL，不是 0%


def test_surges_z_score_against_previous_7_days():
    def day(d, terms):
        return {"key_as_string": d, "terms": {"buckets": [{"key": k, "doc_count": v} for k, v in terms.items()]}}
    buckets = [day(f"2026-10-2{i}", {"颱風": 10, "高雄": 30}) for i in range(6, 10)] + [day("2026-10-30", {"颱風": 12, "高雄": 28}), day("2026-10-31", {"颱風": 9, "高雄": 31}),
               day("2026-11-01", {"颱風": 11, "高雄": 30}), day("2026-11-02", {"颱風": 80, "高雄": 31, "新字": 25, "小": 5})]
    out = quality.surges(buckets, "2026-11-02")
    terms = {s["term"]: s for s in out}
    assert "颱風" in terms and terms["颱風"]["z"] > 3 and terms["颱風"]["mean7"] == pytest.approx(10.29, abs=0.01)   # (4×10＋12＋9＋11)÷7
    assert "新字" in terms and terms["新字"]["mean7"] == 0                               # 前 7 天沒出現過：z=(25−0)/max(0,0,1)
    assert "高雄" not in terms and "小" not in terms                                      # 沒變／低於 SURGE_MIN
    assert quality.surges(buckets, "2026-11-03") == []                                    # 今天沒資料 → 空


def test_whole_word_recall_from_msearch():
    words = ["颱風假", "高雄", "罕見詞"]
    responses = [{"hits": {"total": {"value": 100}}, "aggregations": {"whole": {"doc_count": 100}}},
                 {"hits": {"total": {"value": 1000}}, "aggregations": {"whole": {"doc_count": 980}}},
                 {"hits": {"total": {"value": 3}}, "aggregations": {"whole": {"doc_count": 0}}}]
    ww = quality.whole_word_from_msearch(words, responses, min_docs=20)
    assert ww["words"] == 2 and ww["recall"] == round(1080 / 1100, 5) and ww["worst"][0]["word"] == "高雄"   # 罕見詞 < 20 篇不算
    assert quality.whole_word_from_msearch(words, [{"error": {"type": "x"}}] * 3, min_docs=20) == {"recall": None, "words": 0, "worst": []}


def test_es_bodies_exclude_tombstones_and_use_bigram_phrase():
    s = quality.sample_body("ptt", 20261102)
    flt = s["query"]["function_score"]["query"]["bool"]
    assert {"term": {"channel": "ptt"}} in flt["filter"] and {"exists": {"field": "terms_v2"}} in flt["filter"]
    assert flt["must_not"] == [{"exists": {"field": "deleted_at"}}] and s["query"]["function_score"]["random_score"]["seed"] == 20261102
    lines = quality.probe_bodies(["颱風假"], extra_filter=[{"range": {"published_at": {"gte": "now/d"}}}])
    assert lines[0] == {} and lines[1]["query"]["bool"]["filter"][0] == {"match_phrase": {"content.bigram": "颱風假"}}
    assert lines[1]["aggs"]["whole"]["filter"] == {"term": {"terms_v2": "颱風假"}}
    t = quality.top_body("(all)", "terms_v1")
    assert "filter" not in t["query"]["bool"] and t["aggs"]["top"]["terms"] == {"field": "terms_v1", "size": quality.TOP_N}
    assert quality.top_body("ptt", "terms_v2")["query"]["bool"]["filter"] == [{"term": {"channel": "ptt"}}]
    sb = quality.surge_body()
    assert sb["aggs"]["days"]["date_histogram"]["time_zone"] == "+08:00" and sb["aggs"]["days"]["aggs"]["terms"]["terms"]["field"] == "terms_v2"


def test_publish_sets_gauges_with_null_safe_defaults():
    rows = {"ptt": {"docs_sampled": 4, "url": 0.0, "phone": 0.25, "number": 0.0, "stopword": 0.0, "must_keep_split": 0.25,
                    "place_split_ratio": 0.5, "rbo_prev": None, "js_prev": None, "rbo_v1v2": 0.42}}
    all_row = {"whole_word": {"recall": 0.995, "words": 12, "worst": []}, "gold": {"v2": {"recall": 0.756}, "v0": None},
               "mcnemar": {"v1_v2": {"p": 0.000001}}, "surges": [{"term": "x", "fragment": True}, {"term": "y", "fragment": False}]}
    quality.publish(rows, all_row)
    assert read_value(metrics.QUALITY_LEAK, "ptt", "phone") == 0.25 and read_value(metrics.QUALITY_PLACE, "ptt") == 0.5
    assert read_value(metrics.QUALITY_RBO, "ptt", "prev_day") == 1 and read_value(metrics.QUALITY_RBO, "ptt", "v1_v2") == 0.42   # 沒有昨天 → 1（不響）
    assert read_value(metrics.QUALITY_WHOLE) == 0.995 and read_value(metrics.QUALITY_GOLD, "v2") == 0.756 and read_value(metrics.QUALITY_GOLD, "v0") == 0
    assert read_value(metrics.QUALITY_SURGES, "all") == 2 and read_value(metrics.QUALITY_SURGES, "fragment") == 1
    assert read_value(metrics.QUALITY_OK) > 0


# ---------- 金標檔、設定、資料表 ----------
def test_gold_file_matches_recall_eval_and_is_shipped():
    gold = json.loads(read_text(REALENV / "seg" / "quality" / "gold_150.json"))
    assert gold["titles"] == 150 and gold["gold_terms"] == 430 and gold["min_votes"] == 2 and len(gold["items"]) == 150
    ref = json.loads(read_text(REALENV.parent / "data" / "recall_eval" / "recall.json"))["gold"]
    assert {i["id"]: i["gold"] for i in gold["items"]} == ref                                        # 與 q6/code/recall_eval.py 同一份金標
    assert "COPY stack/seg/quality/ /app/quality/" in read_text(REALENV / "seg" / "Dockerfile")
    assert quality.GOLD_PATH.name == "gold_150.json"


def test_quality_service_is_wired_and_table_defined():
    compose = read_text(REALENV / "docker-compose.yml")
    assert '"quality", "--interval"' in compose and 'QUALITY_PORT: "9113"' in compose
    prom = yaml.safe_load(read_text(MON / "prometheus.yml"))
    jobs = {j["job_name"]: [t for s in j["static_configs"] for t in s["targets"]] for j in prom["scrape_configs"]}
    assert jobs["seg-quality"] == ["seg-quality:9113"]
    rules = yaml.safe_load(read_text(MON / "rules" / "quality.yml"))
    names = {r["alert"] for g in rules["groups"] for r in g["rules"]}
    assert {"SegQualityDown", "QualityStale", "QualityLeak", "QualityPlaceSplit", "QualityWholeWordRecallLow", "QualityGoldRecallDrop",
            "QualityNewWordFragment", "QualityTopShift"} <= names
    exprs = " ".join(r["expr"] for g in rules["groups"] for r in g["rules"])
    assert 'up{job="seg-quality"} == 0' in exprs and 'absent(up{job="seg-quality"})' in exprs and "< 0.99" in exprs
    for f in ("init.sql", "migrate_p0.sql"):
        assert "CREATE TABLE IF NOT EXISTS quality_daily" in read_text(PG / f) and "PRIMARY KEY (day, channel)" in read_text(PG / f)
    assert "seg-quality" in read_text(REALENV / "run.sh") and 'cmd == "quality"' in read_text(SEG / "qseg" / "__main__.py")
    assert quality.DDL.strip().startswith("CREATE TABLE IF NOT EXISTS quality_daily")
