"""PM 頁「查不到文字雲：Failed to fetch」一直掛著的修正：
- 後端 /api/compare 兩欄同時查，合計有上限（超過回 JSON 504，不讓連線一直掛著）；
- 前端沒查到（連線中斷、API 回錯、逾時）會自動重查，不必等資料再變或使用者再按；查詢中資料又變了也不會漏掉。
後端用假的 wordcloud，不連 ES；前端只做文字層面的檢查（沒有瀏覽器）。"""
import threading
import time

import pytest

from _p0_fakes import REALENV, ensure_prometheus_client
from test_p0_api_error_reason import load_app

ensure_prometheus_client()
pytest.importorskip("fastapi")
from fastapi import HTTPException  # noqa: E402

ARGS = dict(a="v0", b="v2", q="", channels="", date_from="", date_to="", size=60, soft=1, merge=1)


def _cloud(seg):
    return {"buckets": [{"term": f"{seg}詞", "count": 1}], "total_docs": 1, "state_token": "t"}


def test_compare_runs_both_columns_at_the_same_time(monkeypatch):
    app = load_app()
    gate = threading.Barrier(2, timeout=5)   # 兩欄同時在算才過得去；一欄查完才查另一欄會在這裡等到逾時

    def wordcloud(seg, *a, **k):
        gate.wait()
        return _cloud(seg)

    monkeypatch.setattr(app, "wordcloud", wordcloud)
    r = app.api_compare(**ARGS)
    assert [x["term"] for x in r["a"]["buckets"]] == ["v0詞"] and [x["term"] for x in r["b"]["buckets"]] == ["v2詞"]
    assert [x["term"] for x in r["only_a"]] == ["v0詞"] and [x["term"] for x in r["only_b"]] == ["v2詞"]


def test_compare_over_deadline_returns_504_instead_of_hanging(monkeypatch):
    app = load_app()
    release = threading.Event()

    def wordcloud(seg, *a, **k):
        release.wait(5)
        return _cloud(seg)

    monkeypatch.setattr(app, "wordcloud", wordcloud)
    monkeypatch.setattr(app, "COMPARE_DEADLINE_S", 0.2)
    t0 = time.monotonic()
    try:
        with pytest.raises(HTTPException) as ei:
            app.api_compare(**ARGS)
    finally:
        release.set()
    assert ei.value.status_code == 504 and time.monotonic() - t0 < 2


def test_compare_passes_column_errors_through(monkeypatch):
    app = load_app()

    def wordcloud(seg, *a, **k):
        if seg == "v2":
            raise HTTPException(502, "ES 查詢失敗（ConnectionError）")
        return _cloud(seg)

    monkeypatch.setattr(app, "wordcloud", wordcloud)
    with pytest.raises(HTTPException) as ei:
        app.api_compare(**ARGS)
    assert ei.value.status_code == 502 and "ES 查詢失敗" in ei.value.detail


def test_page_retries_after_a_failed_compare():
    js = (REALENV / "web" / "app.js").read_text(encoding="utf-8")
    assert "stale = true; retryAt = Date.now() + wait * 1000;" in js                 # 沒查到：標成待重查，依 5、10、20…秒間隔
    assert "if (stale && $('auto').checked && s.es.ok && !running && Date.now() >= retryAt) run(true);" in js   # 2 秒輪詢負責重查
    assert "if (changed) { lastChange = new Date(); stale = true; }" in js          # 查詢中資料又變了也不會漏掉
    assert "timedOut = true; ctl.abort();" in js and "clearTimeout(timer);" in js    # 前端 200 秒逾時也算沒查到
    assert "{ status: r.status }" in js                                              # 橫幅分得出 API 回錯還是連線中斷
    assert "if (running && fromPoll) return;" in js and "e.name === 'AbortError' && !timedOut" in js
