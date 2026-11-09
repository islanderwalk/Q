"""data/channels 六渠道樣本：每篇都有 +08:00 的 published_at，重播進 ES 後沒有 month=unknown。
IG 未登入抓不到發文時間，43 篇取自登入爬取的副本、5 篇由 shortcode 推算（collect/fill_ig_published_at.py），
逐列來源與驗證數字在 _meta/instagram_published_at.json，這裡核對它與資料一致。"""
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REALENV = HERE.parent
CHANNELS = REALENV / "data" / "channels"
CRAWL = REALENV.parent / "data" / "meta-login" / "instagram_clean.jsonl"
sys.path.insert(0, str(REALENV / "api"))

import ops  # noqa: E402

_spec = importlib.util.spec_from_file_location("fill_ig_published_at", REALENV / "collect" / "fill_ig_published_at.py")
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

ISO_TW = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")
FIELDS = ["post_id", "channel", "sub_type", "source", "url", "published_at", "fetched_at", "text", "collect_method"]


def _rows(p: Path) -> list:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _meta() -> dict:
    return json.loads((CHANNELS / "_meta" / "instagram_published_at.json").read_text(encoding="utf-8"))


def test_every_sample_has_a_published_at_so_no_doc_lands_in_month_unknown():
    posts = ops.load_channel_samples(CHANNELS)
    assert len(posts) == 267
    bad = [p["post_id"] for p in posts if not ISO_TW.match(p.get("published_at") or "")]
    assert bad == [], "同一種 +08:00 格式，load_channel_samples 用字串排序才等於時間順序"
    assert {ops.base_doc(p)["month"] for p in posts if p["channel"] == "instagram"} == {"2026-04", "2026-10"}


def test_instagram_times_match_their_recorded_source():
    ig, meta = _rows(CHANNELS / "instagram.jsonl"), _meta()
    assert len(ig) == 48 and all(list(r) == FIELDS for r in ig), "欄位固定 9 個、順序不變"
    assert [m["post_id"] for m in meta["rows"]] == [r["post_id"] for r in ig]
    crawl = {F.shortcode(r["url"]): r["published_at"] for r in _rows(CRAWL)}
    for r, m in zip(ig, meta["rows"]):
        code = F.shortcode(r["url"])
        assert r["post_id"] == f"ig-{code}" and m["published_at"] == r["published_at"]
        if m["basis"] == "loggedin_crawl":
            assert crawl[code] == r["published_at"] and -86400 < m["shortcode_delta_s"] <= 60
        else:
            assert m["basis"] == "shortcode" and code not in crawl and F.shortcode_time(code) == r["published_at"]
    assert meta["counts"] == {"loggedin_crawl": 43, "shortcode": 5} and meta["by_month"] == {"2026-04": 1, "2026-10": 47}


def test_shortcode_time_validation_numbers_are_reproducible():
    v = F.validate(_rows(CRAWL))
    assert v == _meta()["validation"]
    assert (v["rows"], v["within_60s"], v["within_1h"], v["median_s"]) == (1134, 1064, 1128, -26.3)
    assert len(v["beyond_1h_s"]) == 6 and all(x < 0 for x in v["beyond_1h_s"]), "差超過 1 小時的都是推算值比較早（先上傳、後發文）"


def test_fill_is_deterministic_and_only_touches_published_at():
    ig, crawl = _rows(CHANNELS / "instagram.jsonl"), _rows(CRAWL)
    blank = copy.deepcopy(ig)
    for r in blank:
        r["published_at"] = None
    assert [m["basis"] for m in F.fill(blank, crawl)] == [m["basis"] for m in _meta()["rows"]]
    assert blank == ig, "從 null 重補一次得到同樣的檔案"
    again = copy.deepcopy(ig)
    F.fill(again, crawl)
    assert again == ig, "已補過的不再變"
    derived = copy.deepcopy(ig[:1])
    derived[0]["published_at"] = F.shortcode_time(F.shortcode(derived[0]["url"]))
    F.fill(derived, crawl)
    assert derived == ig[:1], "還是推算值的列，副本有精確值就換成精確值"
    assert F.shortcode_time("DfBh8RqiVNH") == "2026-10-28T12:03:44+08:00"   # 登入爬取的 taken_at 是 12:04:15
    assert F.shortcode_ms("bad!") is None and F.shortcode_ms("") is None
