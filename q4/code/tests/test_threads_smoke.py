"""Q4 Threads 量測程式的離線煙霧測試。

shared/capture/fixtures/threads_graphql_sample.json 是 @nasa 個人頁載入時的 GraphQL 回應片段
（只留前 2 串）。這裡把它包成頁面內嵌的 data-sjs 形狀與 GraphQL 回應形狀，檢查
extract_posts / graphql_summary 能抽出 URL、內文、發文時間三個欄位，且不把 token、cursor 留下來。
HTTP 模式用假的 gzip 回應，檢查壓縮後與解壓後大小分開記。
另外照 2026-10-30 實抓 HTML 的形狀（expectedPreloaders、mediaData.page_info、share_info.quoted_post）
組假資料，檢查預排 query、分頁資訊與被引用貼文抽得出來，cursor、userID 的值不留。
瀏覽器流程用本機 127.0.0.1 的假站跑真的 Chromium（找不到就 skip）；main() 的參數用假的
browser_fetch 檢查。都不連外網。
"""
import contextlib
import gzip
import importlib.util
import io
import json
import os
import zlib
from pathlib import Path
from urllib.parse import urlencode

import pytest
import requests
import urllib3

CODE = Path(__file__).resolve().parents[1]
REPO = CODE.parents[1]
FIXTURE = REPO / "shared" / "capture" / "fixtures" / "threads_graphql_sample.json"


def _load():
    spec = importlib.util.spec_from_file_location("_q4_threads", CODE / "threads_q4_measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload(fx):
    edges = [{"node": {
        "code": it["code"],
        "taken_at": it["taken_at"],
        "user": {"username": it["username"]},
        "caption": {"text": it["text"]},
    }} for it in fx["items"]]
    return {"mediaData": {"edges": edges}}


def _wrap(fx):
    return ('<html><body><script type="application/json" data-sjs>'
            + json.dumps(_payload(fx), ensure_ascii=False) + "</script></body></html>")


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_extract_posts_yields_url_text_and_time():
    mod = _load()
    fx = _fixture()
    posts, threads = mod.extract_posts(_wrap(fx))

    assert threads == len(fx["items"]) == 2
    assert len(posts) == 2
    first = next(p for p in posts if p["code"] == "DeaX_rOGaz-")
    assert first["url"] == "https://www.threads.com/@nasa/post/DeaX_rOGaz-"
    assert first["taken_at"] == 1791846387
    assert first["has_text"] is True
    assert all(p["url"] and p["taken_at"] and p["has_text"] for p in posts)
    assert mod.post_stats(posts, "nasa") == {"posts": 2, "own_posts": 2, "quoted_posts": 0,
                                             "posts_with_all_3_fields": 2}


def test_quoted_post_is_counted_but_flagged():
    """10/30 natgeo 的 HTML：natgeo 自己的貼文引用 @natgeotv 那篇，被引用的貼文嵌在
    text_post_app_info.share_info.quoted_post。程式把它也算成 1 篇，但標 quoted，另記 quoted_posts；
    同一篇只要在 share_info 以外出現過，就不算 quoted。"""
    mod = _load()

    def post(code, user, quoted=None):
        p = {"code": code, "taken_at": 1, "user": {"username": user}, "caption": {"text": "t"}}
        if quoted:
            p["text_post_app_info"] = {"share_info": {"quoted_post": quoted, "reposted_post": None}}
        return p

    def thread(p):
        return {"node": {"thread_items": [{"post": p}]}}

    data = {"mediaData": {"edges": [
        thread(post("OWN1", "natgeo", quoted=post("TV1", "natgeotv"))),
        thread(post("OWN2", "natgeo", quoted=post("OWN3", "natgeo"))),   # 先被引用、後面又自己出現
        thread(post("OWN3", "natgeo")),
    ]}}
    posts, threads = mod.posts_in_json([data])
    by = {p["code"]: p for p in posts}

    assert threads == 3 and len(posts) == 4
    assert by["TV1"]["quoted"] is True and by["OWN3"]["quoted"] is False and by["OWN1"]["quoted"] is False
    assert mod.post_stats(posts, "natgeo") == {"posts": 4, "own_posts": 3, "quoted_posts": 1,
                                               "posts_with_all_3_fields": 4}
    s = mod.graphql_summary("doc_id=1", 200, json.dumps({"data": data}).encode("utf-8"))
    assert s["posts"] == 4 and s["quoted_posts"] == 1


def test_extract_posts_ignores_non_json_script_blocks():
    mod = _load()
    html = '<script type="text/javascript">var x = {"code": "A", "taken_at": 1};</script>'
    posts, threads = mod.extract_posts(html)
    assert posts == [] and threads == 0


def test_graphql_summary_counts_threads_and_drops_tokens():
    mod = _load()
    fx = _fixture()
    secret_lsd, secret_cursor = "LSD_SECRET_123", "CURSOR_SECRET_456"
    post_data = urlencode({
        "lsd": secret_lsd,
        "fb_api_req_friendly_name": fx["friendly_name"],
        "doc_id": fx["doc_id"],
        "variables": json.dumps({"after": secret_cursor, "first": 10, "userID": "1",
                                 "__relay_internal__pv__flag": True}),
    })
    body = json.dumps({"data": _payload(fx)}, ensure_ascii=False).encode("utf-8")
    s = mod.graphql_summary(post_data, 200, body)

    assert s["name"] == "BarcelonaProfileThreadsTabRefetchableDirectQuery"
    assert s["doc_id"] == fx["doc_id"]
    assert s["status"] == 200 and s["bytes"] == len(body)
    assert s["threads"] == 2 and s["posts"] == 2 and s["posts_with_all_3_fields"] == 2 and s["quoted_posts"] == 0
    assert s["variables"] == ["after", "first", "userID"]
    assert s["first"] == 10 and s["after_is_null"] is False
    dumped = json.dumps(s)
    assert secret_lsd not in dumped and secret_cursor not in dumped


def test_graphql_summary_handles_prefix_and_non_json():
    mod = _load()
    fx = _fixture()
    body = b"for (;;);" + json.dumps({"data": _payload(fx)}).encode("utf-8")
    s = mod.graphql_summary("", 200, body, header_name="SomeQuery")
    assert s["name"] == "SomeQuery" and s["threads"] == 2 and "variables" not in s

    s = mod.graphql_summary("doc_id=1", 401, b"<html>not json</html>")
    assert s["status"] == 401 and s["threads"] == 0 and s["posts"] == 0


def test_http_fetch_records_compressed_and_decoded_sizes(monkeypatch, tmp_path):
    mod = _load()
    html = _wrap(_fixture()) + ("<!-- pad -->" * 2000)
    raw = gzip.compress(html.encode("utf-8"))
    sent = {}

    def fake_get(url, headers, timeout, stream):
        assert stream is True
        sent.update(headers)
        r = requests.Response()
        r.status_code = 200
        r.headers = requests.structures.CaseInsensitiveDict({"Content-Encoding": "gzip"})
        r.encoding = "utf-8"
        r.raw = urllib3.HTTPResponse(body=io.BytesIO(raw), headers={"Content-Encoding": "gzip"},
                                     status=200, preload_content=False, decode_content=True)
        return r

    monkeypatch.setattr(mod.requests, "get", fake_get)
    res = mod.http_fetch("nasa", tmp_path, sec_fetch=True)

    assert sent["Sec-Fetch-Mode"] == "navigate" and sent["Sec-Fetch-Dest"] == "document"
    assert res["status"] == 200 and res["redirects"] == 0
    assert res["wire_bytes"] == len(raw)
    assert res["html_bytes"] == len(html.encode("utf-8")) > res["wire_bytes"]
    assert res["verdict"] == "OK" and res["threads"] == 2 and res["posts_with_all_3_fields"] == 2
    assert (tmp_path / "http_secfetch_nasa.html").exists()

    sent.clear()
    mod.http_fetch("nasa", tmp_path)
    assert "Sec-Fetch-Mode" not in sent and sent["Accept-Encoding"] == "gzip, deflate"


def test_decode_body_handles_gzip_deflate_identity():
    mod = _load()
    data = b"<html>threads</html>" * 50
    assert mod.decode_body(gzip.compress(data), "gzip") == data
    assert mod.decode_body(zlib.compress(data), "deflate") == data
    raw_deflate = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    assert mod.decode_body(raw_deflate.compress(data) + raw_deflate.flush(), "deflate") == data
    assert mod.decode_body(data, None) == data
    try:
        mod.decode_body(data, "br")
    except ValueError:
        pass
    else:
        raise AssertionError("br 應該要拒絕")


def _profile_html(fx, cursor):
    """仿 10/30 未登入 profile HTML：一段 data-sjs 帶預排 query（first=4），另一段帶 4 串與 page_info。"""
    pre = {"expectedPreloaders": [
        {"actorID": "0", "preloaderID": "P1", "queryID": "28967344372926951", "queryName": "BarcelonaProfileThreadsTabDirectQuery",
         "variables": {"allow_page_info_for_lox_user": True, "first": 4, "userID": "USERID_SECRET_789",
                       "__relay_internal__pv__flag": True}},
        {"actorID": "0", "preloaderID": "P2", "queryID": "39586378077644222", "queryName": "BarcelonaProfilePageDirectQuery",
         "variables": {"canSeeFeedsTab": True, "userID": "USERID_SECRET_789"}},
    ]}
    data = _payload(fx)
    data["mediaData"]["page_info"] = {"end_cursor": cursor, "has_next_page": True,
                                      "has_previous_page": False, "start_cursor": None}
    return ("<html><body>"
            '<script type="application/json" data-sjs>' + json.dumps({"require": [pre]}) + "</script>"
            '<span dir="auto">Log in to see more from nasa.</span>'
            '<script type="application/json" data-sjs>' + json.dumps(data, ensure_ascii=False) + "</script>"
            "</body></html>")


def test_html_summary_reads_preloaders_and_page_info_without_values():
    mod = _load()
    cursor = "C" * 120
    s = mod.html_summary(_profile_html(_fixture(), cursor), "nasa")

    assert s["threads"] == 2 and s["posts"] == 2 and s["posts_with_all_3_fields"] == 2
    assert s["page_info"] == {"threads": 2, "has_next_page": True, "end_cursor_len": 120}
    assert s["login_wall_text"] is True
    tab = next(q for q in s["preloaded_queries"] if q["name"] == "BarcelonaProfileThreadsTabDirectQuery")
    assert tab == {"name": "BarcelonaProfileThreadsTabDirectQuery", "query_id": "28967344372926951",
                   "variables": ["allow_page_info_for_lox_user", "first", "userID"], "first": 4}
    assert [q["name"] for q in s["preloaded_queries"]] == ["BarcelonaProfilePageDirectQuery",
                                                           "BarcelonaProfileThreadsTabDirectQuery"]
    dumped = json.dumps(s)
    assert cursor not in dumped and "USERID_SECRET_789" not in dumped


def test_html_summary_on_shell_page_is_empty():
    mod = _load()
    s = mod.html_summary("<html><script>BarcelonaProfile</script></html>", "nasa")
    assert s["threads"] == 0 and s["posts"] == 0
    assert s["page_info"] is None and s["preloaded_queries"] == [] and s["login_wall_text"] is False


def test_graphql_summary_reports_has_next_page():
    mod = _load()
    data = _payload(_fixture())
    data["mediaData"]["page_info"] = {"end_cursor": "SECRET_CURSOR", "has_next_page": False}
    s = mod.graphql_summary("doc_id=1", 200, json.dumps({"data": data}).encode("utf-8"))
    assert s["has_next_page"] is False and "SECRET_CURSOR" not in json.dumps(s)


def test_url_shape_drops_query_values():
    mod = _load()
    assert mod.url_shape("https://www.threads.com/@nasa") == "/@nasa"
    assert mod.url_shape("https://www.threads.com/@nasa?xmt=TOKEN123&hl=en") == "/@nasa?hl&xmt"


@pytest.fixture(scope="module")
def chromium():
    """真的 Chromium：先看 Q4_CHROMIUM，再看 Playwright 自己的預設執行檔，最後看 PLAYWRIGHT_BROWSERS_PATH
    底下的 chromium 連結（這台容器的 Playwright 1.64.1 找不到自己的版本，只有這個連結能用）。都沒有就 skip。"""
    pytest.importorskip("playwright.sync_api")
    cands = [os.environ.get("Q4_CHROMIUM")]
    if not cands[0]:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            cands.append(p.chromium.executable_path)
        if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            cands.append(str(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) / "chromium"))
    path = next((c for c in cands if c and Path(c).exists()), None)
    if not path:
        pytest.skip("找不到 Chromium（設 Q4_CHROMIUM）")
    return path


@contextlib.contextmanager
def _local_site(pages, post_body=b"{}"):
    """本機 127.0.0.1 假站，不連外。GET 交給 pages(path) 回 (狀態, body, 額外標頭)；POST 一律回 post_body。"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="text/html; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            code, body, extra = pages(self.path)
            self._send(code, body, extra=extra)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send(200, post_body, "application/json")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


@pytest.mark.parametrize("device", ["desktop", "mobile"])
def test_browser_fetch_on_local_page_survives_renavigation(monkeypatch, tmp_path, chromium, device):
    """本機假站：第一份 HTML 帶 4 串資料、過一下用 JS 跳到 302 再回 /@nasa?hpir=1
    （10/30 手機視窗實際看到的流程），第二份頁面的主欄是內層捲動區，捲到底才送 /graphql/query。
    前一次被中斷的嘗試（07:49 UTC 寫的版本，沒有 commit）在導覽後才 resp.text()，手機模式 3/3 丟
    Network.getResponseBody 錯誤；現在要讀到第一份 HTML。DEVICES 的桌面與手機設定各跑一次。"""
    mod = _load()
    fx = _fixture()
    first = _profile_html(fx, "C" * 120).replace(
        "</body>", "<script>setTimeout(() => { location.href = '/aymh/?next=%2F%40nasa'; }, 800);</script></body>")
    second = """<html><head><style>html,body{margin:0;height:100%;overflow:hidden}
#main{height:100vh;overflow-y:auto}</style></head><body><div id="main"><div id="list"></div>
<div style="height:3000px"></div></div><script>
const list = document.getElementById('list');
const add = (code) => { const a = document.createElement('a'); a.href = '/@nasa/post/' + code;
  a.innerHTML = '<time>t</time>' + code; list.appendChild(a); };
add('DeaX_rOGaz-'); add('DeUWxGgjHSK');
let sent = false;
document.getElementById('main').addEventListener('scroll', () => {
  if (sent) return; sent = true;
  const body = new URLSearchParams({lsd: 'LSD_SECRET', doc_id: '39825889313693314',
    fb_api_req_friendly_name: 'BarcelonaProfileThreadsTabRefetchableDirectQuery',
    variables: JSON.stringify({after: 'CURSOR_SECRET', first: 11, userID: '1'})});
  fetch('/graphql/query', {method: 'POST', body}).then(r => r.json()).then(() => add('NEWCODE'));
});
</script></body></html>"""

    def pages(path):
        if path == "/@nasa":
            return 200, first.encode("utf-8"), None
        if path.startswith("/aymh/"):
            return 302, b"", {"Location": "/@nasa?hpir=1"}
        if path.startswith("/@nasa?"):
            return 200, second.encode("utf-8"), None
        return 404, b"", None

    with _local_site(pages, json.dumps({"data": _payload(fx)}).encode("utf-8")) as base:
        monkeypatch.setattr(mod, "BASE", base)
        b = mod.browser_fetch("nasa", tmp_path, scrolls=1, device=device, executable_path=chromium)

    assert b["device"] == device and b["status"] == 200
    assert [d["status"] for d in b["documents"]] == [200, 302, 200]
    # 兩次導覽、三份文件：302 的中間網址 /aymh/ 不會出現在 navigations
    assert b["navigations"] == ["/@nasa", "/@nasa?hpir"]
    doc = b["document"]
    assert doc["threads"] == 2 and doc["posts_with_all_3_fields"] == 2 and doc["quoted_posts"] == 0
    assert doc["page_info"] == {"threads": 2, "has_next_page": True, "end_cursor_len": 120}
    assert b["rendered_after_load"]["post_links"] == 2
    assert b["scroll_steps"][0]["how"] == "inner" and b["scroll_steps"][0]["inner"]["after"] > 0
    g = [x for x in b["graphql"] if x.get("name") == "BarcelonaProfileThreadsTabRefetchableDirectQuery"]
    assert len(g) == 1 and g[0]["phase"] == "scroll" and g[0]["threads"] == 2 and g[0]["first"] == 11
    assert b["rendered_after_scroll"]["post_links"] == 3 and b["scroll_loaded_more"] is True
    # inline_* 量的是 page.content() 的內嵌 JSON：轉址後的頁面沒有內嵌資料，捲動載入的那篇也不會寫回去
    assert b["inline_posts_before_scroll"] == 0 and b["inline_posts_after_scroll"] == 0
    dumped = json.dumps(b)
    assert "LSD_SECRET" not in dumped and "CURSOR_SECRET" not in dumped and "C" * 120 not in dumped
    assert (tmp_path / f"browser_{device}_nasa.html").exists()


def test_browser_fetch_falls_back_to_scrollto_without_inner_scroller(monkeypatch, tmp_path, chromium):
    """短頁、沒有內層捲動區：滾輪捲不動，SCROLL_INNER_JS 也找不到能捲的元素，就退回 window.scrollTo。
    頁面載入後用 history.replaceState 改一次網址（同一份文件）：navigations 多一筆、documents 仍是 1 份，
    筆數和 10/30 桌面實測（每次 navigations 2 筆、文件 1 份）一樣。"""
    mod = _load()
    html = (_wrap(_fixture())
            .replace("<body>", '<body><a href="/@nasa/post/DeaX_rOGaz-"><time>t</time>x</a>')
            .replace("</body>", "<script>setTimeout(() => history.replaceState(null, '', '/@nasa'), 300);</script></body>"))

    with _local_site(lambda path: (200, html.encode("utf-8"), None) if path == "/@nasa" else (404, b"", None)) as base:
        monkeypatch.setattr(mod, "BASE", base)
        b = mod.browser_fetch("nasa", tmp_path, scrolls=1, device="desktop", executable_path=chromium)

    assert [d["status"] for d in b["documents"]] == [200]
    assert b["navigations"] == ["/@nasa", "/@nasa"] and b["requests_by_type"]["document"] == 1
    assert b["scroll_steps"][0]["how"] == "scrollTo" and "inner" not in b["scroll_steps"][0]
    assert b["graphql"] == [] and b["scroll_loaded_more"] is False
    # 內嵌 JSON 有 2 篇，DOM 裡只有 1 個貼文連結：兩種數法本來就不同
    assert b["inline_posts_before_scroll"] == b["inline_posts_after_scroll"] == 2
    assert b["rendered_after_scroll"]["post_links"] == 1


def _fake_browser_result(device):
    return {"device": device, "document": {"threads": 0, "posts": 0},
            "rendered_after_load": {"post_links": 0}, "rendered_after_scroll": {"post_links": 0},
            "graphql": [], "total_requests": 0, "total_wire_bytes": 0}


def test_main_passes_chromium_path_and_opens_new_run_dir(monkeypatch, tmp_path, capsys):
    """--chromium 優先、沒給就讀 Q4_CHROMIUM、都沒有就是 None；--device、--scroll-check 照傳。
    沒給 --out 時在 OUT_ROOT 底下開新的 run_<時間>/，不碰 OUT_ROOT 本身的 results.json。"""
    mod = _load()
    calls = []

    def fake_browser_fetch(account, out_dir, scrolls, device="desktop", executable_path=None):
        calls.append({"account": account, "out_dir": out_dir, "scrolls": scrolls, "device": device,
                      "executable_path": executable_path})
        return _fake_browser_result(device)

    monkeypatch.setattr(mod, "browser_fetch", fake_browser_fetch)
    monkeypatch.setattr(mod, "OUT_ROOT", tmp_path)
    monkeypatch.delenv("Q4_CHROMIUM", raising=False)
    (tmp_path / "results.json").write_text("legacy", encoding="utf-8")
    accounts = tmp_path / "accounts.txt"
    accounts.write_text("nasa\n", encoding="utf-8")
    base = ["--mode", "browser", "--accounts", str(accounts), "--delay", "0"]

    mod.main(base + ["--device", "mobile", "--scroll-check", "2", "--chromium", "/x/chrome", "--out", str(tmp_path / "a")])
    monkeypatch.setenv("Q4_CHROMIUM", "/y/chrome")
    mod.main(base + ["--out", str(tmp_path / "b")])
    monkeypatch.delenv("Q4_CHROMIUM")
    mod.main(base)
    capsys.readouterr()

    assert [(c["account"], c["device"], c["scrolls"], c["executable_path"]) for c in calls] == [
        ("nasa", "mobile", 2, "/x/chrome"), ("nasa", "desktop", 5, "/y/chrome"), ("nasa", "desktop", 5, None)]
    runs = sorted(tmp_path.glob("run_*/results.json"))
    assert len(runs) == 1 and calls[2]["out_dir"] == runs[0].parent
    assert (tmp_path / "results.json").read_text(encoding="utf-8") == "legacy"
    rows = json.loads((tmp_path / "a" / "results.json").read_text(encoding="utf-8"))
    assert rows == [{"account": "nasa", "browser": _fake_browser_result("mobile")}]


def test_main_refuses_to_overwrite_existing_results(monkeypatch, tmp_path, capsys):
    """--out 指到已經有 results.json 的目錄（例如 10-27 舊紀錄所在的 threads_q4_out/）：退出碼 2，檔案不動，也不開始量測。"""
    mod = _load()
    monkeypatch.setattr(mod, "browser_fetch", lambda *a, **k: pytest.fail("不該開始量測"))
    monkeypatch.setattr(mod, "http_fetch", lambda *a, **k: pytest.fail("不該開始量測"))
    (tmp_path / "results.json").write_text("legacy", encoding="utf-8")

    with pytest.raises(SystemExit) as e:
        mod.main(["--delay", "0", "--out", str(tmp_path)])
    assert e.value.code == 2
    assert "不覆蓋" in capsys.readouterr().err
    assert (tmp_path / "results.json").read_text(encoding="utf-8") == "legacy"
