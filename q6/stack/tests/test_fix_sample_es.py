"""scripts/fix_sample_es.py：已在 ES 的樣本文件補時間、成對的爬蟲那份軟刪除、照存下的現值復原。用假的 ES（記憶體裡的 dict），不連線。"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import fix_sample_es as X  # noqa: E402


class FakeES:
    """只認 fix_sample_es 會打的四種請求：_mget、month=unknown 聚合、合成時間查詢、_bulk 部分更新。"""

    def __init__(self, docs, synthetic=()):
        self.docs, self.synthetic, self.bulks = docs, list(synthetic), []

    def __call__(self, method, path, body=None):
        if "/_mget" in path:
            return {"docs": [{"_id": i, "found": True, "_source": self.docs[i]} if i in self.docs else {"_id": i, "found": False} for i in body["ids"]]}
        if path.startswith("/_bulk"):
            lines = body.decode("utf-8").splitlines()
            self.bulks.append(lines)
            for head, doc in zip(lines[::2], lines[1::2]):
                self.docs[json.loads(head)["update"]["_id"]].update(json.loads(doc)["doc"])
            return {"items": [{"update": {"_id": json.loads(h)["update"]["_id"], "result": "updated"}} for h in lines[::2]]}
        if "aggs" in body:
            n = sum(1 for d in self.docs.values() if d.get("month") == "unknown")
            return {"aggregations": {"c": {"buckets": [{"key": "instagram", "doc_count": n}] if n else []}}}
        return {"hits": {"hits": [{"_id": i} for i in self.synthetic]}}


@pytest.fixture
def stack(monkeypatch):
    """跑在 ES 上的現況：48 篇 ig- 沒有時間（month unknown）；其中 3 篇有 in- 雙胞胎（2 篇內容相同、1 篇改過字），
    1 篇 in- 已經刪過；另有 1 篇舊版橋補了合成時間的 fa- 文件。"""
    ig = X.rows("instagram")
    docs = {r["post_id"]: {"published_at": None, "month": "unknown", "content_sha": f"sha-{r['post_id'][3:]}", "seg_ver_v2": 22026103002} for r in ig}
    codes = [r["post_id"][3:] for r in ig]
    docs[f"in-{codes[0]}"] = {"published_at": "2026-04-26T22:28:33+08:00", "month": "2026-04", "content_sha": f"sha-{codes[0]}"}
    docs[f"in-{codes[1]}"] = {"published_at": "2026-10-30T17:35:59+08:00", "month": "2026-10", "content_sha": f"sha-{codes[1]}"}
    docs[f"in-{codes[2]}"] = {"published_at": "2026-10-30T16:05:27+08:00", "month": "2026-10", "content_sha": "edited"}
    docs[f"in-{codes[3]}"] = {"published_at": "2026-10-30T15:01:57+08:00", "month": "2026-10", "content_sha": f"sha-{codes[3]}", "deleted_at": "2026-11-04T00:00:00Z"}
    docs["fa-1265822015576361"] = {"published_at": "2026-11-03T22:00:00+08:00", "month": "2026-11", "content_sha": "x"}
    fake = FakeES(docs, synthetic=["fa-1265822015576361"])
    monkeypatch.setattr(X, "es", fake)
    return fake, codes


def test_pairs_follow_the_bridge_id_mapping():
    p = dict((d, k) for k, d in X.pairs())
    assert len(p) == 48 + 4 + 61
    assert p["in-DXmSYkXGOI9"] == "ig-DXmSYkXGOI9" and p["fa-1535025325322694"] == "fb-1535025325322694" and p["th-DfHRS8rAbTy"] == "DfHRS8rAbTy"


def test_check_is_read_only(stack, capsys):
    fake, _ = stack
    assert X.main(["x", "check"]) == 0 and fake.bulks == []
    out = json.loads(capsys.readouterr().out)
    assert out["month_unknown_by_channel"] == {"instagram": 48} and out["ig_without_published_at"] == 48 and out["ig_without_seg_ver_v2"] == 0
    assert out["pairs_in_es"] == {"in-": 4, "fa-": 0, "th-": 0} and out["synthetic_time_docs"] == ["fa-1265822015576361"]
    assert out["planned_time_updates"] == 48 and len(out["planned_soft_deletes"]) == 2


def test_apply_saves_before_state_then_rollback_restores_it(stack, tmp_path, capsys):
    fake, codes = stack
    before = tmp_path / "before.json"
    orig = json.loads(json.dumps(fake.docs))
    assert X.main(["x", "apply", "--before", str(before)]) == 0
    d = fake.docs
    assert d["ig-DXmSYkXGOI9"]["published_at"] == "2026-04-26T22:28:33+08:00" and d["ig-DXmSYkXGOI9"]["month"] == "2026-04"
    assert sum(1 for k, v in d.items() if k.startswith("ig-") and v["month"] == "2026-10") == 47
    assert all("terms_v2" not in v and v["seg_ver_v2"] == 22026103002 for k, v in d.items() if k.startswith("ig-")), "斷詞欄位不動"
    assert d[f"in-{codes[0]}"]["deleted_at"] and d[f"in-{codes[1]}"]["deleted_at"]
    assert "deleted_at" not in d[f"in-{codes[2]}"], "內容不同的不刪，留給人看"
    assert d[f"in-{codes[3]}"]["deleted_at"] == "2026-11-04T00:00:00Z" and "deleted_at" not in d["fa-1265822015576361"], "沒加 --synthetic 不動合成時間的文件"
    n = len(fake.bulks)
    assert X.main(["x", "apply", "--before", str(before)]) == 0 and len(fake.bulks) == n, "再跑一次沒有要改的，不送 _bulk"
    assert X.main(["x", "rollback", "--before", str(before)]) == 0
    assert {k: {f: v.get(f) for f in X.FIELDS} for k, v in d.items()} == {k: {f: v.get(f) for f in X.FIELDS} for k, v in orig.items()}
    with pytest.raises(SystemExit):
        X.main(["x", "apply", "--before", str(before)])                # 現值檔還在：不覆蓋上一次存的現值


def test_apply_synthetic_soft_deletes_the_listed_docs_too(stack, tmp_path):
    fake, _ = stack
    assert X.main(["x", "apply", "--synthetic", "--before", str(tmp_path / "b.json")]) == 0
    assert fake.docs["fa-1265822015576361"]["deleted_at"]
    saved = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    assert saved["fa-1265822015576361"] == {"deleted_at": None} and saved["ig-DXmSYkXGOI9"] == {"published_at": None, "month": "unknown"}
