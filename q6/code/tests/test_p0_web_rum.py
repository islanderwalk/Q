"""PM 頁修正（計畫書「其他落差」web/app.js、web/cloud.js 那一列＋P1 PM 頁 RUM）：
rum beacon 解析進指標、兩欄共用字級尺度、丟詞回傳、AbortController、簽核備註保留、告警橫幅、.n-symbol、web.yml。
前端只做文字層面的檢查（沒有瀏覽器）；/api/rum 用 rum_ingest 直接打。"""
import json

import pytest

from _p0_fakes import API, MON, REALENV, ensure_prometheus_client, read_value
from test_p0_api_error_reason import load_app

ensure_prometheus_client()
pytest.importorskip("fastapi")
from fastapi import HTTPException  # noqa: E402

WEB = REALENV / "web"


def hist_count(child):
    """直方圖子序列的觀測次數：prometheus_client 本體 的 _buckets 內部不是累積的（每筆只進一桶，收集時才累加），所以全部加總；fake把 observe 累加在 _value。"""
    return sum(b.get() for b in child._buckets) if hasattr(child, "_buckets") else child._value.get()


def test_rum_ingest_routes_events_to_metrics():
    app = load_app()
    before = {"lcp": hist_count(app.WEB_VITALS.labels("lcp")), "api": hist_count(app.WEB_API.labels("/api/compare")), "other": hist_count(app.WEB_API.labels("other")),
              "err": read_value(app.WEB_ERRORS, "error"), "drop": read_value(app.WEB_DROPPED, "v2"), "ren": read_value(app.WEB_RENDERS, "v2"),
              "mis": read_value(app.WEB_MISMATCH, "v0"), "ok": read_value(app.WEB_BEACONS, "ok")}
    events = [{"metric": "lcp", "value": 1.2}, {"metric": "api", "value": 0.4, "path": "/api/compare", "ok": True},
              {"metric": "api", "value": 0.1, "path": "/evil?x=1"}, {"metric": "error", "value": 1, "kind": "error", "msg": "x"},
              {"metric": "render", "value": 1, "seg": "v2", "dropped": 3}, {"metric": "mismatch", "value": 1, "seg": "v0"},
              {"metric": "lcp", "value": "not-a-number"}, {"metric": "unknown", "value": 1}, "junk", {"metric": "ttfc", "value": -5}]
    out = app.rum_ingest(json.dumps({"page": "/", "events": events}).encode("utf-8"))
    assert out == {"ok": True, "accepted": 6, "skipped": 4}            # 壞數值、未知 metric、非物件、ttfc −5 超出範圍：都算 skipped
    assert hist_count(app.WEB_VITALS.labels("lcp")) == before["lcp"] + 1
    assert hist_count(app.WEB_API.labels("/api/compare")) == before["api"] + 1 and hist_count(app.WEB_API.labels("other")) == before["other"] + 1   # 未知路徑收成 other，label 不爆
    assert read_value(app.WEB_ERRORS, "error") == before["err"] + 1
    assert read_value(app.WEB_DROPPED, "v2") == before["drop"] + 3 and read_value(app.WEB_RENDERS, "v2") == before["ren"] + 1
    assert read_value(app.WEB_MISMATCH, "v0") == before["mis"] + 1 and read_value(app.WEB_BEACONS, "ok") == before["ok"] + 1


def test_rum_ingest_rejects_bad_beacons():
    app = load_app()
    bad = read_value(app.WEB_BEACONS, "bad")
    with pytest.raises(HTTPException) as e:
        app.rum_ingest(b"not json")
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        app.rum_ingest(b'{"events": "x"}')
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        app.rum_ingest(b"x" * 70000)
    assert e.value.status_code == 413
    assert read_value(app.WEB_BEACONS, "bad") == bad + 3
    assert '@app.post("/api/rum")' in (API / "app.py").read_text(encoding="utf-8")


def test_web_files_have_the_fixes():
    cloud = (WEB / "cloud.js").read_text(encoding="utf-8")
    assert "opts.max ?? words[0].count" in cloud and "opts.min ?? words[words.length - 1].count" in cloud   # 兩欄共用字級尺度
    assert "return { placed: placed.length, dropped };" in cloud and "if (!ok) dropped.push(w.term);" in cloud  # 放不下的詞回傳
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "new AbortController()" in app and "runCtl.abort()" in app and "e.name === 'AbortError'" in app
    assert "if (running && fromPoll) return;" in app                              # 自動重查不打斷使用者
    assert "function keepInputs(" in app and "sig === ledgerSig" in app         # 2 秒重繪保留簽核備註
    assert "function renderAlerts(" in app and "renderAlerts(s.alerts || [])" in app
    assert "{ ...sc, palette: pal1" in app and "{ ...sc, palette: pal2" in app and "rum.firstCloud()" in app and "rum.mismatch(seg)" in app
    assert "dropped('v1', d1); dropped('v2', d2);" in app
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/rum.js"></script>' in html and html.index("rum.js") < html.index("cloud.js")
    assert 'id="alerts"' in html and 'id="drop-v1"' in html and 'id="drop-v2"' in html
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert ".cloud text.n-symbol" in css and ".alerts .al.page i" in css and ".dropped:not(:empty)" in css
    rum = (WEB / "rum.js").read_text(encoding="utf-8")
    for s in ("largest-contentful-paint", "layout-shift", "durationThreshold: 40", "navigator.sendBeacon('/api/rum'", "unhandledrejection", "window.rum ="):
        assert s in rum, s
    assert "@grafana/faro" not in rum and "import " not in rum and "<script" not in rum   # 自寫，不載入 SDK


def test_web_rules_have_sample_guards():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((MON / "rules" / "web.yml").read_text(encoding="utf-8"))
    rules = {r["alert"]: r for g in doc["groups"] for r in g["rules"]}
    assert {"WebLcpSlow", "WebInpSlow", "WebClsHigh", "WebFirstCloudSlow", "WebJsErrors", "WebDroppedWords", "WebCountMismatch"} <= set(rules)
    for name in ("WebLcpSlow", "WebInpSlow", "WebClsHigh", "WebFirstCloudSlow"):
        assert ">= 5" in rules[name]["expr"] and "histogram_quantile(0.75" in rules[name]["expr"]   # 樣本太少不評
    assert "> 2.5" in rules["WebLcpSlow"]["expr"] and "> 0.2" in rules["WebInpSlow"]["expr"] and "> 0.1" in rules["WebClsHigh"]["expr"]
