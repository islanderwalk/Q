"""文字雲品質日報（計畫書 P0 第 9 項）：RBO／JS／McNemar 純函式、金標召回嚴格比對、洩漏旗標與渠道統計、
新詞暴增 z 分數、整詞召回解析、ES 查詢形狀、設定與資料表一致；整詞召回與 must_keep_split 同一條規則（漏網抽樣重斷、
交集型歧義不算漏）、被切開的例子、唯讀診斷。ES／PG 都用測試裡的假物件，不連線；斷詞用 jieba ＋ 測試內嵌的極小字典。"""
import json
import sys

import pytest

from _p0_fakes import MON, PG, REALENV, SEG, ensure_prometheus_client, read_text, read_value

ensure_prometheus_client()
for _p in (SEG, REALENV.parent / "code"):       # qseg.pipeline 會 import q6/code/segmenter.py；單獨跑這個檔也要找得到
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
quality = pytest.importorskip("qseg.quality")
metrics = pytest.importorskip("qseg.metrics")

# 極小主字典（不需要 dict.txt.big）：台北／市長 是交集型歧義、台北市立 把台北市包在裡面；
# 鳳山區、好市多 只在使用者詞表、不在 tokenizer 裡，模擬字典回歸後被切成 鳳山／區、辦好／市多
TINY_BASE = ("台北 500 ns\n市長 800 n\n台北市 95 ns\n台北市立 3 nt\n美術館 200 n\n政府 500 n\n蔣萬安 50 nr\n辦好 50 v\n市多 60 n\n"
             "會員 300 n\n鳳山 100 ns\n區 500 n\n今天 800 t\n宣布 300 v\n下雨 300 v\n")


@pytest.fixture(scope="module")
def tiny_dic(tmp_path_factory):
    jieba = pytest.importorskip("jieba")
    from qseg import dictionary
    d = tmp_path_factory.mktemp("tiny_dict")
    (d / "base.dict").write_text(TINY_BASE, encoding="utf-8")
    tk = jieba.Tokenizer(dictionary=str(d / "base.dict"))
    tk.tmp_dir = str(d)
    tk.initialize()
    return dictionary.Dictionary(version="qsearch-q6-2026.10.30.2", manifest={}, tokenizer=tk, stopwords=frozenset({"的"}), soft_stopwords=frozenset(),
                                 merges={}, user_words=frozenset({"台北市", "好市多", "鳳山區", "蔣萬安"}), pipeline="v2")


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

    def segment_detail(dic, channel, text):           # 假的斷詞：鳳山區 被切成 鳳山／區；其他沒事
        i = text.find("鳳山區")
        if i < 0:
            return text, [], [], []
        return text, [("鳳山", i, i + 2), ("區", i + 2, i + 3)], [], ["鳳山區"]
    docs = [{"post_id": "ptt-1", "channel": "ptt", "content": "鳳山區 今天下雨", "terms_v2": ["鳳山", "區", "今天", "下雨"]},
            {"post_id": "ptt-2", "channel": "ptt", "content": "高雄市 肥宅", "terms_v2": ["高雄市", "肥宅"]},
            {"post_id": "ptt-3", "channel": "ptt", "content": "純聊天 0912-345678", "terms_v2": ["聊天", "0912-345678"]},
            {"post_id": "ptt-4", "channel": "ptt", "content": "沒有地名", "terms_v2": ["沒有", "地名"]}]
    st = quality.channel_stats(docs, Dic(), url_leaks, segment_detail)
    assert st["docs_sampled"] == 4 and st["phone"] == 0.25 and st["url"] == 0 and st["number"] == 0 and st["stopword"] == 0
    assert st["must_keep_split"] == 0.25 and st["place_docs"] == 2 and st["place_split"] == 1 and st["place_split_ratio"] == 0.5
    assert st["split_docs"] == 1 and st["split_examples"] == [{"post_id": "ptt-1", "word": "鳳山區", "place": True, "cut": "鳳山／區", "context": "鳳山區 今天下雨"}]
    empty = quality.channel_stats([], Dic(), url_leaks, segment_detail)
    assert empty["docs_sampled"] == 0 and empty["url"] is None and empty["place_split_ratio"] is None   # 沒抽到 → NULL，不是 0%
    assert empty["split_examples"] == []


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
    assert ww["words"] == 2 and ww["worst"][0]["word"] == "高雄"                                     # 罕見詞 < 20 篇不算
    assert ww["recall"] == ww["recall_raw"] == round(1080 / 1100, 5) and ww["sampled"] == 0              # 沒抽樣 → 照原始口徑全部算漏
    assert quality.whole_word_from_msearch(words, [{"error": {"type": "x"}}] * 3, min_docs=20) == \
        {"recall": None, "recall_raw": None, "se": None, "words": 0, "sampled": 0, "worst": []}


def test_es_bodies_exclude_tombstones_and_use_bigram_phrase():
    s = quality.sample_body("ptt", 20261102)
    flt = s["query"]["function_score"]["query"]["bool"]
    assert {"term": {"channel": "ptt"}} in flt["filter"] and {"exists": {"field": "terms_v2"}} in flt["filter"]
    assert flt["must_not"] == [{"exists": {"field": "deleted_at"}}] and s["query"]["function_score"]["random_score"]["seed"] == 20261102
    assert s["query"]["function_score"]["boost_mode"] == "replace"                       # 只有 filter 的分數是 0，乘上亂數還是 0（等於沒抽樣）
    assert "post_id" in s["_source"]                                                    # 被切開的例子要帶 post_id
    lines = quality.probe_bodies(["颱風假"], extra_filter=[{"range": {"published_at": {"gte": "now/d"}}}])
    assert lines[0] == {} and lines[1]["query"]["bool"]["filter"][0] == {"match_phrase": {"content.bigram": "颱風假"}}
    assert lines[1]["aggs"]["whole"]["filter"] == {"term": {"terms_v2": "颱風假"}}
    assert lines[1]["track_total_hits"] is True                                         # 片語篇數超過 1 萬也要算準（整詞篇數是聚合，沒有上限）
    m = quality.miss_body("台北市", 20261104)                                            # 漏網文章：與 probe 同一組條件，只差 must_not 整詞
    mb = m["query"]["function_score"]["query"]["bool"]
    assert mb["filter"] == [{"match_phrase": {"content.bigram": "台北市"}}]
    assert mb["must_not"] == [{"term": {"terms_v2": "台北市"}}, {"exists": {"field": "deleted_at"}}]
    assert m["query"]["function_score"]["random_score"] == {"seed": 20261104, "field": "_seq_no"} and m["size"] == quality.MISS_SAMPLE
    assert m["query"]["function_score"]["boost_mode"] == "replace"
    assert {"post_id", "channel", "content"} <= set(m["_source"]) and quality.miss_body("x", 1, size=7)["size"] == 7
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
    yaml = pytest.importorskip("yaml")                   # 只有這個測試要 PyYAML；其他測試沒裝也照跑
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


# ---------- 整詞召回與 must_keep_split 同一條規則；被切開的例子 ----------
def test_occurrence_kind_is_the_must_keep_split_rule_on_real_jieba(tiny_dic):
    from qseg import pipeline
    cases = {"台北市長蔣萬安今天宣布": ("台北市", "overlap"), "台北市政府今天宣布": ("台北市", "whole"), "台北市立美術館": ("台北市", "inside"),
             "今天辦好市多會員": ("好市多", "split"), "鳳山區今天下雨": ("鳳山區", "split")}
    for text, (w, kind) in cases.items():
        clean, toks, terms, bad = pipeline.segment_detail(tiny_dic, "ptt", text)
        assert [k for _, k in pipeline.occurrences(clean, toks, w)] == [kind], (text, toks)
        assert (w in bad) == (kind == "split") and (w in terms) == (kind == "whole"), text
        assert pipeline.segment_checked(tiny_dic, "ptt", text) == (terms, bad)          # segment_checked 只是 segment_detail 的後兩項
    # 同一篇兩處：一處交集型歧義、一處確實被切開 → 這個詞算被切開
    toks = [("台北", 0, 2), ("市長", 2, 4), ("，", 4, 5), ("中台", 5, 7), ("北市", 7, 9)]
    assert pipeline.occurrences("台北市長，中台北市", toks, "台北市") == [(0, "overlap"), (6, "split")]
    assert pipeline.must_keep_split("台北市長，中台北市", toks, ["台北市"]) == ["台北市"]
    assert pipeline.must_keep_split("台北市長", toks[:2], ["台北市", "不在文裡"]) == []
    # 剛好跨在兩個詞中間、兩邊都往外跨：兩個都是字典詞＝鏈狀交集型歧義，不算切開（youtube「大台北市區」→ 大台北／市區）；
    # 有一邊是 HMM 猜的（不在字典）＝斷錯（instagram「搶輝達來邀功」→ 搶輝／達來，字典發版調高「輝達」詞頻之前）
    freq = {"大台北": 3, "市區": 2460, "達來": 6, "邀功": 30, "搶": 300, "台北": 0}
    toks = [("大台北", 0, 3), ("市區", 3, 5)]
    assert pipeline.occurrences("大台北市區", toks, "台北市", freq) == [(1, "overlap")]
    assert pipeline.must_keep_split("大台北市區", toks, ["台北市"], freq) == []
    assert pipeline.occurrences("大台北市區", toks, "台北市") == [(1, "split")]                    # 沒給詞頻表就不放行
    assert pipeline.occurrences("大台北市區", toks, "台北市", {"大台北": 3}) == [(1, "split")]      # 一邊不是字典詞
    toks = [("搶輝", 0, 2), ("達來", 2, 4), ("邀功", 4, 6)]
    assert pipeline.occurrences("搶輝達來邀功", toks, "輝達", freq) == [(1, "split")]
    assert pipeline.must_keep_split("搶輝達來邀功", toks, ["輝達"], freq) == ["輝達"]
    # 左邊吃進來、右邊剛好收在詞尾（辦好／市多），或碎成三段以上：兩邊都是字典詞也算切開
    assert pipeline.occurrences("今天辦好市多會員", [("今天", 0, 2), ("辦好", 2, 4), ("市多", 4, 6), ("會員", 6, 8)], "好市多",
                                {"辦好": 50, "市多": 60}) == [(3, "split")]
    toks = [("這國", 0, 2), ("立", 2, 3), ("台灣", 3, 5), ("大學生", 5, 8)]
    assert pipeline.occurrences("這國立台灣大學生", toks, "國立台灣大學", {"這國": 1, "立": 1, "台灣": 1, "大學生": 1}) == [(1, "split")]


def test_two_dictionary_words_straddle_is_not_a_split_on_real_jieba(tmp_path):
    """jieba 本體：「大台北市區」斷成 大台北／市區（兩個都是字典詞），台北市不算被切開；品質日報與漏網分類用同一個詞頻表。"""
    jieba = pytest.importorskip("jieba")
    from qseg import dictionary, pipeline
    from qseg.job import url_leaks
    (tmp_path / "base.dict").write_text("大台北 3 ns\n市區 2460 n\n台北市 95 ns\n台北 500 ns\n白天 300 t\n風雨 300 n\n", encoding="utf-8")
    tk = jieba.Tokenizer(dictionary=str(tmp_path / "base.dict"))
    tk.tmp_dir = str(tmp_path)
    tk.initialize()
    dic = dictionary.Dictionary(version="qsearch-q6-2026.10.30.2", manifest={}, tokenizer=tk, stopwords=frozenset(), soft_stopwords=frozenset(),
                                merges={}, user_words=frozenset({"台北市"}), pipeline="v2")
    clean, toks, terms, bad = pipeline.segment_detail(dic, "youtube", "白天大台北市區風雨")
    assert [t for t, _, _ in toks] == ["白天", "大台北", "市區", "風雨"] and bad == []
    assert pipeline.occurrences(clean, toks, "台北市", pipeline.dict_freq(dic)) == [(3, "overlap")]
    assert quality.miss_kind("台北市", clean, toks, terms, pipeline.dict_freq(dic)) == ("overlap", 3)
    st = quality.channel_stats([{"post_id": "yt-1", "channel": "youtube", "content": "白天大台北市區風雨", "terms_v2": terms}], dic, url_leaks,
                               pipeline.segment_detail)
    assert (st["split_docs"], st["place_docs"], st["place_split"], st["split_examples"]) == (0, 1, 0, [])


def test_miss_kind_only_split_is_a_miss(tiny_dic):
    from qseg.pipeline import segment_detail

    def kind(text, w):
        clean, toks, terms, _ = segment_detail(tiny_dic, "ptt", text)
        return quality.miss_kind(w, clean, toks, terms)
    assert kind("台北市長蔣萬安今天宣布", "台北市") == ("overlap", 0)
    assert kind("台北市立美術館", "台北市") == ("inside", 0)
    assert kind("今天辦好市多會員", "好市多") == ("split", 3)
    assert kind("台北市政府今天宣布", "台北市") == ("stale", 0)                    # 目前字典會留住：ES 的詞表是舊字典斷的或還沒斷
    assert kind("作者 x\n標題 台北市\n今天下雨", "台北市") == ("absent", None)      # PTT 標頭行在清洗時就拿掉了
    assert quality.miss_kind("市長", "台北市長", [("台北", 0, 2), ("市長", 2, 4)], []) == ("filtered", 2)   # 整詞卻沒進詞表
    assert quality.MISS_KINDS[0] == "stale" and set(quality.MISS_KINDS) == {"stale", "split", "overlap", "inside", "filtered", "absent"}


def test_whole_word_counts_only_estimated_real_splits_and_keeps_raw():
    rows = [{"word": "台北市", "phrase": 6236, "whole": 2422}, {"word": "高雄", "phrase": 1000, "whole": 990}, {"word": "颱風假", "phrase": 50, "whole": 50}]
    samples = {"台北市": {"n": 200, "kinds": {"stale": 8, "split": 2, "overlap": 170, "inside": 20}, "examples": {"split": [{"post_id": "p1"}]}, "dict_versions": {}},
               "高雄": {"n": 10, "kinds": {"split": 1, "inside": 9}, "examples": {}, "dict_versions": {}}}
    ww = quality.whole_word(rows, samples)
    t = {r["word"]: r for r in ww["worst"]}
    # 台北市：漏網 3814 篇抽 200 篇、2 篇確實被切開 → 展開估計 3814 × 2 ÷ 200 ＝ 38.1；高雄：漏網 10 篇全抽到 → 精確 1 篇
    assert (t["台北市"]["misses"], t["台北市"]["sampled"], t["台北市"]["split_est"], t["台北市"]["exact"]) == (3814, 200, 38.1, False)
    assert t["台北市"]["recall_raw"] == 0.3884 and t["台北市"]["recall"] == round(1 - 38.1 / 6236, 4) and t["台北市"]["split_examples"] == [{"post_id": "p1"}]
    assert (t["高雄"]["split_est"], t["高雄"]["exact"], t["高雄"]["recall"]) == (1.0, True, 0.999)
    assert (t["颱風假"]["misses"], t["颱風假"]["sampled"], t["颱風假"]["recall"]) == (0, 0, 1.0)
    assert ww["recall_raw"] == round((2422 + 990 + 50) / 7286, 5) and ww["recall"] == round(1 - (38.1 + 1.0) / 7286, 5)
    assert ww["words"] == 3 and ww["sampled"] == 210 and 0 < ww["se"] < 0.01 and [r["word"] for r in ww["worst"]] == ["台北市", "高雄", "颱風假"]
    # 漏網都是允許的切法 → 1.0；沒抽到樣本的詞照原始口徑全部算漏（只會偏低）
    ok = quality.whole_word(rows[:1], {"台北市": {"n": 200, "kinds": {"overlap": 200}, "examples": {}, "dict_versions": {}}})
    assert ok["recall"] == 1.0 and ok["recall_raw"] == 0.38839 and ok["se"] == 0
    partial = {r["word"]: r for r in quality.whole_word(rows, {"台北市": samples["台北市"]})["worst"]}
    assert (partial["高雄"]["sampled"], partial["高雄"]["split_est"], partial["高雄"]["recall"]) == (0, 10.0, 0.99)


def test_pick_examples_caps_and_rotates_words_place_first():
    many = [{"word": "好市多", "place": False, "post_id": i} for i in range(30)] + [{"word": "鳳山區", "place": True, "post_id": 99}, {"word": "蔣萬安", "place": False, "post_id": 98}]
    got = quality.pick_examples(many, 20)
    assert len(got) == 20 and [g["word"] for g in got[:3]] == ["鳳山區", "好市多", "蔣萬安"] and got[3] == many[1]
    assert len(quality.pick_examples(many)) == quality.EXAMPLES == 20 and quality.pick_examples([]) == []


def test_channel_stats_split_examples_on_real_jieba(tiny_dic):
    from qseg.job import url_leaks
    from qseg.pipeline import segment_detail
    docs = [{"post_id": "ig-1", "channel": "instagram", "content": "今天辦好市多會員", "terms_v2": ["今天", "辦好", "市多", "會員"]},
            {"post_id": "ig-2", "channel": "instagram", "content": "鳳山區今天下雨", "terms_v2": ["鳳山", "今天", "下雨"]},
            {"post_id": "ig-3", "channel": "instagram", "content": "台北市長蔣萬安今天宣布", "terms_v2": ["台北", "市長", "蔣萬安", "今天", "宣布"]}]
    st = quality.channel_stats(docs, tiny_dic, url_leaks, segment_detail)
    assert (st["split_docs"], st["must_keep_split"], st["place_docs"], st["place_split"]) == (2, round(2 / 3, 5), 2, 1)   # 台北／市長 允許、不算
    assert st["split_examples"] == [{"post_id": "ig-2", "word": "鳳山區", "place": True, "cut": "鳳山／區", "context": "鳳山區今天下雨"},
                                    {"post_id": "ig-1", "word": "好市多", "place": False, "cut": "辦好／市多", "context": "今天辦好市多會員"}]
    long = "前面" * 20 + "辦好市多" + "後面" * 20
    ex = quality.split_examples("x", long, [("前面", i, i + 2) for i in range(0, 40, 2)] + [("辦好", 40, 42), ("市多", 42, 44)], ["好市多"], set())
    assert ex[0]["context"] == long[31:54] and len(ex[0]["context"]) == 3 + 2 * quality.CONTEXT     # 前後各 10 字


# ---------- run_once／diagnose 走一遍（假 ES、假 PG） ----------
class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeES:
    """記下每個請求；只回答品質日報會發的唯讀查詢（_search／_msearch），片語比對用子字串近似 content.bigram。"""
    index, url = "posts", "http://es:9200"

    def __init__(self, docs):
        self.docs, self.calls = docs, []
        self.s = self

    def _phrase(self, w):
        return [d for d in self.docs if w in d["content"]]

    def _hits(self, docs, src):
        return {"hits": {"total": {"value": len(docs)}, "hits": [{"_source": {k: d[k] for k in src if k in d}} for d in docs]}}

    def req(self, method, path, body=None, **kw):
        self.calls.append((method, path.split("?")[0]))
        aggs = body.get("aggs") or {}
        if "ch" in aggs:
            return {"aggregations": {"ch": {"buckets": [{"key": c} for c in sorted({d["channel"] for d in self.docs})]}}}
        if "top" in aggs:
            return {"aggregations": {"top": {"buckets": []}}}
        if "days" in aggs:
            return {"aggregations": {"days": {"buckets": []}}}
        q = body["query"]["function_score"]["query"]["bool"]
        first = q["filter"][0]
        if "term" in first:                                    # 渠道抽樣
            ch = first["term"]["channel"]
            return self._hits([d for d in self.docs if d["channel"] == ch and "terms_v2" in d][:body["size"]], body["_source"])
        w = first["match_phrase"]["content.bigram"]             # 漏網抽樣
        return self._hits([d for d in self._phrase(w) if w not in d.get("terms_v2", [])][:body["size"]], body["_source"])

    def post(self, url, data=None, headers=None, timeout=None):  # _msearch
        self.calls.append(("POST", url.replace(self.url, "")))
        lines = [json.loads(l) for l in data.decode("utf-8").splitlines() if l.strip()]
        out = []
        for b in lines[1::2]:
            w = b["query"]["bool"]["filter"][0]["match_phrase"]["content.bigram"]
            ph = self._phrase(w)
            out.append({"hits": {"total": {"value": len(ph)}}, "aggregations": {"whole": {"doc_count": sum(1 for d in ph if w in d.get("terms_v2", []))}}})
        return _Resp({"responses": out})


class FakeConn:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        rows = [(["台北市", "好市多", "颱風假"],)] if "FROM dict_releases" in sql else []
        return type("Cur", (), {"fetchall": lambda s: rows, "fetchone": lambda s: None})()


def _live_docs(dic):
    from qseg.pipeline import segment_checked

    def doc(pid, ch, text, terms=None):
        return {"post_id": pid, "channel": ch, "content": text, "dict_version_v2": dic.version,
                "terms_v2": segment_checked(dic, ch, text)[0] if terms is None else terms}
    return [doc("ptt-1", "ptt", "台北市長蔣萬安今天宣布"), doc("ptt-2", "ptt", "台北市政府今天宣布"), doc("ptt-3", "ptt", "台北市立美術館"),
            dict(doc("ptt-4", "ptt", "台北市政府今天宣布", terms=["台北", "政府"]), dict_version_v2="qsearch-q6-2026.10.30.1"),   # 舊字典斷的
            doc("ig-1", "instagram", "今天辦好市多會員"), doc("ig-2", "instagram", "好市多", terms=["好市多"]), doc("ig-3", "instagram", "鳳山區今天下雨")]


def test_run_once_adjusts_whole_word_and_saves_split_examples(tiny_dic, monkeypatch, capsys):
    monkeypatch.setattr(quality, "MIN_DOCS", 1)
    es, conn = FakeES(_live_docs(tiny_dic)), FakeConn()
    res = quality.run_once(es, conn, dicts={"v2": tiny_dic}, gold_items=[])
    ww = res["all"]["whole_word"]
    t = {r["word"]: r for r in ww["worst"]}
    # 台北市：片語 4、整詞 1；漏網 3 篇全抽到：交集型歧義、在台北市立裡、詞表過期各一篇 → 沒有真漏
    assert (t["台北市"]["phrase"], t["台北市"]["whole"], t["台北市"]["recall_raw"], t["台北市"]["recall"]) == (4, 1, 0.25, 1.0)
    assert t["台北市"]["kinds"] == {"stale": 1, "overlap": 1, "inside": 1} and t["台北市"]["exact"] is True
    # 好市多：片語 2、整詞 1；漏網那一篇是 辦好／市多 → 真漏
    assert (t["好市多"]["split_est"], t["好市多"]["recall"]) == (1.0, 0.5) and t["好市多"]["split_examples"][0]["cut"] == "辦好／市多"
    assert "颱風假" not in t and ww["recall_raw"] == round(2 / 6, 5) and ww["recall"] == round(1 - 1 / 6, 5)
    assert read_value(metrics.QUALITY_WHOLE) == ww["recall"]
    ig = res["channels"]["instagram"]
    assert [(e["post_id"], e["word"], e["place"]) for e in ig["split_examples"]] == [("ig-3", "鳳山區", True), ("ig-1", "好市多", False), ("ig-2", "好市多", False)]
    assert res["channels"]["ptt"]["split_examples"] == [] and ig["place_split"] == 1
    assert all(path.endswith(("/_search", "/_msearch")) for _, path in es.calls)
    assert any("ADD COLUMN IF NOT EXISTS split_examples" in s for s, _ in conn.sql)
    ins = {p[1]: (s, p) for s, p in conn.sql if s.lstrip().startswith("INSERT") and p and len(p) > 2 and p[1] != "(all)"}
    s, p = ins["instagram"]
    assert "split_examples" in s and json.loads(p[-1])[0]["cut"] == "鳳山／區"
    s, p = next((s, p) for s, p in conn.sql if s.lstrip().startswith("INSERT") and "'(all)'" in s)
    worst = json.loads(p[6])
    assert {"recall_raw", "kinds", "split_est"} <= set(worst[0]) and p[4] == ww["recall"]
    logs = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    split_log = next(l for l in logs if l["event"] == "quality_splits")
    assert split_log["channel"] == "instagram" and split_log["split_docs"] == 3 and split_log["examples"][0]["word"] == "鳳山區"
    daily = next(l for l in logs if l["event"] == "quality_daily")
    assert daily["whole_word_recall"] == ww["recall"] and daily["whole_word_recall_raw"] == ww["recall_raw"] and daily["whole_word_worst"][0]["word"] == "好市多"


def test_diagnose_is_read_only_and_prints_contexts(tiny_dic, monkeypatch):
    monkeypatch.setattr(quality, "MIN_DOCS", 1)
    es, conn, lines = FakeES(_live_docs(tiny_dic)), FakeConn(), []
    metrics.QUALITY_OK.set(12345)
    metrics.QUALITY_WHOLE.set(0.5)
    res = quality.diagnose(es, tiny_dic, conn, [], 20261104, out=lines.append)
    assert read_value(metrics.QUALITY_OK) == 12345 and read_value(metrics.QUALITY_WHOLE) == 0.5          # 不更新 /metrics
    assert all(s.lstrip().upper().startswith("SELECT") for s, _ in conn.sql)                              # 只讀 PG（不建表、不寫）
    assert all(m == "POST" and path.endswith(("/_search", "/_msearch")) for m, path in es.calls)          # 只查 ES
    text = "\n".join(lines)
    assert "原始口徑 0.33333 → 調整後 0.83333" in text
    assert "-- 台北市　片語 4　整詞 1（原始 0.25）　漏網 3　抽 3 篇：stale 1、overlap 1、inside 1" in text
    assert "overlap  ptt-1（ptt）　台北／市長　「台北市長蔣萬安今天宣布」" in text
    assert "qsearch-q6-2026.10.30.1 1" in text                                                            # 詞表過期那篇是舊字典斷的
    assert "地名 鳳山區　鳳山／區　「鳳山區今天下雨」　ig-3" in text and "好市多　辦好／市多　「今天辦好市多會員」　ig-1" in text
    assert res["channels"]["instagram"]["split_docs"] == 3 and res["whole_word"]["recall"] == round(1 - 1 / 6, 5)


def test_diagnose_flag_uses_read_only_pg_and_never_starts_the_service(tiny_dic, monkeypatch, capsys):
    psycopg = pytest.importorskip("psycopg")
    monkeypatch.setattr(quality, "MIN_DOCS", 1)
    from qseg import dictionary
    from qseg import es as es_mod
    seen = {}

    class Conn(FakeConn):
        def close(self):
            seen["closed"] = True

    def connect(dsn, **kw):
        seen["kw"] = kw
        return Conn()
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(dictionary, "load", lambda **kw: seen.setdefault("load", kw) and tiny_dic)
    monkeypatch.setattr(es_mod, "ES", lambda: FakeES(_live_docs(tiny_dic)))
    monkeypatch.setattr(quality.metrics, "serve", lambda *a, **k: pytest.fail("診斷不該開 /metrics"))
    monkeypatch.setattr(quality, "run_once", lambda *a, **k: pytest.fail("診斷不該跑日報（會建表、寫 PG）"))
    assert quality.main(["--diagnose", "--seed", "20261104", "--examples", "2"]) == 0
    assert "default_transaction_read_only=on" in seen["kw"]["options"] and seen["closed"] and seen["load"] == {"pipeline": "v2"}
    lines = capsys.readouterr().out.splitlines()
    assert any("種子 20261104" in l for l in lines) and any(l.startswith("-- 台北市") for l in lines)


def test_split_examples_column_is_added_everywhere():
    ddl = "ALTER TABLE quality_daily ADD COLUMN IF NOT EXISTS split_examples jsonb"
    for text in (quality.DDL, read_text(PG / "init.sql"), read_text(PG / "migrate_p0.sql")):
        assert ddl in text
    assert quality.DDL.strip().startswith("CREATE TABLE IF NOT EXISTS quality_daily")
