"""P0-1：文字雲查詢的錯誤要分 reason（timeout／partial／other），SLA 失敗才看得出是超時還是 ES 壞掉。
失敗一律轉成 HTTP 錯誤（逾時／部分結果 504、其餘 502）給 PM 頁看得懂的訊息。用 monkeypatch 把 ES 呼叫換成會丟例外的假函式，不連 ES。"""
import importlib.util
import sys

import pytest

from _p0_fakes import API, ensure_prometheus_client, read_value

ensure_prometheus_client()
requests = pytest.importorskip("requests")
pytest.importorskip("fastapi")
from fastapi import HTTPException  # noqa: E402


def load_app():
    if "q6_api_app" in sys.modules:
        return sys.modules["q6_api_app"]
    if str(API) not in sys.path:          # app.py 內 import ops（同目錄）；單獨跑這個目錄的測試時 api/ 還不在 sys.path
        sys.path.insert(0, str(API))
    # api/web（前端建置產物）不在 repo 裡，載入時 StaticFiles 會檢查目錄；這裡換成空替身，只為了 import 不為了服務靜態檔
    import fastapi.staticfiles as fs
    real_static = fs.StaticFiles
    async def _no_static(scope, receive, send):
        return None
    fs.StaticFiles = lambda *a, **k: _no_static
    try:
        spec = importlib.util.spec_from_file_location("q6_api_app", API / "app.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["q6_api_app"] = mod
        spec.loader.exec_module(mod)
    finally:
        fs.StaticFiles = real_static
    return mod


def _call(app):
    return app.wordcloud("v2", "", [], "", "", 60, True, True, None, False)


@pytest.fixture
def app(monkeypatch):
    mod = load_app()
    monkeypatch.setattr(mod, "dict_overlay", lambda: {"dict_version": "test", "soft": [], "merges": {}})
    monkeypatch.setattr(mod, "es_state", lambda: {"ok": True, "token": "tok"})
    monkeypatch.setattr(mod, "pg_rows", lambda *a, **k: [])
    return mod


def test_read_timeout_counts_as_timeout(app, monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ReadTimeout("read timed out")
    monkeypatch.setattr(app, "es", boom)
    before_t = read_value(app.ERR, "v2", "timeout")
    before_o = read_value(app.ERR, "v2", "other")
    with pytest.raises(HTTPException) as ei:
        _call(app)
    assert ei.value.status_code == 504
    assert read_value(app.ERR, "v2", "timeout") == before_t + 1
    assert read_value(app.ERR, "v2", "other") == before_o


def test_es_http_error_counts_as_other(app, monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.HTTPError("500 Server Error")
    monkeypatch.setattr(app, "es", boom)
    before_t = read_value(app.ERR, "v2", "timeout")
    before_o = read_value(app.ERR, "v2", "other")
    with pytest.raises(HTTPException) as ei:
        _call(app)
    assert ei.value.status_code == 502
    assert read_value(app.ERR, "v2", "other") == before_o + 1
    assert read_value(app.ERR, "v2", "timeout") == before_t


def test_connection_error_counts_as_other(app, monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(app, "es", boom)
    before_o = read_value(app.ERR, "v2", "other")
    with pytest.raises(HTTPException) as ei:
        _call(app)
    assert ei.value.status_code == 502
    assert read_value(app.ERR, "v2", "other") == before_o + 1
