"""3 分鐘 SLO 不漏報（計畫書「如果要改，建議順序」第 1 項）：
ES 逾時 (3, 180)、ES 查詢帶 timeout、失敗也記耗時與 over_180s、label 啟動時預建、
快取鍵（已結束範圍不含 ES 狀態 token）、single-flight、Alertmanager webhook 寫 live_events。
全部用 monkeypatch fake，不連 ES／PostgreSQL。"""
import json
import threading
import time

import pytest

from _p0_fakes import ensure_prometheus_client, has_series, read_value
from test_p0_api_error_reason import load_app

ensure_prometheus_client()
requests = pytest.importorskip("requests")
fastapi = pytest.importorskip("fastapi")
from fastapi import HTTPException  # noqa: E402


@pytest.fixture
def app(monkeypatch):
    mod = load_app()
    monkeypatch.setattr(mod, "dict_overlay", lambda: {"dict_version": "test", "soft": [], "merges": {}})
    monkeypatch.setattr(mod, "es_state", lambda: {"ok": True, "token": "tok-1"})
    monkeypatch.setattr(mod, "pg_rows", lambda *a, **k: [{"runs": 1, "parts": 2}])
    mod.CACHE.clear()
    mod._FLIGHTS.clear()
    mod._GEN.update(at=0.0, v="0:0")
    return mod


def hist_sum(child):
    """直方圖子序列的累計值：prometheus_client 本體 看 _sum，fake把 observe 累加在 _value。"""
    return child._sum.get() if hasattr(child, "_sum") else child._value.get()


def ok_response(took=5, timed_out=False):
    return {"took": took, "timed_out": timed_out, "hits": {"total": {"value": 3}},
            "aggregations": {"cloud": {"buckets": [{"key": "颱風", "doc_count": 2}], "doc_count_error_upper_bound": 0, "sum_other_doc_count": 0},
                             "versions": {"buckets": []}, "channels": {"buckets": []}}}


def call(app, seg="v2", **kw):
    args = dict(q="颱風", channels=["ptt"], date_from="", date_to="", size=60, soft=True, merge=True, shard_size=None, use_cache=True)
    args.update(kw)
    return app.wordcloud(seg, args["q"], args["channels"], args["date_from"], args["date_to"], args["size"], args["soft"], args["merge"],
                         args["shard_size"], args["use_cache"])


# ---------- 逾時 ----------
def test_es_default_timeout_is_connect_3s_read_sla(app, monkeypatch):
    seen = {}

    class R:
        status_code, text = 200, "{}"
        def raise_for_status(self): pass
        def json(self): return {}
    monkeypatch.setattr(app.S, "request", lambda method, url, json=None, timeout=None: seen.update(timeout=timeout) or R())
    app.es("GET", "/")
    assert seen["timeout"] == (3.0, 180.0)
    assert app.SLA_MS == 180_000


def test_wordcloud_search_body_carries_es_timeout_and_counts_partial(app, monkeypatch):
    bodies = []

    def fake_es(method, path, body=None, timeout=None):
        bodies.append(body)
        return ok_response(timed_out=True), 12.0
    monkeypatch.setattr(app, "es", fake_es)
    before = read_value(app.ERR, "v2", "partial")
    with pytest.raises(HTTPException) as ei:
        call(app)
    assert ei.value.status_code == 504
    assert bodies[0]["timeout"] == app.ES_SEARCH_TIMEOUT == "170s"
    assert read_value(app.ERR, "v2", "partial") == before + 1


# ---------- 失敗也記耗時與 over_180s ----------
def test_error_path_records_duration_and_over_180s(app, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(app.time, "perf_counter", lambda: clock["t"])

    def slow_boom(*a, **k):
        clock["t"] += 181.0      # 假裝等了 181 秒才逾時
        raise requests.exceptions.ReadTimeout("read timed out")
    monkeypatch.setattr(app, "es", slow_boom)
    over = read_value(app.OVER, "v2")
    err_sum = hist_sum(app.DUR.labels("v2", "miss", "error"))
    with pytest.raises(HTTPException) as ei:
        call(app)
    assert ei.value.status_code == 504
    assert read_value(app.OVER, "v2") == over + 1
    # 失敗也要進耗時直方圖（SLI 分母用 _count）：這次 181 秒
    assert hist_sum(app.DUR.labels("v2", "miss", "error")) == pytest.approx(err_sum + 181.0)
    assert read_value(app.ERR, "v2", "timeout") >= 1
    assert read_value(app.INFLIGHT, "v2") == 0


def test_other_errors_become_502_and_keep_http_exceptions(app, monkeypatch):
    monkeypatch.setattr(app, "es", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError("refused")))
    with pytest.raises(HTTPException) as ei:
        call(app)
    assert ei.value.status_code == 502
    monkeypatch.setattr(app, "es", lambda *a, **k: (_ for _ in ()).throw(HTTPException(503, "no index")))
    with pytest.raises(HTTPException) as ei:
        call(app)
    assert ei.value.status_code == 503


# ---------- label 預建 ----------
def test_labels_are_precreated_at_import():
    mod = load_app()
    for seg in ("v0", "v1", "v2"):
        for reason in ("timeout", "partial", "other"):
            assert has_series(mod.ERR, seg, reason), (seg, reason)
        assert has_series(mod.OVER, seg)
        for cache, status in (("hit", "ok"), ("miss", "ok"), ("miss", "error")):
            assert has_series(mod.DUR, seg, cache, status)
    for kind in mod.OPS_KINDS:
        assert has_series(mod.OPS, kind)
    assert has_series(mod.ALERTS, "firing") and has_series(mod.ALERTS, "resolved")


# ---------- 快取鍵 ----------
def test_cache_key_normalizes_query_and_omits_token_for_closed_range(app):
    a = app.cache_key("v2", "  颱風   假 ", ["ptt", "media", "ptt"], "2026-01-01", "2026-01-31", 60, True, True, None, "d1", "1:2", "tok-1")
    b = app.cache_key("v2", "颱風 假", ["media", "ptt"], "2026-01-01", "2026-01-31", 60, 1, 1, 0, "d1", "1:2", "tok-2")
    assert a == b                      # 已結束範圍：token 不同也同鍵（串流寫入不影響舊範圍）
    assert "tok-1" not in a
    c = app.cache_key("v2", "颱風 假", ["media", "ptt"], "2026-01-01", "2026-01-31", 60, 1, 1, 0, "d1", "1:3", "tok-2")
    assert c != b                      # 世代號（回補分區／定向重跑完成）一變就換鍵
    o1 = app.cache_key("v2", "颱風", [], "", "", 60, 1, 1, 0, "d1", "-", "tok-1")
    o2 = app.cache_key("v2", "颱風", [], "", "", 60, 1, 1, 0, "d1", "-", "tok-2")
    assert o1 != o2                    # 沒有結束日：token 進鍵，串流一寫入就失效
    assert app.closed_range("", "2020-01-31") and not app.closed_range("2020-01-01", "") and not app.closed_range("", "2999-12-31")


def test_closed_range_ttl_is_24h_and_open_range_300s(app, monkeypatch):
    monkeypatch.setattr(app, "es", lambda *a, **k: (ok_response(), 1.0))
    call(app, date_from="2020-01-01", date_to="2020-01-31")
    call(app)
    exps = sorted(v["exp"] - v["at"] for v in app.CACHE.values())
    assert exps == [pytest.approx(300, abs=1), pytest.approx(86400, abs=1)]
    assert app.CACHE_TTL_CLOSED_S == 86400 and app.CACHE_TTL_OPEN_S == 300


def test_second_identical_query_is_a_cache_hit(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "es", lambda *a, **k: calls.append(1) or (ok_response(), 1.0))
    a = call(app)
    b = call(app)
    assert a["cache"] == "miss" and b["cache"] == "hit" and len(calls) == 1


# ---------- single-flight ----------
def test_concurrent_identical_misses_hit_es_once(app, monkeypatch):
    gate = threading.Event()
    calls = []

    def slow_es(*a, **k):
        calls.append(1)
        gate.wait(5)
        return ok_response(), 1.0
    monkeypatch.setattr(app, "es", slow_es)
    results = []
    threads = [threading.Thread(target=lambda: results.append(call(app))) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    gate.set()
    for t in threads:
        t.join(10)
    assert len(calls) == 1
    assert sorted(r["cache"] for r in results) == ["hit", "hit", "hit", "miss"]
    assert sum(1 for r in results if r.get("coalesced")) == 3
    assert app._FLIGHTS == {}


def test_followers_recompute_when_leader_fails(app, monkeypatch):
    gate = threading.Event()
    n = {"calls": 0}

    def es_fail_first(*a, **k):
        n["calls"] += 1
        if n["calls"] == 1:
            gate.wait(5)
            raise requests.exceptions.ConnectionError("refused")
        return ok_response(), 1.0
    monkeypatch.setattr(app, "es", es_fail_first)
    out = {}

    def leader():
        try:
            call(app)
        except HTTPException as e:
            out["leader"] = e.status_code

    def follower():
        out["follower"] = call(app)["cache"]
    t1, t2 = threading.Thread(target=leader), threading.Thread(target=follower)
    t1.start(); time.sleep(0.2); t2.start(); time.sleep(0.2); gate.set(); t1.join(10); t2.join(10)
    assert out == {"leader": 502, "follower": "miss"}
    assert app._FLIGHTS == {}


# ---------- Alertmanager webhook ----------
def test_alert_webhook_writes_one_live_event_per_alert(app, monkeypatch):
    rows = []
    monkeypatch.setattr(app, "pg_exec", lambda sql, params=(): rows.append((sql, params)) or (1, None))
    payload = {"receiver": "pm-page", "status": "firing", "alerts": [
        {"status": "firing", "labels": {"alertname": "WordcloudSlo180sBurn", "severity": "page"}, "annotations": {"summary": "燒太快"},
         "startsAt": "2026-11-02T20:00:00Z", "endsAt": "0001-01-01T00:00:00Z", "fingerprint": "abc"},
        {"status": "resolved", "labels": {"alertname": "WordcloudOver180s", "severity": "ticket"}, "annotations": {}, "fingerprint": "def"}]}
    before_f, before_r = read_value(app.ALERTS, "firing"), read_value(app.ALERTS, "resolved")
    res = app.api_alert(payload)
    assert res == {"ok": True, "received": 2}
    assert read_value(app.ALERTS, "firing") == before_f + 1 and read_value(app.ALERTS, "resolved") == before_r + 1
    assert len(rows) == 2 and all("live_events" in sql for sql, _ in rows)
    kinds = [p[0] for _, p in rows]
    details = [json.loads(p[2]) for _, p in rows]
    assert kinds == ["alert", "alert"]
    assert details[0]["alertname"] == "WordcloudSlo180sBurn" and details[0]["severity"] == "page" and details[0]["status"] == "firing"
    assert details[1]["status"] == "resolved" and details[1]["fingerprint"] == "def"
    with pytest.raises(HTTPException):
        app.api_alert({"alerts": "not-a-list"})


def test_alerts_endpoint_returns_only_firing_sorted_by_severity(app, monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(app, "pg_rows", lambda sql, params=(): [
        {"at": now - timedelta(minutes=5), "detail": {"status": "firing", "severity": "ticket", "alertname": "B", "fingerprint": "1"}},
        {"at": now - timedelta(minutes=1), "detail": {"status": "resolved", "severity": "page", "alertname": "C", "fingerprint": "2"}},
        {"at": now - timedelta(minutes=50), "detail": {"status": "firing", "severity": "page", "alertname": "A", "fingerprint": "3"}},
        # Alertmanager 重啟後 resolved 不會來：最後一筆 firing 超過 ALERT_STALE_SECONDS（2 小時 10 分）就不再顯示，只計入 stale
        {"at": now - timedelta(hours=3), "detail": {"status": "firing", "severity": "page", "alertname": "D", "fingerprint": "4"}}])
    res = app.api_alerts(24)
    assert [a["alertname"] for a in res["firing"]] == ["A", "B"] and res["seen"] == 4 and res["stale"] == 1
    assert res["stale_after_seconds"] == app.ALERT_STALE_SECONDS == 2 * 3600 + 600
