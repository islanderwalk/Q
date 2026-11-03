"""P0-2：ES 覆蓋檢查。查詢形狀（未斷詞、published_at 缺值、A/B 覆蓋）、解析、發佈到 gauge、失敗計數。
ES 用假物件；下面的回應是測試輸入（固定小數字），不是實際資料。"""
import sys

import pytest

from _p0_fakes import SEG, ensure_prometheus_client, has_series, read_value

ensure_prometheus_client()
sys.path.insert(0, str(SEG))

from qseg import coverage, metrics  # noqa: E402


def canned_response():
    """測試輸入：兩個渠道。ptt 有 4 篇，其中 1 篇未斷詞、1 篇缺 published_at；threads 沒有 terms_v2 的文章。"""
    return {
        "hits": {"total": {"value": 7}},
        "aggregations": {"by_channel": {"buckets": [
            {"key": "ptt", "doc_count": 4,
             "unsegmented_recent": {"doc_count": 1},
             "missing_published_at": {"doc_count": 1},
             "with_terms_v2": {"doc_count": 3}},
            {"key": "threads", "doc_count": 3,
             "unsegmented_recent": {"doc_count": 0},
             "missing_published_at": {"doc_count": 0},
             "with_terms_v2": {"doc_count": 0}},
        ]}},
    }


class FakeES:
    index = "posts"

    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error

    def req(self, method, path, body=None, **kw):
        self.calls.append((method, path, body, kw))
        if self._error:
            raise self._error
        return self._response


def test_query_shape_has_recent_window_and_exists_filters():
    body = coverage.coverage_body()
    assert body["size"] == 0
    by = body["aggs"]["by_channel"]
    assert by["terms"]["field"] == "channel"
    subs = by["aggs"]
    recent = subs["unsegmented_recent"]["filter"]["bool"]
    assert {"range": {"fetched_at": {"gte": "now-1h", "lte": "now-15m"}}} in recent["filter"]
    assert recent["must_not"] == [{"exists": {"field": "seg_ver_v2"}}]
    assert subs["missing_published_at"]["filter"]["bool"]["must_not"] == [{"exists": {"field": "published_at"}}]
    assert subs["with_terms_v2"]["filter"] == {"exists": {"field": "seg_ver_v2"}}


def test_parse_computes_ratio_and_guards_zero_denominator():
    rows = {r["channel"]: r for r in coverage.parse(canned_response())}
    assert rows["ptt"]["terms_v2_ratio"] == pytest.approx(0.75)
    assert rows["ptt"]["unsegmented_recent"] == 1
    assert rows["ptt"]["missing_published_at"] == 1
    assert rows["threads"]["terms_v2_ratio"] == 0.0
    empty = {"aggregations": {"by_channel": {"buckets": [
        {"key": "x", "doc_count": 0, "unsegmented_recent": {"doc_count": 0},
         "missing_published_at": {"doc_count": 0}, "with_terms_v2": {"doc_count": 0}}]}}}
    assert coverage.parse(empty)[0]["terms_v2_ratio"] is None


def test_run_once_publishes_gauges_and_stamps_success():
    es = FakeES(response=canned_response())
    rows = coverage.run_once(es)
    assert len(rows) == 2
    method, path, body, kw = es.calls[0]
    assert (method, path) == ("POST", "/posts/_search?request_cache=false")
    assert body == coverage.coverage_body()
    assert read_value(metrics.COV_UNSEG, "ptt") == 1
    assert read_value(metrics.COV_NO_PUB, "ptt") == 1
    assert read_value(metrics.COV_AB, "ptt") == pytest.approx(0.75)
    assert read_value(metrics.COV_DOCS, "threads") == 3
    assert read_value(metrics.COV_OK) > 0


def test_main_once_returns_nonzero_and_counts_error_when_es_fails(monkeypatch):
    from qseg import es as es_mod
    monkeypatch.setattr(es_mod, "ES", lambda *a, **k: FakeES(error=RuntimeError("POST /posts/_search -> 503")))
    before = read_value(metrics.COV_ERR)
    assert coverage.main(["--once"]) == 1
    assert read_value(metrics.COV_ERR) == before + 1


def test_main_once_success_returns_zero(monkeypatch):
    from qseg import es as es_mod
    monkeypatch.setattr(es_mod, "ES", lambda *a, **k: FakeES(response=canned_response()))
    assert coverage.main(["--once"]) == 0


def test_dropped_channel_series_is_removed_on_next_publish():
    coverage.publish([{"channel": "gone", "docs": 2, "unsegmented_recent": 0,
                       "missing_published_at": 0, "terms_v2_ratio": 1.0}])
    assert has_series(metrics.COV_DOCS, "gone")
    coverage.publish([{"channel": "ptt", "docs": 4, "unsegmented_recent": 1,
                       "missing_published_at": 1, "terms_v2_ratio": 0.75}])
    for g in (metrics.COV_DOCS, metrics.COV_UNSEG, metrics.COV_NO_PUB, metrics.COV_AB):
        assert not has_series(g, "gone")
    assert has_series(metrics.COV_DOCS, "ptt")
