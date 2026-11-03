"""完整性四層對帳（計畫書 P0 第 2 項）與回補燃盡（第 8 項）：recon.py 的查詢形狀、合併與差額計算、gauge 發布。只用假物件，不連 ES／PG。"""
import json
import sys

import pytest

from _p0_fakes import SEG, ensure_prometheus_client, has_series, read_value

ensure_prometheus_client()
if str(SEG) not in sys.path:
    sys.path.insert(0, str(SEG))
recon = pytest.importorskip("qseg.recon")
metrics = pytest.importorskip("qseg.metrics")


def test_recon_body_has_channel_month_buckets_and_three_filters():
    body = recon.recon_body(22026103002)
    by_ch = body["aggs"]["by_channel"]
    assert by_ch["terms"]["field"] == "channel"
    by_m = by_ch["aggs"]["by_month"]
    assert by_m["terms"]["field"] == "month" and by_m["terms"]["missing"] == "unknown"
    sub = by_m["aggs"]
    assert sub["with_v2"] == {"filter": {"exists": {"field": "seg_ver_v2"}}}   # 空詞表的文章也算有 v2（exists 對 [] 是 false）
    assert sub["deleted"] == {"filter": {"exists": {"field": "deleted_at"}}}
    assert sub["latest"] == {"filter": {"range": {"seg_ver_v2": {"gte": 22026103002}}}}
    assert "latest" not in recon.recon_body(None)["aggs"]["by_channel"]["aggs"]["by_month"]["aggs"]


def _res():
    return {"aggregations": {"by_channel": {"buckets": [
        {"key": "ptt", "by_month": {"buckets": [
            {"key": "2026-10", "doc_count": 6260, "with_v2": {"doc_count": 6250}, "deleted": {"doc_count": 10}, "latest": {"doc_count": 6000}},
            {"key": "unknown", "doc_count": 3, "with_v2": {"doc_count": 0}, "deleted": {"doc_count": 0}, "latest": {"doc_count": 0}}]}},
        {"key": "media", "by_month": {"buckets": [
            {"key": "2026-11", "doc_count": 40, "with_v2": {"doc_count": 40}, "deleted": {"doc_count": 0}, "latest": {"doc_count": 40}}]}}]}}}


def test_rows_merge_three_layers_and_compute_missing_and_extra(tmp_path):
    es_counts = recon.parse(_res())
    assert es_counts[("ptt", "2026-10")] == {"es": 6260, "es_with_v2": 6250, "es_at_latest_v2": 6000, "es_deleted": 10}
    (tmp_path / "_manifest.json").write_text(json.dumps({"by_month": {"ptt/2026-10": 6300, "ptt/2026-09": 100}}), encoding="utf-8")
    raw = recon.load_manifest(str(tmp_path))
    assert raw == {("ptt", "2026-10"): 6300, ("ptt", "2026-09"): 100}
    ledger = {("ptt", "2026-10"): {"status": "passed", "written": 6200, "noop": 60, "raw_count": 6300}}
    rows = {(r["channel"], r["month"]): r for r in recon.rows_of(es_counts, raw, ledger, 22026103002)}
    r = rows[("ptt", "2026-10")]
    assert r["missing_ratio"] == pytest.approx((6300 - (6260 - 10)) / 6300)   # 原文有、ES（扣已刪）沒有
    assert r["extra_docs"] == 0 and r["ledger_status"] == "passed" and r["latest_seg_ver_v2"] == 22026103002
    r9 = rows[("ptt", "2026-09")]                                                 # 原文有分區、ES 完全沒有：缺 100%
    assert r9["es"] == 0 and r9["missing_ratio"] == 1.0 and r9["ledger_status"] is None
    rm = rows[("media", "2026-11")]                                               # 串流進來、原文湖沒有：missing 不算、extra 也不算（raw 未知）
    assert rm["raw_docs"] is None and rm["missing_ratio"] is None and rm["extra_docs"] is None
    assert rows[("ptt", "unknown")]["es"] == 3                                    # unknown 月份單獨一列
    assert recon.load_manifest(str(tmp_path / "nope")) == {}


def test_publish_sets_layer_gauges_and_removes_gone_partitions(tmp_path):
    es_counts = recon.parse(_res())
    rows = recon.rows_of(es_counts, {("ptt", "2026-10"): 6300}, {("ptt", "2026-10"): {"status": "passed", "written": 6200, "noop": 60}}, 1)
    recon.publish(rows)
    assert read_value(metrics.RECON_DOCS, "raw", "ptt", "2026-10") == 6300
    assert read_value(metrics.RECON_DOCS, "es", "ptt", "2026-10") == 6260
    assert read_value(metrics.RECON_DOCS, "ledger", "ptt", "2026-10") == 6260
    assert read_value(metrics.RECON_PASSED, "ptt", "2026-10") == 1
    assert not has_series(metrics.RECON_DOCS, "raw", "media", "2026-11")        # 原文層沒有就不發這個 layer
    assert not has_series(metrics.RECON_MISSING, "media", "2026-11")
    assert read_value(metrics.RECON_OK) > 0
    recon.publish([r for r in rows if r["channel"] != "media"])
    assert not has_series(metrics.RECON_DOCS, "es", "media", "2026-11")        # 消失的分區移除序列


def test_burndown_sql_mentions_the_four_ledger_signals():
    sql = recon.BURNDOWN_SQL
    assert "attempts >= %s" in sql and "status='awaiting_pm'" in sql and "checkpoint_line" in sql and "interval '15 minutes'" in sql
    assert "/ 900.0" in sql
