"""Q5 (c) 回補原型的離線測試（不連 ES、不連網）。

資料來源：
- shared/capture/fixtures/threads_graphql_sample.json：@nasa 兩串的 code／帳號／時間／內文（沒有計數欄位）。
- 計數取自 q5/evidence/a-dataflow/threads-evidence-panel.png 的實測：
  ① 35＋25＝畫面 60、分享 15；② 4＋0＝畫面 4、reshare_count 為 null；③ 1,061＋124＝畫面 1,185、分享 176。
- q5/evidence/playwright/ig_reel_fields.json：IG Reels 分頁的 play_count（12 筆）。
- make_sample.py 產生的合成資料（含舊欄位名、缺漏、重複、壞行），附獨立算出的 expected.json。
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]          # q5/code/backfill
REPO = Path(__file__).resolve().parents[4]          # repo 根目錄
FIXTURE = REPO / "shared" / "capture" / "fixtures" / "threads_graphql_sample.json"
IG_FIELDS = REPO / "q5" / "evidence" / "playwright" / "ig_reel_fields.json"
THREADS_VERIFY_JS = REPO / "q5" / "evidence" / "src" / "threads_verify.js"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import normalize as n  # noqa: E402
import backfill as bf  # noqa: E402
import make_sample as ms  # noqa: E402
import demo_checks as dc  # noqa: E402
from fake_es import FakeES, poster  # noqa: E402

T0 = 1_793_000_000_000  # crawled_at（毫秒）
FIELDS = bf.METRIC_FIELDS["threads"]
# _records 的手寫分區只有 4 筆、其中 1 筆缺容器（25%），會被分區關卡（預設 20%）擋下；
# 測其他關卡或送出端時明確放寬，分區關卡本身見 test_partition_inventory_and_unknown_rate_gate。
LOOSE = ["--max-unknown-rate", "1"]


@pytest.fixture
def fixture_items():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]


def _post(item, info=None, container="text_post_app_info", text=None):
    post = {"code": item["code"], "taken_at": item["taken_at"], "user": {"username": item["username"]},
            "caption": {"text": item["text"] if text is None else text}}
    if info is not None:
        post[container] = info
    return post


def _records(items):
    """a＝實測 ①（35＋25、分享 15）；b 抓兩次：舊的用 ① 的數字，新的用 ②（4＋0、分享 null），
    新的那次用假設的舊欄位名；另加一篇缺容器的貼文。"""
    a, b = items[0], items[1]
    return [
        {"crawled_at": T0, "post": _post(a, {"repost_count": 35, "quote_count": 25, "reshare_count": 15})},
        {"crawled_at": T0 - 1_000_000, "post": _post(b, {"repost_count": 1, "quote_count": 1, "reshare_count": 1},
                                                     text="old")},
        {"crawled_at": T0 + 100_000, "post": _post(b, {"repostCount": 4, "quote_cnt": 0, "reshare_count": None},
                                                   container="textPostAppInfo")},
        {"crawled_at": T0 + 200_000, "post": {"code": "NOCONTAINER01", "taken_at": a["taken_at"],
                                              "user": {"username": a["username"]}, "caption": {"text": "x"}}},
    ]


def _write_src(tmp_path, recs, name="batch_0001.json"):
    src = tmp_path / "src" / "2026" / "10" / "30"
    src.mkdir(parents=True, exist_ok=True)
    (src / name).write_text(json.dumps({"records": recs}, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "src"


def _bulk_pairs(out):
    pairs = []
    for f in sorted(out.glob("bulk_*.ndjson")):
        lines = f.read_text(encoding="utf-8").splitlines()
        pairs += [(json.loads(lines[i]), json.loads(lines[i + 1])) for i in range(0, len(lines), 2)]
    return pairs


def _tree_hashes(out):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}


# ---------- normalize ----------

def test_null_is_kept_not_zero():
    counts = n.normalize_counts({"text_post_app_info": {"repost_count": 4, "quote_count": 0, "reshare_count": None}})
    assert counts["reshare_count"] is None and counts["status"] == "ok"
    assert counts["unknown"] == [] and "missing:reshare_count" not in counts["flags"]


def test_screen_repost_is_repost_plus_quote_and_none_if_unknown():
    assert n.screen_repost({"repost_count": 35, "quote_count": 25}) == 60       # 實測 ①
    assert n.screen_repost({"repost_count": 1061, "quote_count": 124}) == 1185  # 實測 ③
    assert n.screen_repost({"repost_count": 35, "quote_count": None}) is None  # 缺值不當 0


def test_missing_container_gives_none_and_all_unknown():
    counts = n.normalize_counts({"code": "X"})
    assert counts["status"] == "missing_container"
    assert (counts["repost_count"], counts["quote_count"], counts["reshare_count"]) == (None, None, None)
    assert counts["unknown"] == ["repost_count", "quote_count", "reshare_count"]


def test_missing_key_is_unknown_not_null():
    counts = n.normalize_counts({"text_post_app_info": {"repost_count": 4, "quote_count": 0}})
    assert counts["unknown"] == ["reshare_count"] and "missing:reshare_count" in counts["flags"]


def test_assumed_alias_names_are_read_and_flagged():
    counts = n.normalize_counts({"textPostAppInfo": {"repostCount": 3, "quote_cnt": 0, "reshare_count": 7}})
    assert (counts["repost_count"], counts["quote_count"], counts["reshare_count"]) == (3, 0, 7)
    assert {"alias_used:container=textPostAppInfo", "alias_used:repost_count=repostCount",
            "alias_used:quote_count=quote_cnt"} <= set(counts["flags"])


def test_alias_conflict_is_flagged_and_verified_name_wins():
    counts = n.normalize_counts({"text_post_app_info": {"repost_count": 35, "repostCount": 34,
                                                        "quote_count": 25, "reshare_count": 15}})
    assert counts["repost_count"] == 35 and "alias_conflict:repost_count" in counts["flags"]


def test_display_strings_are_rejected_not_parsed():
    # 畫面「1.2 萬」不能被解析回數字；字串一律標 invalid、存成 None、列為不知道
    counts = n.normalize_counts({"text_post_app_info": {"repost_count": "1.2萬", "quote_count": 0, "reshare_count": None}})
    assert counts["repost_count"] is None and "invalid_type:repost_count" in counts["flags"]
    assert counts["unknown"] == ["repost_count"]


def test_threads_primary_names_match_evidence_script():
    # 別名表每組第一個名稱，必須是佐證腳本實際從頁面 JSON 讀的 key
    js = THREADS_VERIFY_JS.read_text(encoding="utf-8")
    assert f"node.{n.CONTAINERS[0]}" in js
    for aliases in n.ALIASES.values():
        assert f"t.{aliases[0]}" in js


def test_fixture_has_no_count_fields(fixture_items):
    # GraphQL fixture 只留 code／帳號／時間／內文，不能拿來核對計數欄位名
    for item in fixture_items:
        assert set(item) == {"code", "username", "taken_at", "text"}


def test_ig_play_count_matches_evidence():
    rows = json.loads(IG_FIELDS.read_text(encoding="utf-8"))
    assert len(rows) == 12
    for row in rows:
        assert n.IG_VIEW_ALIASES[0] in row and "video_view_count" not in row
        got = n.normalize_ig_views(row)
        assert got["video_view_count"] == row["play_count"] and got["flags"] == []
    assert {r["code"]: r["play_count"] for r in rows}["De7XCa_TJ4h"] == 4_298_087  # 縮圖 4.2M


def test_ig_legacy_name_is_assumed_alias_and_missing_is_unknown():
    got = n.normalize_ig_views({"code": "X", "video_view_count": 10})
    assert got["video_view_count"] == 10 and "alias_used:video_view_count=video_view_count" in got["flags"]
    got = n.normalize_ig_views({"code": "X"})
    assert got["status"] == "missing_field" and got["unknown"] == ["video_view_count"]


def test_ig_conflicting_keys_are_unknown_until_pm_decides():
    # play_count 與 video_view_count 可能是不同指標：同一筆兩個都有且值不同時不寫入，只記 alias_conflict
    got = n.normalize_ig_views({"code": "X", "play_count": 4_298_087, "video_view_count": 1_200_000})
    assert got["video_view_count"] is None and got["unknown"] == ["video_view_count"]
    assert "alias_conflict:video_view_count" in got["flags"]
    got = n.normalize_ig_views({"code": "X", "play_count": 10, "video_view_count": 10})  # 值相同：沒有衝突
    assert got["video_view_count"] == 10 and got["unknown"] == [] and got["flags"] == []
    # Threads 的舊名只是改名：值不同時以實抓看到的名稱為準（見 test_alias_conflict_is_flagged_and_verified_name_wins）


# ---------- 合併與 bulk ----------

def test_merge_keeps_newest_crawl_and_quality_counts(tmp_path, fixture_items):
    src = _write_src(tmp_path, _records(fixture_items))
    posts, quality, rejects, _ = bf.merge_latest(bf.iter_raw_records(src))
    a, b = fixture_items[0]["code"], fixture_items[1]["code"]
    assert set(posts) == {a, b, "NOCONTAINER01"} and rejects == []
    assert posts[b]["version"] == T0 + 100_000 and posts[b]["base"]["text"] == fixture_items[1]["text"]
    assert posts[b]["fields"] == {"repost_count": (T0 + 100_000, 4), "quote_count": (T0 + 100_000, 0),
                                  "reshare_count": (T0 + 100_000, None)}  # 新的 null 是觀測值，蓋掉舊的 1
    assert quality["duplicate_records"] == 1 and quality["missing_container"] == 1
    assert quality["null:reshare_count"] == 1 and quality["unknown:reshare_count"] == 1


def test_missing_key_in_newer_crawl_falls_back_to_older_value(tmp_path, fixture_items):
    a = fixture_items[0]
    recs = [{"crawled_at": T0, "post": _post(a, {"repost_count": 35, "quote_count": 25, "reshare_count": 15})},
            {"crawled_at": T0 + 1, "post": _post(a, {"repost_count": 36, "quote_count": 25})},  # 缺 reshare key
            {"crawled_at": T0 + 2, "post": _post(a)}]                                        # 整個容器不見
    posts, quality, _, _ = bf.merge_latest(bf.iter_raw_records(_write_src(tmp_path, recs)))
    p = posts[a["code"]]
    assert p["version"] == T0 + 2
    assert p["fields"] == {"repost_count": (T0 + 1, 36), "quote_count": (T0 + 1, 25), "reshare_count": (T0, 15)}
    assert bf.field_stamps(p) == {"quote_count": T0 + 1, "repost_count": T0 + 1, "reshare_count": T0}  # 每欄各自的時間
    assert quality["from_older_crawl:reshare_count"] == 1 and quality["from_older_crawl:repost_count"] == 1


def test_update_bulk_writes_only_known_fields_and_skips_all_unknown(tmp_path, fixture_items):
    posts, *_ = bf.merge_latest(bf.iter_raw_records(_write_src(tmp_path, _records(fixture_items))))
    out = tmp_path / "out"
    manifest = bf.write_bulk(posts, out, "threads-posts", batch_docs=500, max_bytes=5_000_000)
    assert manifest["total_docs"] == 2 and manifest["skipped_no_known_metric"] == 1  # 缺容器那篇不寫
    by_id = {meta["update"]["_id"]: body["script"]["params"] for meta, body in _bulk_pairs(out)}
    b = by_id[fixture_items[1]["code"]]
    assert b["values"] == {"quote_count": 0, "repost_count": 4, "reshare_count": None}  # null 照寫，不是 0
    assert b["obj"] == "text_post_app_info" and b["at"] == dict.fromkeys(FIELDS, T0 + 100_000)


def test_index_bulk_uses_external_version_and_keeps_null(tmp_path, fixture_items):
    posts, *_ = bf.merge_latest(bf.iter_raw_records(_write_src(tmp_path, _records(fixture_items))))
    out = tmp_path / "out"
    manifest = bf.write_bulk(posts, out, "threads-posts-v2", 500, 5_000_000, action="index")
    assert manifest["total_docs"] == 3 and manifest["version_type"] == "external"
    pairs = _bulk_pairs(out)
    for meta, _ in pairs:
        assert meta["index"]["version_type"] == "external" and meta["index"]["_index"] == "threads-posts-v2"
    docs = {d["post_id"]: d for _, d in pairs}
    assert docs[fixture_items[0]["code"]]["text_post_app_info"] == {"repost_count": 35, "quote_count": 25,
                                                                   "reshare_count": 15}
    assert docs["NOCONTAINER01"]["text_post_app_info"] == {"repost_count": None, "quote_count": None,
                                                          "reshare_count": None}
    assert docs["NOCONTAINER01"]["counts_at"] == {}  # 不知道的欄位沒有時間戳，之後爬蟲的寫入一定會套用
    assert docs[fixture_items[0]["code"]]["counts_at"] == dict.fromkeys(FIELDS, T0)


def test_batching_splits_by_doc_count_and_bytes(tmp_path):
    posts = {f"id{i}": {"version": T0 + i, "base": {"post_id": f"id{i}"}, "where": "x",
                        "fields": {"repost_count": (T0 + i, i)}} for i in range(5)}
    manifest = bf.write_bulk(posts, tmp_path / "b", "idx", batch_docs=2, max_bytes=10_000_000)
    assert [f["docs"] for f in manifest["files"]] == [2, 2, 1]
    one = len(bf.bulk_lines("id0", posts["id0"], "idx", "update").encode("utf-8"))
    manifest = bf.write_bulk(posts, tmp_path / "c", "idx", batch_docs=500, max_bytes=one * 2 + 1)
    assert [f["docs"] for f in manifest["files"]] == [2, 2, 1]
    assert not (tmp_path / "c" / "bulk_00004.ndjson").exists()


def test_partition_inventory_and_unknown_rate_gate(tmp_path, fixture_items):
    _, quality, _, parts = bf.merge_latest(bf.iter_raw_records(_write_src(tmp_path, _records(fixture_items))))
    s = parts["2026/10/30"]  # 原始檔所在的目錄就是分區
    assert s["records"] == 4 and s["unknown"] == dict.fromkeys(FIELDS, 1)  # NOCONTAINER01 三欄都不知道
    assert s["keys"]["text_post_app_info.repost_count"] == 2 and s["keys"]["textPostAppInfo.repostCount"] == 1
    assert s["keys"]["code"] == 4 and s["keys"]["user.username"] == 4  # 只列 key 名稱，不列值
    # 4 筆裡 1 筆不知道＝25%：超過 20% 就停，三欄各一條原因；等於門檻不算超過
    assert bf.gate_failures(quality, parts, 0.01, 0, 0.2) == [
        f"unknown_rate 2026/10/30 {f} 1/4=0.2500 > 0.2" for f in FIELDS]
    assert bf.gate_failures(quality, parts, 0.01, 0, 0.25) == []


# ---------- 命令列：端到端、冪等、壞資料 ----------

def test_cli_on_sample_matches_independent_expected_and_is_idempotent(tmp_path):
    exp = ms.write_sample(tmp_path, posts=300, reels=40, seed=7)
    src = tmp_path / "src"
    assert bf.main(["--src", str(src), "--out", str(tmp_path / "o1"), "--batch-docs", "100"]) == 0
    assert bf.main(["--src", str(src), "--out", str(tmp_path / "o2"), "--batch-docs", "100"]) == 0
    h1, h2 = _tree_hashes(tmp_path / "o1"), _tree_hashes(tmp_path / "o2")
    assert h1 == h2 and "bulk_00001.ndjson" in h1  # 同一批輸入 → 每個輸出檔位元組相同

    got = {meta["update"]["_id"]: body["script"]["params"]["values"] for meta, body in _bulk_pairs(tmp_path / "o1")}
    want = {code: v for code, v in exp["threads"].items() if v}
    assert got == want
    quality = json.loads((tmp_path / "o1" / "quality.json").read_text(encoding="utf-8"))["counts"]
    assert quality["rejected:bad_json_line"] == 1 and quality["dropped:bad_crawled_at"] == 1
    assert quality["skipped:platform=instagram"] == exp["records"]["instagram"]
    assert quality["records"] == exp["records"]["threads"]
    rejects = (tmp_path / "o1" / "rejects.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(r)["reason"] for r in rejects] == ["bad_json_line", "bad_crawled_at"]
    report = json.loads((tmp_path / "o1" / "quality.json").read_text(encoding="utf-8"))
    assert report["gate"]["max_unknown_rate"] == 0.2 and report["gate_failures"] == []
    weeks = [f"threads/{w}" for w in ms.WEEKS]
    assert [p for p, s in report["partitions"].items() if s["records"]] == weeks  # instagram 分區只有 skipped
    assert sum(report["partitions"][w]["records"] for w in weeks) == exp["records"]["threads"]
    assert "textPostAppInfo.repostCount" in report["partitions"]["threads/2026-05-04"]["keys"]


def test_cli_instagram_run_uses_play_count(tmp_path):
    exp = ms.write_sample(tmp_path, posts=20, reels=60, seed=3)
    out = tmp_path / "ig"
    # 實務上 GCS 依平台分 prefix；壞行不知道屬於哪個平台，混在一起跑會算進 IG 的壞行比例
    assert bf.main(["--src", str(tmp_path / "src" / "instagram"), "--out", str(out), "--platform", "instagram"]) == 0
    pairs = _bulk_pairs(out)
    assert {m["update"]["_index"] for m, _ in pairs} == {"ig-posts"}
    got = {m["update"]["_id"]: b["script"]["params"]["values"] for m, b in pairs}
    assert got == {code: v for code, v in exp["instagram"].items() if v}
    assert all(b["script"]["params"]["obj"] is None for _, b in pairs)


def test_cli_gate_stops_partition_with_unrecognized_field_names(tmp_path):
    # 一週的欄位名改成別名表沒有的名稱：那一週占全體不到兩成（98／510），但那一週是 100% 不知道，要停下來
    ms.write_sample(tmp_path, posts=300, reels=0, seed=7)
    src, out = tmp_path / "src", tmp_path / "out"
    week = src / "threads" / "2026-05-11" / "batch_0001.json"
    assert dc.rename_keys(week, {"repost_cnt": "reposts", "quote_cnt": "quotes", "reshare_cnt": "reshares"}) > 0
    assert bf.main(["--src", str(src), "--out", str(out)]) == 3
    assert not list(out.glob("bulk_*.ndjson")) and not (out / "manifest.json").exists()
    report = json.loads((out / "quality.json").read_text(encoding="utf-8"))
    assert [f.split()[1:3] for f in report["gate_failures"]] == [["threads/2026-05-11", f] for f in FIELDS]
    week_stats = report["partitions"]["threads/2026-05-11"]
    assert week_stats["unknown_rate"] == dict.fromkeys(FIELDS, 1.0)
    assert "text_post_app_info.reposts" in week_stats["keys"]  # 盤點清單看得到新名稱，補進別名表即可
    total = report["counts"]["records"]
    assert week_stats["records"] / total < 0.2  # 只看全體比例的話，門檻 20% 擋不下來
    assert bf.main(["--src", str(src), "--out", str(out), "--max-unknown-rate", "1"]) == 0  # 明確放行


def test_cli_gate_blocks_corrupt_file_and_writes_no_bulk(tmp_path, fixture_items):
    src = _write_src(tmp_path, _records(fixture_items))
    out = tmp_path / "out"
    assert bf.main(["--src", str(src), "--out", str(out), *LOOSE]) == 0 and (out / "bulk_00001.ndjson").exists()
    (src / "2026" / "10" / "30" / "batch_0002.json").write_text('{"records": [{"crawled_at": 1', encoding="utf-8")
    assert bf.main(["--src", str(src), "--out", str(out), *LOOSE]) == 3
    assert not list(out.glob("bulk_*.ndjson")) and not (out / "manifest.json").exists()  # 舊批次也清掉
    report = json.loads((out / "quality.json").read_text(encoding="utf-8"))
    assert report["gate_failures"] == ["bad_files 1 > 0"]
    assert bf.main(["--src", str(src), "--out", str(out), *LOOSE, "--max-bad-files", "1"]) == 0  # 明確放行才繼續


def test_cli_gate_blocks_high_reject_rate(tmp_path, fixture_items):
    src = _write_src(tmp_path, _records(fixture_items))
    (src / "2026" / "10" / "30" / "part.jsonl").write_text('{"post": \n{"crawled_at": "2026-10-30"}\n',
                                                         encoding="utf-8")
    out = tmp_path / "out"
    assert bf.main(["--src", str(src), "--out", str(out), *LOOSE]) == 3  # 2/6 壞掉，超過預設 1%
    report = json.loads((out / "quality.json").read_text(encoding="utf-8"))
    assert report["gate_failures"] == ["reject_rate 2/6=0.3333 > 0.01"]
    assert report["partitions"]["2026/10/30"]["rejected"] == 2  # 壞行也記在分區上
    assert bf.main(["--src", str(src), "--out", str(out), *LOOSE, "--max-reject-rate", "0.5"]) == 0


# ---------- 送出（假 ES，不連網） ----------

def _write(posts, out, index, action="update"):
    manifest = bf.write_bulk(posts, out, index, 500, 5_000_000, action=action)
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _seeded_es(posts, index="threads-posts", online=None):
    """線上 index 已有這些文件（沒有計數）；online 可指定某篇已被爬蟲寫過較新的值。"""
    docs = {(index, code): {"_source": {"post_id": code}, "_version": 1} for code in posts}
    for code, src in (online or {}).items():
        docs[(index, code)]["_source"].update(src)
    return FakeES(docs)


def test_send_update_then_rerun_is_all_noop_and_online_newer_wins(tmp_path, fixture_items):
    posts, *_ = bf.merge_latest(bf.iter_raw_records(_write_src(tmp_path, _records(fixture_items))))
    a, b = fixture_items[0]["code"], fixture_items[1]["code"]
    online = {"repost_count": 99, "quote_count": 9, "reshare_count": None}  # 爬蟲在 T0+9 寫過三欄
    es = _seeded_es([a], online={a: {"text_post_app_info": dict(online), "counts_at": dict.fromkeys(FIELDS, T0 + 9)}})
    out = tmp_path / "out"
    _write(posts, out, "threads-posts")
    sleeps = []
    stats = bf.send_bulk_files(out, "http://es.test:9200", 2.0, post=poster(es), sleep=sleeps.append)
    assert stats["noop"] == 1 and stats["not_in_index"] == 1 and stats["files_sent"] == 1  # b 不在 index
    assert es.docs[("threads-posts", a)]["_source"]["text_post_app_info"] == online  # 三欄線上都較新，不蓋
    assert sleeps == [0.5]
    errors = (out / "send_errors.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(errors[0])["error"] == "document_missing_exception"

    es2 = _seeded_es([a, b])
    stats = bf.send_bulk_files(out, "http://other:9200", 0, post=poster(es2), sleep=lambda s: None)
    assert stats["ok"] == 2
    assert es2.docs[("threads-posts", b)]["_source"]["text_post_app_info"]["reshare_count"] is None
    (out / "sent.json").unlink()
    stats = bf.send_bulk_files(out, "http://other:9200", 0, post=poster(es2), sleep=lambda s: None)
    assert stats["noop"] == 2 and stats["ok"] == 0  # 帳本清掉後整批重送：全部 noop


def _mixed_crawls(tmp_path, a, crawls):
    """同一篇的多次抓取寫成一個分區，合併後產生 update bulk，回傳輸出目錄。"""
    recs = [{"crawled_at": t, "post": _post(a, info)} for t, info in crawls]
    posts, *_ = bf.merge_latest(bf.iter_raw_records(_write_src(tmp_path, recs)))
    out = tmp_path / "out"
    _write(posts, out, "threads-posts")
    return out


def test_guard_is_per_field_so_older_value_never_overwrites_newer(tmp_path, fixture_items):
    # 線上：爬蟲在 T2 寫了 repost 20。回補輸入缺 T2 那次抓取，只有 T1（三欄）與 T3（缺 repost key）。
    # 合併後 repost 取自 T1、另外兩欄取自 T3；整篇只有一個時間戳時，T3 會讓 T1 的舊 repost 10 蓋掉 20。
    a = fixture_items[0]
    t1, t2, t3 = T0, T0 + 1_000, T0 + 2_000
    out = _mixed_crawls(tmp_path, a, [(t1, {"repost_count": 10, "quote_count": 1, "reshare_count": 1}),
                                      (t3, {"quote_count": 3, "reshare_count": None})])
    params = _bulk_pairs(out)[0][1]["script"]["params"]
    assert params["values"] == {"quote_count": 3, "repost_count": 10, "reshare_count": None}
    assert params["at"] == {"quote_count": t3, "repost_count": t1, "reshare_count": t3}
    es = _seeded_es([a["code"]], online={a["code"]: {"text_post_app_info": {"repost_count": 20},
                                                     "counts_at": {"repost_count": t2}}})
    assert bf.send_bulk_files(out, "http://es.test", 0, post=poster(es), sleep=lambda s: None)["ok"] == 1
    src = es.docs[("threads-posts", a["code"])]["_source"]
    assert src["text_post_app_info"] == {"repost_count": 20, "quote_count": 3, "reshare_count": None}
    assert src["counts_at"] == {"repost_count": t2, "quote_count": t3, "reshare_count": t3}
    (out / "sent.json").unlink()
    assert bf.send_bulk_files(out, "http://es.test", 0, post=poster(es), sleep=lambda s: None)["noop"] == 1


def test_guard_older_run_sent_later_fills_only_fields_still_missing(tmp_path, fixture_items):
    # 分週跑、較新的一週先送：較新那週缺 reshare key，較舊那週有。較舊那週送出時只補 reshare，其他兩欄不動。
    a = fixture_items[0]
    newer = _mixed_crawls(tmp_path / "w2", a, [(T0 + 7, {"repost_count": 40, "quote_count": 30})])
    older = _mixed_crawls(tmp_path / "w1", a, [(T0, {"repost_count": 35, "quote_count": 25, "reshare_count": 15})])
    es = _seeded_es([a["code"]])
    for out in (newer, older):
        assert bf.send_bulk_files(out, "http://es.test", 0, post=poster(es), sleep=lambda s: None)["ok"] == 1
    src = es.docs[("threads-posts", a["code"])]["_source"]
    assert src["text_post_app_info"] == {"repost_count": 40, "quote_count": 30, "reshare_count": 15}
    assert src["counts_at"] == {"repost_count": T0 + 7, "quote_count": T0 + 7, "reshare_count": T0}


def test_guard_fails_loudly_when_online_stamp_is_not_epoch_millis(tmp_path, fixture_items):
    # 爬蟲若把 counts_at 寫成 ISO 字串，腳本丟錯：這筆 400、記為失敗、檔案不記帳，文件不變，不默默略過
    a = fixture_items[0]
    out = _mixed_crawls(tmp_path, a, [(T0, {"repost_count": 35, "quote_count": 25, "reshare_count": 15})])
    es = _seeded_es([a["code"]], online={a["code"]: {"counts_at": {"repost_count": "2026-10-30T00:00:00Z"}}})
    stats = bf.send_bulk_files(out, "http://es.test", 0, post=poster(es), sleep=lambda s: None)
    assert stats["failed"] == 1 and stats["files_incomplete"] == 1 and "files_sent" not in stats
    err = json.loads((out / "send_errors.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert (err["status"], err["error"]) == (400, "illegal_argument_exception")
    assert "text_post_app_info" not in es.docs[("threads-posts", a["code"])]["_source"]
    assert "counts_at.' + f + ' is not epoch_millis" in bf.GUARD_SCRIPT  # 腳本裡有同樣的檢查


def test_send_index_mode_counts_409_as_applied(tmp_path, fixture_items):
    posts, *_ = bf.merge_latest(bf.iter_raw_records(_write_src(tmp_path, _records(fixture_items))))
    out = tmp_path / "out"
    _write(posts, out, "threads-posts-v2", action="index")
    es = FakeES()
    assert bf.send_bulk_files(out, "http://a", 0, post=poster(es), sleep=lambda s: None)["ok"] == 3
    stats = bf.send_bulk_files(out, "http://b", 0, post=poster(es), sleep=lambda s: None)  # 換位址＝新帳本
    assert stats["version_conflict_skipped"] == 3 and stats["failed"] == 0


def test_send_retries_only_rejected_items_with_backoff(tmp_path, fixture_items):
    posts, *_ = bf.merge_latest(bf.iter_raw_records(_write_src(tmp_path, _records(fixture_items))))
    out = tmp_path / "out"
    _write(posts, out, "threads-posts")
    a, b = fixture_items[0]["code"], fixture_items[1]["code"]
    es = _seeded_es([a, b])
    es.http_429_times, es.reject_items_once = 1, {b}
    sleeps = []
    stats = bf.send_bulk_files(out, "http://es.test", 0, post=poster(es), sleep=sleeps.append, rand=lambda: 0.0)
    assert stats["requests"] == 3 and stats["http_retry_429"] == 1 and stats["retried_items"] == 3
    assert stats["ok"] == 2 and stats["files_sent"] == 1
    assert sleeps[:2] == [1.0, 2.0]  # 2 秒、4 秒的一半（rand=0）


def test_send_item_failure_keeps_file_unsent_and_exit_4(tmp_path, fixture_items, monkeypatch):
    src = _write_src(tmp_path, _records(fixture_items))
    a, b = fixture_items[0]["code"], fixture_items[1]["code"]
    es = _seeded_es([a, b])
    es.bad_ids = {a}
    monkeypatch.setattr(bf, "send_bulk_files",
                        lambda out, url, rate, _f=bf.send_bulk_files: _f(out, url, 0, post=poster(es),
                                                                          sleep=lambda s: None))
    out = tmp_path / "out"
    assert bf.main(["--src", str(src), "--out", str(out), *LOOSE, "--send", "http://es.test"]) == 4
    assert not (out / "sent.json").exists() or json.loads((out / "sent.json").read_text())["http://es.test"] == {}
    es.bad_ids = set()  # 修好之後重跑：已套用的 b 是 noop，a 寫入
    assert bf.main(["--src", str(src), "--out", str(out), *LOOSE, "--send", "http://es.test"]) == 0
    assert es.docs[("threads-posts", a)]["_source"]["text_post_app_info"]["repost_count"] == 35


# ---------- 示範工具 ----------

def test_make_sample_is_deterministic(tmp_path):
    ms.write_sample(tmp_path / "a", posts=50, reels=10, seed=11)
    ms.write_sample(tmp_path / "b", posts=50, reels=10, seed=11)
    files = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*") if p.is_file())
    assert len(files) == 6  # 5 個原始檔＋expected.json
    for rel in files:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()


def test_demo_send_seed_and_expected_state():
    import demo_send as ds

    expected = {"S5T000000": {"repost_count": 1}, "S5T000001": {"repost_count": 2},
                "S5T000002": {"repost_count": 3, "quote_count": 4}, "S5T000003": {}}
    docs, groups = ds.seed(expected)
    assert groups == {"missing": {"S5T000000"}, "newer": {"S5T000001"}, "partial": {"S5T000002"}}
    assert len(docs) == 3
    assert ds.want_after("S5T000001", expected["S5T000001"], groups) == ds.CRAWLER  # 三欄線上都較新
    assert ds.want_after("S5T000002", expected["S5T000002"], groups) == {"repost_count": 999, "quote_count": 4}
    es = FakeES(docs)
    assert ds.mismatches(es, expected, groups) == 1  # 回補前：partial 那篇還沒有 quote；000003 沒有計數，None 對 None
    es.docs[(ds.INDEX, "S5T000002")]["_source"]["text_post_app_info"]["quote_count"] = 4
    assert ds.mismatches(es, expected, groups) == 0


def test_demo_checks_helpers(tmp_path):
    jl = tmp_path / "p.jsonl"
    rec = {"crawled_at": T0, "post": {"code": "X", "text_post_app_info": {"repost_count": 1, "quote_count": 2}}}
    jl.write_text("\n".join(json.dumps(rec) for _ in range(20)) + "\n", encoding="utf-8")
    assert dc.rename_keys(jl, {"repost_count": "reposts"}) == 20
    assert json.loads(jl.read_text(encoding="utf-8").splitlines()[0])["post"]["text_post_app_info"] == {
        "quote_count": 2, "reposts": 1}
    assert dc.truncate_lines(jl, every=10) == 2  # 第 1、11 行
    src = jl.parent / "src"
    src.mkdir()
    jl.rename(src / "p.jsonl")
    _, _, rejects, _ = bf.merge_latest(bf.iter_raw_records(src))
    assert [r["where"] for r in rejects] == ["p.jsonl:1", "p.jsonl:11"]


def test_demo_checks_compare_counts_missing_and_extra(tmp_path):
    exp = tmp_path / "expected.json"
    exp.write_text(json.dumps({"threads": {"A": {"repost_count": 1}, "B": {}, "C": {"quote_count": 2}}}))
    posts = {"A": {"version": T0, "base": {}, "where": "x", "fields": {"repost_count": (T0, 1)}},
             "B": {"version": T0, "base": {}, "where": "x", "fields": {"repost_count": (T0, 5)}}}
    bf.write_bulk(posts, tmp_path / "out", "idx", 500, 5_000_000)
    assert dc.compare(tmp_path / "out", exp) == (2, 2, 2)  # B 不該寫、C 沒寫到
