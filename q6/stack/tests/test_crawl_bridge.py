"""歷史爬蟲 → 文字雲的橋（api/crawl_bridge.py）：沒有來源發文時間的行在 make_post 之前就略過（不補合成時間）；
Meta 的 post_id 換成 data/channels 樣本那一套，同一篇不會有 ig-／in- 兩份文件。
不連 ES／PostgreSQL／Pub/Sub：send、save_offset 換成測試內的記錄函式。"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REALENV = HERE.parent
sys.path.insert(0, str(REALENV / "api"))

import crawl_bridge as B  # noqa: E402

META = {"sub_type": "post", "source": "nasa", "author": "nasa", "text": "Artemis III", "collect_method": "loggedin_graphql_cursor_replay"}


def _line(**r) -> str:
    return json.dumps(r, ensure_ascii=False) + "\n"


def _rows(p: Path) -> list:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """drain 的外部依賴換成記錄：送出的 post、存的讀取位置、make_post 收到的時間。"""
    rec = {"sent": [], "saved": [], "make_post": []}
    real = B.ops.make_post

    def spy(channel, text, url=None, source=None, published_at=None, *a, **kw):
        rec["make_post"].append(published_at)
        return real(channel, text, url, source, published_at, *a, **kw)

    monkeypatch.setattr(B.ops, "make_post", spy)
    monkeypatch.setattr(B, "ROOT", tmp_path)
    monkeypatch.setattr(B, "SINCE", "2026-10-01")
    monkeypatch.setattr(B, "PAUSE", 0)
    monkeypatch.setattr(B, "send", lambda posts, pubsub: rec["sent"].extend(posts) or len(posts))
    monkeypatch.setattr(B, "save_offset", lambda path, pos, sent, skipped: rec["saved"].append((path, pos, sent, skipped)))
    return rec


def test_meta_rows_without_source_time_never_reach_make_post(tmp_path, bridge):
    f = tmp_path / "metalogin" / "deep" / "clean" / "instagram.jsonl"
    f.parent.mkdir(parents=True)
    done = "".join([
        _line(channel="instagram", post_id="in-DfHyVL-F1uT", url="https://www.instagram.com/p/DfHyVL-F1uT/", published_at="2026-10-30T22:22:29+08:00", **META),
        _line(channel="facebook", post_id="fa-1603187384509965", url=None, published_at=None, **META),     # FB 沒有 creation_time 的貼文
        _line(channel="threads", post_id="th-DfJtncBAYLE", url=None, published_at="  ", **META),
        _line(channel="instagram", post_id="in-DXmSYkXGOI9", url="https://www.instagram.com/p/DXmSYkXGOI9/", published_at="2026-04-26T22:28:33+08:00", **META),
        "{not json}\n",
        "[1, 2]\n",
    ])
    f.write_text(done + '{"channel": "instagram", "post_id": "in-half', encoding="utf-8")   # 爬蟲寫到一半的行留到下一輪
    pos = B.drain(f, B.from_meta, 0, None)
    assert [p["post_id"] for p in bridge["sent"]] == ["ig-DfHyVL-F1uT"]
    assert bridge["sent"][0]["published_at"] == "2026-10-30T22:22:29+08:00", "時間照來源，不是現在時間"
    assert bridge["make_post"] == ["2026-10-30T22:22:29+08:00", "2026-04-26T22:28:33+08:00"], "沒有時間的行不能走到 make_post（會被補成現在時間）"
    assert pos == len(done.encode("utf-8"))
    assert bridge["saved"] == [("metalogin/deep/clean/instagram.jsonl", pos, 1, 5)]   # 缺時間 2、早於 BRIDGE_SINCE 1、壞行 2


@pytest.mark.parametrize("conv,rec", [
    (B.from_ptt, {"url": "https://www.ptt.cc/bbs/Gossiping/M.1793264859.A.8CF.html", "title": "[問卦] 颱風假", "published_at": None}),
    (B.from_youtube, {"video_id": "K7IBteSskis", "title": "AI 時代", "text": "說明", "url": "https://www.youtube.com/watch?v=K7IBteSskis"}),
    (B.from_meta, {"channel": "facebook", "post_id": "fa-1", "text": "x", "published_at": ""}),
    (B.from_meta, {"channel": "instagram", "post_id": "in-x", "text": "x", "published_at": 1761800000}),   # 不是字串也不算有時間
])
def test_to_post_skips_every_converter_without_source_time(conv, rec, bridge):
    assert B.to_post(rec, conv) is None and bridge["make_post"] == []


def test_to_post_keeps_the_source_time():
    p = B.to_post({"url": "https://www.ptt.cc/bbs/Gossiping/M.1793264859.A.8CF.html", "title": "[問卦] 颱風假",
                   "published_at": "2026-10-29T17:07:39+08:00"}, B.from_ptt)
    assert p["post_id"] == "ptt-M.1793264859.A.8CF" and p["published_at"] == "2026-10-29T17:07:39+08:00"


def test_meta_post_ids_follow_the_sample_scheme():
    p = B.from_meta({"channel": "instagram", "post_id": "in-DfHyVL-F1uT", "text": "x", "published_at": "2026-10-30T22:22:29+08:00"})
    assert p["post_id"] == "ig-DfHyVL-F1uT"
    assert B.meta_post_id("facebook", "fa-1671214107707292") == "fb-1671214107707292"
    assert B.meta_post_id("threads", "th-DfHRS8rAbTy") == "DfHRS8rAbTy"
    assert B.meta_post_id("instagram", "ig-DfHyVL-F1uT") == "ig-DfHyVL-F1uT", "已經是樣本那一套就不動"
    assert B.meta_post_id("youtube", "in-abc") == "in-abc", "只換 Meta 三個渠道"


def test_crawl_copy_ids_land_on_the_sample_docs():
    """repo 裡的登入爬取副本與 data/channels 樣本：重疊的貼文換過 id 後都對上樣本那一份（IG 43、Threads 13、FB 2）。"""
    channels, crawl = REALENV / "data" / "channels", REALENV.parent / "data" / "meta-login"
    for name, crawl_file, want in (("instagram", "instagram_clean.jsonl", 43), ("threads", "threads.jsonl", 13), ("facebook", "facebook.jsonl", 2)):
        sample = {r["post_id"] for r in _rows(channels / f"{name}.jsonl")}
        mapped = {B.meta_post_id(name, r["post_id"]) for r in _rows(crawl / crawl_file)}
        assert len(sample & mapped) == want, name
        assert not any(x.startswith(B.META_IDS[name][0]) for x in mapped), name
