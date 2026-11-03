"""即時操作（PM 頁資料工程端）的純函式測試：不需要 ES／PostgreSQL／Pub/Sub。
執行：PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider q6/stack/tests"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))
sys.path.insert(0, str(HERE.parent / "scripts"))

import collector  # noqa: E402
import ops  # noqa: E402
import prepare_raw  # noqa: E402

FILES = ("base.dict", "user.dict", "stopwords.txt", "soft_stopwords.txt", "merges.tsv", "synonyms.txt", "ik_ext.dic", "ik_stop.dic")


# ---------- 版本 ----------
def test_seg_ver_matches_pipeline_formula():
    assert ops.seg_ver("v2", "qsearch-q6-2026.10.30.2") == 22026103002
    assert ops.seg_ver("v1", "qsearch-q6-2026.10.30.1") == 12026103001


def test_seg_ver_is_monotonic_across_days_and_two_digit_serials():
    a = ops.seg_ver("v2", "qsearch-q6-2026.10.30.10")
    b = ops.seg_ver("v2", "qsearch-q6-2026.11.01.1")
    assert a < b
    assert ops.seg_ver("v2", "qsearch-q6-2026.10.30.9") < a


def test_next_version_same_day_increments_and_new_day_restarts():
    assert ops.next_version("qsearch-q6-2026.10.30.6", today=date(2026, 10, 30)) == "qsearch-q6-2026.10.30.7"
    assert ops.next_version("qsearch-q6-2026.10.30.6", today=date(2026, 10, 31)) == "qsearch-q6-2026.10.31.1"
    assert ops.seg_ver("v2", ops.next_version("qsearch-q6-2026.10.30.6", today=date(2026, 10, 31))) > ops.seg_ver("v2", "qsearch-q6-2026.10.30.6")
    with pytest.raises(ValueError):
        ops.next_version("qsearch-q6-2026.10.30.99", today=date(2026, 10, 30))
    with pytest.raises(ValueError):
        ops.next_version("not-a-version")


# ---------- 詞條 ----------
def test_validate_words_accepts_three_forms_and_dedupes():
    out = ops.validate_words(["颱風假", {"word": "高雄市", "freq": 31, "pos": "ns"}, ["肥宅", 20], "颱風假"])
    assert out == [("颱風假", 20, "n"), ("高雄市", 31, "ns"), ("肥宅", 20, "n")]


@pytest.mark.parametrize("bad", [[], ["有 空白"], ["標點！"], ["a" * 21], [{"word": "x", "freq": "abc"}], [{"word": "x", "freq": 0}], [str(i) for i in range(51)]])
def test_validate_words_rejects(bad):
    with pytest.raises(ValueError):
        ops.validate_words(bad)


# ---------- 字典 artifact 發版 ----------
def make_root(tmp_path: Path, version="qsearch-q6-2026.10.30.2"):
    root = tmp_path / "dictsrv"
    d = root / "versions" / version
    d.mkdir(parents=True)
    content = {"base.dict": "高雄市 31 ns\n台北 100 ns\n", "user.dict": "必不可拆 50 n\n", "stopwords.txt": "的\n", "soft_stopwords.txt": "問卦\n",
               "merges.tsv": "臺北市\t台北市\n", "synonyms.txt": "高雄,打狗\n", "ik_ext.dic": "必不可拆\n", "ik_stop.dic": "的\n"}
    for name in FILES:
        (d / name).write_text(content[name], encoding="utf-8")
    man = {"dict_version": version, "note": "測試基底", "base_dict": "dict.txt.big", "total_pinned": 60101967, "user_words": 1,
           "files": {name: ops.sha256_file(d / name) for name in FILES}, "built_at": "2026-10-30T00:00:00+00:00"}
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (root / "current").symlink_to(Path("versions") / version)
    return root


def test_build_release_copies_files_adds_words_with_base_freq_floor_and_switches_current(tmp_path):
    root = make_root(tmp_path)
    words = ops.validate_words(["颱風假", {"word": "高雄市", "freq": 5, "pos": "ns"}])
    man = ops.build_release(root, words, note="測試發版", requested_by="PM")
    v = man["dict_version"]
    assert v != "qsearch-q6-2026.10.30.2" and ops.seg_ver("v2", v) > ops.seg_ver("v2", "qsearch-q6-2026.10.30.2")
    assert ops.current_version(root) == "qsearch-q6-2026.10.30.2", "build_release 不切 current"
    new = root / "versions" / v
    assert all((new / name).exists() for name in FILES)
    user = (new / "user.dict").read_text(encoding="utf-8")
    assert "必不可拆 50 n" in user and "颱風假 20 n" in user
    assert "高雄市 31 ns" in user, "使用者字典詞頻不低於主字典（5 → 31）"
    ik = (new / "ik_ext.dic").read_text(encoding="utf-8").split()
    assert set(ik) == {"必不可拆", "颱風假", "高雄市"}
    assert man["extends"] == "qsearch-q6-2026.10.30.2" and man["total_pinned"] == 60101967 and man["requested_by"] == "PM"
    assert man["files"]["user.dict"] == ops.sha256_file(new / "user.dict")
    assert (root / "releases" / f"{v}.json").exists()
    assert [x["dict_version"] for x in ops.list_versions(root)] == ["qsearch-q6-2026.10.30.2", v]
    ops.switch_current(root, v)
    assert ops.current_version(root) == v and (root / "current").is_symlink()
    with pytest.raises(FileNotFoundError):
        ops.switch_current(root, "qsearch-q6-2099.01.01.1")


def test_build_release_twice_same_day_gets_consecutive_serials(tmp_path):
    root = make_root(tmp_path)
    a = ops.build_release(root, ops.validate_words(["甲"]))
    ops.switch_current(root, a["dict_version"])
    b = ops.build_release(root, ops.validate_words(["乙"]))
    assert ops.seg_ver("v2", b["dict_version"]) == ops.seg_ver("v2", a["dict_version"]) + 1
    assert b["extends"] == a["dict_version"]
    assert "甲 20 n" in (root / "versions" / b["dict_version"] / "user.dict").read_text(encoding="utf-8"), "第二版包含第一版加的詞"


# ---------- 貼文 ----------
def test_make_post_and_base_doc():
    p = ops.make_post("ptt", "  [問卦] 颱風假  ", url="https://www.ptt.cc/bbs/Gossiping/M.1.A.1.html", source="Gossiping", published_at="2026-10-31T10:00:00+08:00")
    assert p["post_id"].startswith("live-ptt-") and p["text"] == "[問卦] 颱風假" and p["collect_method"] == "live"
    doc = ops.base_doc(p)
    assert doc["content"] == "[問卦] 颱風假" and doc["month"] == "2026-10" and "text" not in doc and set(doc) == set(ops.BASE_KEYS) | {"content", "month", "content_sha"}
    assert doc["content_sha"] == ops.content_sha("[問卦] 颱風假") and doc["author"] is None
    with pytest.raises(ValueError):
        ops.make_post("line", "x")
    with pytest.raises(ValueError):
        ops.make_post("ptt", "")
    with pytest.raises(ValueError):
        ops.make_post("ptt", "x", published_at="昨天")


# ---------- 原文湖：只收原始發文時間 ----------
def test_prepare_raw_parses_ptt_pretrain_header_and_skips_bad_time():
    text = "作者: someone (暱稱)\n看板: Gossiping\n標題: [問卦] 有沒有八卦\n時間: Fri Aug 31 00:23:47 2007\n內文: 第一行\n第二行"
    p = prepare_raw.parse_pretrain(text)
    assert p["board"] == "Gossiping" and p["title"] == "[問卦] 有沒有八卦" and p["body"] == "第一行\n第二行"
    assert p["time"].isoformat() == "2007-08-31T00:23:47+08:00"
    assert prepare_raw.parse_pretrain(text.replace("Fri Aug 31 00:23:47 2007", "unknown")) is None
    assert prepare_raw.parse_pretrain("沒有檔頭的文字") is None
    assert prepare_raw.ptt_time("Sun Jan  5 09:00:00 2014").isoformat() == "2014-01-05T09:00:00+08:00", "日期個位數會多一個空白"
    assert prepare_raw.ptt_time("Fri Aug 31 00:23:47 1980") is None


def test_prepare_raw_article_time_from_ptt_id():
    assert prepare_raw.article_time("M.1793264859.A.8CF").isoformat() == "2026-10-29T17:07:39+08:00"
    assert prepare_raw.article_time("deleted-38879-6685") is None


def test_lake_writes_real_months_only_and_dedupes(tmp_path):
    lake = prepare_raw.Lake(tmp_path / "raw", since="2010-01", max_per_source=2)
    t1 = prepare_raw.ptt_time("Fri Aug 31 00:23:47 2007")
    t2 = prepare_raw.article_time("M.1793264859.A.8CF")
    assert lake.add("src", "a", "ptt", "article", "Gossiping", None, None, "沒時間") is False
    assert lake.add("src", "b", "ptt", "article", "Gossiping", None, t1, "太舊") is False
    assert lake.add("src", "c", "ptt", "article", "Gossiping", None, t2, "新的") is True
    assert lake.add("src", "c", "ptt", "article", "Gossiping", None, t2, "重複 id") is False
    assert lake.add("src", "d", "ptt", "article", "Gossiping", None, t2, "第二篇") is True
    assert lake.add("src", "e", "ptt", "article", "Gossiping", None, t2, "超過上限") is False
    stats = lake.close()
    s = stats["sources"]["src"]
    assert (s["no_time"], s["before_since"], s["dup"], s["over_cap"], s["kept"]) == (1, 1, 1, 1, 2)
    rows = [json.loads(l) for l in (tmp_path / "raw" / "ptt" / "2026-10.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["post_id"] for r in rows] == ["c", "d"] and all(r["time_basis"] == "real" for r in rows)
    assert stats["partitions"] == 1 and (tmp_path / "raw" / "_manifest.json").exists()


# ---------- 採集器：feed 解析 ----------
ATOM_PTT = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Gossiping</title>
<entry><id>https://www.ptt.cc/bbs/Gossiping/M.1761900000.A.ABC.html</id><title>[問卦] 颱風假怎麼放</title>
<link href="https://www.ptt.cc/bbs/Gossiping/M.1761900000.A.ABC.html"/><published>2026-10-31T10:00:00+08:00</published>
<content type="html">&lt;pre&gt;明天放不放&lt;br&gt;有沒有八卦&lt;/pre&gt;</content></entry>
<entry><id>x</id><title>沒有連結</title><published>2026-10-31T10:00:00+08:00</published></entry></feed>"""
ATOM_YT = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
<entry><yt:videoId>K7IBteSskis</yt:videoId><title>AI 時代</title><link rel="alternate" href="https://www.youtube.com/watch?v=K7IBteSskis"/>
<published>2026-10-30T15:30:11+00:00</published><media:group><media:description>安全感有時比恐懼更危險</media:description></media:group></entry></feed>"""
RSS_CNA = """<?xml version="1.0"?><rss version="2.0"><channel><title>中央社</title>
<item><title>台北市長辯論會登場</title><link>https://www.cna.com.tw/news/aipl/202610300323.aspx</link>
<pubDate>Fri, 30 Oct 2026 21:48:58 +0800</pubDate><description>&lt;p&gt;（中央社記者台北30日電）辯論會明天登場&lt;/p&gt;</description></item>
<item><title>公視</title><link>https://news.pts.org.tw/article/833702</link><pubDate>Fri, 30 Oct 2026 20:00:00 +0800</pubDate><description>內容</description></item></channel></rss>"""


def test_parse_feed_ptt_atom_uses_article_id_and_strips_html():
    posts = collector.parse_feed(ATOM_PTT.encode("utf-8"), {"name": "ptt/Gossiping", "channel": "ptt", "source": "Gossiping", "url": "https://www.ptt.cc/atom/Gossiping.xml"})
    assert len(posts) == 1, "沒有文章連結的 entry 不收"
    p = posts[0]
    assert p["post_id"] == "ptt-M.1761900000.A.ABC" and p["source"] == "Gossiping" and p["sub_type"] == "article"
    assert p["text"] == "[問卦] 颱風假怎麼放\n明天放不放\n有沒有八卦" and p["published_at"] == "2026-10-31T10:00:00+08:00" and p["collect_method"] == "feed"


def test_parse_feed_youtube_atom_matches_sample_ids():
    p = collector.parse_feed(ATOM_YT.encode("utf-8"), {"name": "youtube/TVBSNEWS01", "channel": "youtube", "source": "TVBS NEWS", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=x"})[0]
    assert p["post_id"] == "K7IBteSskis" and p["url"] == "https://www.youtube.com/watch?v=K7IBteSskis" and p["sub_type"] == "video"
    assert p["published_at"] == "2026-10-30T23:30:11+08:00" and p["text"] == "AI 時代\n安全感有時比恐懼更危險" and p["source"] == "TVBS NEWS"


def test_parse_feed_rss_media_ids_match_samples():
    posts = collector.parse_feed(RSS_CNA.encode("utf-8"), {"name": "media/cna-politics", "channel": "media", "source": "cna/politics", "url": "https://feeds.feedburner.com/rsscna/politics"})
    assert [p["post_id"] for p in posts] == ["CNA/2026-10-30/202610300323", "https://news.pts.org.tw/article/833702"]
    assert posts[0]["text"] == "台北市長辯論會登場\n（中央社記者台北30日電）辯論會明天登場" and posts[0]["published_at"] == "2026-10-30T21:48:58+08:00"
    assert posts[0]["sub_type"] == "news" and ops.base_doc(posts[0])["month"] == "2026-10"


def test_parse_time_accepts_iso_and_rfc822_and_rejects_garbage():
    assert collector.parse_time("2026-10-30T15:30:11Z") == "2026-10-30T23:30:11+08:00"
    assert collector.parse_time("Fri, 30 Oct 2026 21:48:58 +0800") == "2026-10-30T21:48:58+08:00"
    assert collector.parse_time("") is None and collector.parse_time("昨天") is None


def test_skip_rules_by_prefix_channel_or_host(monkeypatch):
    monkeypatch.setattr(collector, "SKIP", ["ptt"])
    assert collector.skipped({"name": "ptt/Gossiping", "channel": "ptt", "url": "https://www.ptt.cc/atom/Gossiping.xml"})
    assert not collector.skipped({"name": "media/pts", "channel": "media", "url": "https://news.pts.org.tw/xml/newsfeed.xml"})
    monkeypatch.setattr(collector, "SKIP", ["youtube.com"])
    assert collector.skipped({"name": "youtube/x", "channel": "youtube", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=1"})


def test_feeds_json_is_well_formed():
    spec = json.loads((HERE.parent / "api" / "feeds.json").read_text(encoding="utf-8"))
    names = [f["name"] for f in spec["feeds"]]
    assert len(names) == len(set(names)) and all(f["channel"] in ops.CHANNELS and f["url"].startswith("https://") for f in spec["feeds"])
    assert all("Cookie" not in (f.get("headers") or {}) or f["name"] == "ptt/Gossiping" for f in spec["feeds"]), "只有八卦板帶年齡同意標頭"
