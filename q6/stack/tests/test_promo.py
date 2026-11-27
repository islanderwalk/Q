"""宣傳句整行移除（seg/qseg/promo.py）、回補關卡的「宣傳句殘留」、定向重跑範圍、PM 頁待清條件、發版帶清單。
不連 ES／PostgreSQL。需要 jieba（segmenter 會載入），沒裝就整份跳過；在斷詞映像裡跑：
  PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider q6/stack/tests/test_promo.py"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
for p in (HERE.parent / "api", HERE.parent / "seg", HERE.parents[1] / "code"):
    sys.path.insert(0, str(p))

pytest.importorskip("jieba")
import ops  # noqa: E402
from qseg import promo  # noqa: E402
from qseg.normalize import normalize  # noqa: E402

V1, V2 = "qsearch-q6-2026.11.12.1", "qsearch-q6-2026.11.20.1"
LIST = [
    "# 渠道<TAB>句子<TAB>從哪一版開始收",
    "",
    f"youtube\t訂閱TVBS NEWS頻道 https://bit.ly/abc123\t{V1}",
    f"youtube\t#PNN #ptsnews\t{V1}",
    f"youtube\tTVBS NEWS\t{V1}",
    f"threads\t立即下載民視新聞APP\t{V1}",
    f"youtube\t加入會員支持我們\t{V2}",
]


# ---------- 清單格式 ----------
def test_parse_groups_by_channel_and_ignores_comments_and_blank_lines():
    rules = promo.parse(LIST)
    assert set(rules) == {"youtube", "threads"}
    assert "訂閱TVBS NEWS頻道" in rules["youtube"], "比對基準已去掉網址"
    assert "#PNN #ptsnews" in rules["youtube"], "句子本身以 # 開頭不會被當註解"
    assert promo.since_map(LIST)[V2] == {"youtube": ["加入會員支持我們"]}
    assert sum(len(v) for by in promo.since_map(LIST).values() for v in by.values()) == 5


@pytest.mark.parametrize("bad", [
    "youtube\t訂閱TVBS NEWS頻道",                       # 少第三欄
    f"youtube\t訂閱TVBS NEWS頻道\t{V1}\t多一欄",
    f"\t訂閱TVBS NEWS頻道\t{V1}",                       # 沒寫渠道
    "youtube\t訂閱TVBS NEWS頻道\t2026.11.12.1x",         # 第三欄不是字典版本
    f"youtube\t訂閱\t{V1}",                             # 太短
    f"youtube\thttps://bit.ly/abc 訂閱\t{V1}",           # 去掉網址後太短
])
def test_parse_rejects_bad_lines(bad):
    with pytest.raises(promo.PromoListError):
        promo.parse([bad])


# ---------- 只拿掉整行 ----------
def test_strip_removes_only_whole_lines_that_match_exactly():
    rules = promo.parse(LIST)
    text = "蔣萬安今天視察\n訂閱TVBS NEWS頻道 https://bit.ly/zzz999 \n記者說請訂閱TVBS NEWS頻道喔\nTVBS NEWS"
    out, n = promo.strip("youtube", text, rules)
    assert n == 2 and out == "蔣萬安今天視察\n記者說請訂閱TVBS NEWS頻道喔"
    assert "訂閱TVBS NEWS頻道" in out, "句子只是出現在別的行裡面，不動"


def test_strip_never_cuts_the_rest_of_the_text():
    rules = promo.parse(LIST)
    out, n = promo.strip("youtube", "標題\nTVBS NEWS\n後面還有正文", rules)
    assert n == 1 and out == "標題\n後面還有正文"


def test_strip_returns_the_same_string_when_nothing_matches():
    rules = promo.parse(LIST)
    text = "完全沒有宣傳句的貼文\r\n第二行"
    for ch, r in (("youtube", rules), ("ptt", rules), ("youtube", {}), ("youtube", None)):
        out, n = promo.strip(ch, text, r)
        assert n == 0 and out is text


@pytest.mark.parametrize("channel", ["ptt", "youtube", "threads", "instagram", "facebook", "media"])
def test_normalize_is_unchanged_without_rules_for_the_channel(channel):
    text = "作者 abc\n[問卦] 高雄市颱風假 https://bit.ly/x1 0912-345-678\n訂閱TVBS NEWS頻道\n--\n簽名檔 &amp; 12:30 #話題"
    base = normalize(channel, text)
    assert normalize(channel, text, None) == base and normalize(channel, text, {}) == base
    assert normalize(channel, text, {"別的渠道": frozenset({"訂閱TVBS NEWS頻道"})}) == base


def test_normalize_drops_listed_line_before_segmentation():
    rules = promo.parse(LIST)
    out = normalize("youtube", "蔣萬安視察\n訂閱TVBS NEWS頻道 https://bit.ly/abc123", rules)
    assert "訂閱" not in out and "蔣萬安" in out
    assert "訂閱" in normalize("ptt", "蔣萬安視察\n訂閱TVBS NEWS頻道", rules), "PTT 沒有規則，不動"


# ---------- 回補關卡：同一句出現在太多篇 ----------
def _counter(n_docs, n_promo, rules=None, line="訂閱東森新聞頻道"):
    c = promo.LineCounter()
    for i in range(n_docs):
        c.add("youtube", f"第 {i} 則新聞的標題內容\n" + (f"{line}\n{line}" if i < n_promo else "各自不同的內文 " + str(i)), rules)
    return c


def test_line_counter_flags_a_line_repeated_in_many_posts():
    s = _counter(1000, 60).suspects(share=0.05, min_docs=50)
    assert [x["line"] for x in s] == ["訂閱東森新聞頻道"] and s[0]["docs"] == 60, "一篇裡重複兩次只算一篇"
    assert promo.residue(s) == 60


def test_line_counter_needs_both_share_and_minimum_docs():
    assert _counter(1000, 49).suspects(share=0.05, min_docs=50) == []          # 不到 50 篇
    assert _counter(2000, 60).suspects(share=0.05, min_docs=50) == []          # 60 篇但不到 5%
    assert promo.residue([]) == 0


def test_line_counter_ignores_lines_already_on_the_list():
    rules = promo.parse([f"youtube\t訂閱東森新聞頻道\t{V1}"])
    assert _counter(1000, 600, rules).suspects(share=0.05, min_docs=50) == []


def test_gate_fails_when_promo_residue_is_present():
    job = pytest.importorskip("qseg.job")
    clean = job.g2_ratios({"phone_like": 0, "promo_residue": 0}, {}, 10000)
    dirty = job.g2_ratios({"phone_like": 0, "promo_residue": 500}, {}, 10000)
    assert job.g2_pass(clean) and not job.g2_pass(dirty)


# ---------- 找候選貼文的片語 ----------
@pytest.mark.parametrize("sentence,expect", [
    ("訂閱TVBS NEWS頻道", "訂閱"),
    ("立即下載民視新聞APP", "立即下載民視新聞"),
    ("TVBS NEWS", "tvbs news"),
    ("#PNN #ptsnews", "ptsnews"),
    ("Facebook", "facebook"),
    ("ab 訂閱 cd", "訂閱"),
    ("a b c d", "a b c d"),
    ("我 a 你 b", ""),          # 中文都是單字、英數字太短：挑不出片語
])
def test_probe_picks_longest_chinese_run_else_longest_ascii_run(sentence, expect):
    assert promo.probe(promo.key(sentence)) == expect


def test_api_probe_is_the_same_as_the_segmenter_probe():
    sentences = [l.split("\t")[1] for l in LIST if "\t" in l] + ["Facebook", "a b c d", "我 a 你 b", "訂閱  頻道   https://youtu.be/x 加入", "LINE TODAY 看更多"]
    # 沒寫 http 的網址（www.、短網址）：兩邊都要先拿掉再挑片語，否則 PM 頁數的和重跑找的不是同一批貼文
    sentences += ["Follow us bit.ly/xyz12345678", "Sign up now www.abc-shop.com/x9", "Join now t.co/x7Qabc1",
                  "Visit https://www.shop.com/a now", "Download App www.example.com", "Subscribe youtu.be/abc123 now",
                  "Follow us www.longdomainname.com", "Download the app goo.gl/abcdefgh", "SUBSCRIBE NOW BIT.LY/ABC WWW.X.COM",
                  "Don't forget to subscribe", "Don’t forget to subscribe", "It's TVBS's official channel"]
    for s in sentences:
        assert ops.promo_probe(s) == promo.probe(promo.key(s)), s


def test_probe_keeps_a_word_with_an_apostrophe_whole():
    # ES 把 don't 當一個字；片語若從撇號斷開（t forget to subscribe）就永遠比不到，那一句等於沒被重跑
    assert promo.probe(promo.key("Don't forget to subscribe")) == "don't forget to subscribe"
    assert promo.probe(promo.key("Don’t forget to subscribe")) == "don’t forget to subscribe"
    assert ops.promo_probe("Don't forget to subscribe") == "don't forget to subscribe"


def test_a_byte_order_mark_at_the_top_of_the_list_is_ignored():
    lines = ["﻿" + f"youtube\t訂閱TVBS NEWS頻道\t{V1}"]
    assert promo.parse(lines) == {"youtube": frozenset({"訂閱TVBS NEWS頻道"})}
    assert list(promo.since_map(lines)[V1]) == ["youtube"]
    flt, n = ops.promo_pending_filter(lines)
    assert n == 1 and flt["bool"]["should"][0]["bool"]["filter"][0] == {"term": {"channel": "youtube"}}


def test_resumed_batch_counts_the_part_already_written():
    # 中斷續跑：前 8000 篇有 600 篇帶同一句、後 2000 篇有 98 篇。只數後段是 98/2000＝4.9% 不到門檻，會誤放行；
    # 把前段補數回來後，要和從頭跑一次的結果一樣（698/10000）
    line = "訂閱東森新聞頻道"
    texts = [f"第 {i} 則新聞\n" + (line if (i < 600 or 8000 <= i < 8098) else f"內文 {i}") for i in range(10000)]
    full, tail, resumed = promo.LineCounter(), promo.LineCounter(), promo.LineCounter()
    for t in texts:
        full.add("youtube", t, None)
    for t in texts[8000:]:
        tail.add("youtube", t, None)
    for t in texts[:8000]:            # job.run_partition 續跑時先補數的那一段
        resumed.add("youtube", t, None)
    for t in texts[8000:]:
        resumed.add("youtube", t, None)
    assert tail.suspects() == [], "只數後段會漏（這就是要修的洞）"
    assert resumed.suspects() == full.suspects() and promo.residue(resumed.suspects()) == 698


def test_job_counts_the_written_prefix_before_resuming():
    src = (Path(__file__).resolve().parents[1] / "seg" / "qseg" / "job.py").read_text(encoding="utf-8")
    assert "for post in islice(src.iter_posts(0), start):" in src
    assert src.index("for post in islice(src.iter_posts(0), start):") < src.index("posts = src.iter_posts(start)")


# ---------- 定向重跑範圍 ＝ PM 頁「待清」條件 ----------
def _dic(version):
    return SimpleNamespace(version=version, promo_rules=promo.parse(LIST), promo_since=promo.since_map(LIST))


def test_rerun_scope_uses_each_lines_since_version_and_matches_the_pm_page_filter():
    rerun = pytest.importorskip("qseg.rerun")
    target = ops.seg_ver("v2", V2)
    clauses, n_lines, no_probe = rerun.promo_scope(_dic(V2), target)
    assert n_lines == 5 and no_probe == []
    floors = {(c["bool"]["filter"][0]["term"]["channel"], c["bool"]["filter"][1]["range"]["seg_ver_v2"]["lt"]) for c in clauses}
    assert floors == {("youtube", ops.seg_ver("v2", V1)), ("threads", ops.seg_ver("v2", V1)), ("youtube", target)}
    flt, n = ops.promo_pending_filter(LIST)
    assert n == 5 and flt == {"bool": {"should": clauses, "minimum_should_match": 1}}, "PM 頁數的和重跑找的是同一批貼文"


def test_pending_filter_never_looks_past_the_current_version():
    # 清單第三欄寫了比現在上線還新的版本（V2），現在上線的是 V1：重跑以 V1 為界，PM 頁也要以 V1 為界，
    # 不然頁面會一直顯示「還有幾篇沒清」而重跑怎麼跑都是 0 篇
    rerun = pytest.importorskip("qseg.rerun")
    target = ops.seg_ver("v2", V1)
    clauses, _, _ = rerun.promo_scope(_dic(V1), target)
    assert {c["bool"]["filter"][1]["range"]["seg_ver_v2"]["lt"] for c in clauses} == {target}
    flt, _ = ops.promo_pending_filter(LIST, V1)
    assert flt == {"bool": {"should": clauses, "minimum_should_match": 1}}
    loose, _ = ops.promo_pending_filter(LIST)
    assert loose != flt, "不給現在的版本就不設上限（舊行為），所以 API 一定要把版本傳進來"


def test_rerun_scope_all_uses_current_version_for_every_line():
    rerun = pytest.importorskip("qseg.rerun")
    target = ops.seg_ver("v2", "qsearch-q6-2026.12.01.1")
    clauses, _, _ = rerun.promo_scope(_dic("qsearch-q6-2026.12.01.1"), target, everything=True)
    assert {c["bool"]["filter"][1]["range"]["seg_ver_v2"]["lt"] for c in clauses} == {target}


def test_rerun_scope_reports_lines_that_cannot_be_searched():
    rerun = pytest.importorskip("qseg.rerun")
    lines = [f"youtube\t我 a 你 b\t{V1}"]
    dic = SimpleNamespace(version=V1, promo_rules=promo.parse(lines), promo_since=promo.since_map(lines))
    clauses, n_lines, no_probe = rerun.promo_scope(dic, ops.seg_ver("v2", V1))
    assert clauses == [] and n_lines == 1 and no_probe == ["我 a 你 b"]
    assert ops.promo_pending_filter(lines) == (None, 1)


def test_pending_filter_rejects_a_two_column_list():
    with pytest.raises(ValueError):
        ops.promo_pending_filter(["youtube\t訂閱TVBS NEWS頻道"])


# ---------- 發版：清單跟著帶到下一版 ----------
FILES = ops.DICT_FILES


def _root(tmp_path, with_promo):
    version = "qsearch-q6-2026.10.30.2"
    d = tmp_path / "dictsrv" / "versions" / version
    d.mkdir(parents=True)
    content = {"base.dict": "高雄市 31 ns\n", "user.dict": "必不可拆 50 n\n", "stopwords.txt": "的\n", "soft_stopwords.txt": "問卦\n",
               "merges.tsv": "臺北市\t台北市\n", "synonyms.txt": "高雄,打狗\n", "ik_ext.dic": "必不可拆\n", "ik_stop.dic": "的\n"}
    names = list(FILES)
    if with_promo:
        content[ops.PROMO_FILE] = "\n".join(LIST) + "\n"
        names.append(ops.PROMO_FILE)
    for name in names:
        (d / name).write_text(content[name], encoding="utf-8")
    man = {"dict_version": version, "total_pinned": 60101967, "user_words": 1, "files": {n: ops.sha256_file(d / n) for n in names}}
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "dictsrv" / "current").symlink_to(Path("versions") / version)
    return tmp_path / "dictsrv"


def test_build_release_carries_the_promo_list_unchanged(tmp_path):
    root = _root(tmp_path, with_promo=True)
    man = ops.build_release(root, ops.validate_words(["颱風假"]))
    new = root / "versions" / man["dict_version"]
    assert (new / ops.PROMO_FILE).read_text(encoding="utf-8") == "\n".join(LIST) + "\n"
    assert man["files"][ops.PROMO_FILE] == ops.sha256_file(new / ops.PROMO_FILE)
    assert promo.since_map((new / ops.PROMO_FILE).read_text(encoding="utf-8").splitlines()).keys() == {V1, V2}, "第三欄不因發版而改"


def test_build_release_without_a_promo_list_is_as_before(tmp_path):
    root = _root(tmp_path, with_promo=False)
    man = ops.build_release(root, ops.validate_words(["颱風假"]))
    assert set(man["files"]) == set(FILES) and not (root / "versions" / man["dict_version"] / ops.PROMO_FILE).exists()


@pytest.mark.parametrize("bad", [f"youtube	訂閱	{V1}", f" 	訂閱TVBS NEWS頻道	{V1}", f"youtube	https://bit.ly/abc 訂閱	{V1}"])
def test_pending_filter_rejects_what_the_loader_rejects(bad):
    # 載入字典時會被擋下的清單（句子太短、渠道空白），PM 頁也要報錯，不能照樣算出一個「還沒清幾篇」
    with pytest.raises(promo.PromoListError):
        promo.parse([bad])
    with pytest.raises(ValueError):
        ops.promo_pending_filter([bad], V1)


def test_loader_and_pm_page_agree_on_every_list():
    # 同一份清單，「載入字典」和「PM 頁數還沒清幾篇」要嘛都收、要嘛都擋；都收的話，定向重跑算版本門檻也不能炸。
    # 之前第三欄只驗了長相：13 月、沒有前綴、三位數序號，會出現一邊收一邊擋，或兩邊都收、重跑時才炸。
    import random
    from qseg import pipeline
    rnd = random.Random(20261112)
    channels = ["youtube", "ptt", "", " ", "threads"]
    texts = ["訂閱東森新聞頻道", "Subscribe to our channel", "完整版：", "https://bit.ly/abc", "看更多 https://x.co/1", "", "📣📣", "ab cd"]
    sinces = [V1, "qsearch-q6-2026.11.12.1", "qsearch-q6-2026.13.12.1", "qsearch-q6-2026.00.12.1", "qsearch-q6-2026.11.32.1",
              "qsearch-q6-2026.11.00.1", "2026.11.12.1", "qsearch-q6-2026.11.12.100", "qsearch-q6-2026.11.12", "qsearch-q6-2026.1.2.1",
              "", "latest", "qsearch-q6-2026.11.12.0", "x-2026.11.12.99"]
    extra = ["", "# 註解", "youtube\t只有兩欄", "a\tb\tc\td"]
    both_ok = both_bad = 0
    for _ in range(4000):
        lines = [rnd.choice(extra) if rnd.random() < 0.15
                 else f"{rnd.choice(channels)}\t{rnd.choice(texts)}\t{rnd.choice(sinces)}"
                 for _ in range(rnd.randint(1, 4))]
        try:
            since = promo.since_map(lines)
            loader_ok = True
        except promo.PromoListError:
            loader_ok = False
        try:
            ops.promo_pending_filter(lines, V1)
            page_ok = True
        except ValueError:
            page_ok = False
        assert loader_ok == page_ok, lines
        if loader_ok:
            both_ok += 1
            for s in since:                       # rerun.promo_scope 對每個版本都呼叫這個
                assert pipeline.seg_ver("v2", s) == ops.seg_ver("v2", s)
        else:
            both_bad += 1
    assert both_ok > 100 and both_bad > 100       # 兩種情況都真的測到了


class _FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self

    def fetchone(self):
        return None

    def close(self):
        pass


class _FakeES:
    index = "posts"

    def __init__(self, errors):
        self.errors, self.pages = errors, 0

    def count(self, q):
        return 3

    def req(self, method, path, body=None):
        if "_pit" in path:
            return {"id": "pit"}
        self.pages += 1
        hits = [{"_id": f"d{i}", "sort": [i], "_source": {"channel": "youtube", "content": "內文", "terms_v2": ["舊"]}} for i in range(3)]
        return {"hits": {"hits": hits if self.pages == 1 else []}}

    def bulk_seg(self, items, **kw):
        return {"created": 0, "updated": len(items) - self.errors, "noop": 0, "errors": self.errors, "rejected": 0}


@pytest.mark.parametrize("errors,status", [(0, "done"), (2, "failed")])
def test_cli_promo_rerun_record_follows_the_real_result(monkeypatch, errors, status):
    # 指令列的宣傳句重跑也會留一筆紀錄：有寫入失敗就要記成 failed（告警與 PM 頁都看這一欄），數字照實寫
    rerun = pytest.importorskip("qseg.rerun")
    conn = _FakeConn()
    monkeypatch.setattr(rerun.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(rerun, "segment", lambda dic, ch, text: ["新"])
    monkeypatch.setattr(rerun, "seg_fields", lambda dic, terms, text: {})
    out = rerun.run_rerun(_dic(V2), _FakeES(errors), [], limit=False, promo_mode="since")
    assert out["errors"] == errors
    sql, params = next(c for c in conn.calls if c[0].startswith("INSERT INTO rerun_runs"))
    cols = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
    row = dict(zip(cols, params))
    assert row["status"] == status and row["errors"] == errors and row["requested_by"] == "cli-promo-since"
    assert row["candidates"] == 3 and row["scanned"] == 3 and row["terms_changed"] == 3 and row["written"] == 3 - errors
    assert (row["error"] is None) == (errors == 0)
