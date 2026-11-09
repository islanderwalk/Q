"""Q3 PTT 量測程式的離線煙霧測試。

用 shared/capture/html 內的實抓的 PTT 頁面（2026/10/30 實抓），不連網。
斷言的數字取自 q3 README 與 ptt_live_invariants.json 記載的實測值。
抓取流程（請求計數、--max-requests、輸出資料夾、中斷時的紀錄）用假的 session 與假時鐘測，不會送出任何請求。
"""
import csv
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import requests

CODE = Path(__file__).resolve().parents[1]
REPO = CODE.parents[1]
HTML = REPO / "shared" / "capture" / "html"
DATA = REPO / "q3" / "data"
LATEST_TS = 1793294966  # ptt_index_latest.html 最新一篇的發文時間


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_q3_{name}", CODE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(name):
    return (HTML / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def measure():
    return _load("ptt_measure")


@pytest.fixture(scope="module")
def q3():
    return _load("ptt_q3_measure")


@pytest.fixture(scope="module")
def census():
    return _load("ptt_q3_census")


class FakeClock:
    """取代模組裡的 time：sleep 只把時鐘往前推，不實際等。"""

    def __init__(self, now):
        self.now = float(now)

    def time(self):
        return self.now

    def perf_counter(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakeResp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")


class FakeSession:
    """取代 requests.Session：依網址回傳 repo 內的 PTT 頁面，記下每個網址，不連網。"""

    def __init__(self, route=None):
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.calls = []
        self.route = route or self.ptt

    @staticmethod
    def ptt(url):
        if url.endswith("/index.html"):
            return FakeResp(200, _read("ptt_index_latest.html"))
        if "/index" in url:
            return FakeResp(200, _read("ptt_index_39386.html"))
        if "/M." in url:
            return FakeResp(200, _read("ptt_article.html"))
        return FakeResp(404)

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.route(url)


def _run_main(mod, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", [mod.__name__, *argv])
    mod.main()


def _runs(path):
    """讀 run_summary.jsonl：每次執行一行。"""
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def _interrupt_on(n, route=FakeSession.ptt):
    """第 n 個請求送出後還沒回應就按 Ctrl-C。"""
    calls = []

    def go(url):
        calls.append(url)
        if len(calls) == n:
            raise KeyboardInterrupt
        return route(url)
    return go


def _mtimes(folder):
    return {p.name: p.stat().st_mtime_ns for p in folder.iterdir()}


# ── 解析：用實抓頁面 ─────────────────────────────────────────────────────


def test_full_list_page_has_20_slots(q3, measure):
    entries, prev = q3.parse_index(_read("ptt_index_39386.html"), newest=False)
    assert len(entries) == 20
    assert prev.endswith("/bbs/Gossiping/index39385.html")
    entries_m, prev_m = measure.parse_index(_read("ptt_index_39386.html"))
    # 兩支程式對「上一頁」的寫法不同（一個相對路徑、一個絕對網址），只比對頁碼
    assert len(entries_m) == 20 and prev_m.endswith("/bbs/Gossiping/index39385.html")


def test_latest_page_stops_at_pinned_separator(q3):
    entries, prev = q3.parse_index(_read("ptt_index_latest.html"), newest=True)
    assert len(entries) == 15  # 置底線以上 15 篇，置底文不計入
    assert prev.endswith("/bbs/Gossiping/index39390.html")


def test_article_comment_counts_are_consistent(q3, census):
    rec = q3.parse_article(_read("ptt_article.html"), 1793000000)
    assert (rec["push"], rec["boo"], rec["arrow"]) == (108, 7, 88)
    assert rec["comments"] == rec["push"] + rec["boo"] + rec["arrow"] == 203

    info = census.parse_article(_read("ptt_article.html"), 1793000000)
    assert (info["push"], info["boo"], info["arrow"], info["comments"]) == (108, 7, 88, 203)
    assert info["content_bytes"] > 0 and info["body_bytes"] > 0


def test_live_invariant_snapshot_rows_are_20_except_latest():
    snap = json.loads(_read("ptt_live_invariants.json"))
    assert snap, "快照不可為空"
    assert all(p["rows"] == 20 for p in snap)
    for p in snap:
        ts = p["ts"]
        # 列表內文章 ID 時間戳不倒退（頁內由上而下遞增）
        assert ts == sorted(ts)


# ── 請求計數與上限 ───────────────────────────────────────────────────────


def test_q3_fetcher_counts_retries_and_stops_at_cap(q3, monkeypatch):
    monkeypatch.setattr(q3, "time", FakeClock(LATEST_TS))
    answers = iter([FakeResp(500), FakeResp(200, "ok"), FakeResp(200, "ok")])
    f = q3.Fetcher(delay=1.5, max_requests=3)
    f.s = FakeSession(lambda url: next(answers))
    assert f.get("https://www.ptt.cc/a", "index").text == "ok"  # 500 之後重試成功：算 2 個請求
    assert f.get("https://www.ptt.cc/b", "article").text == "ok"
    assert f.get("https://www.ptt.cc/c", "article") is None  # 第 4 個不送
    s = f.summary()
    assert (s["requests"], s["capped"], s["max_requests"]) == (3, True, 3)
    assert s["by_kind"] == {"index": 2, "article": 1}
    assert s["status"] == {"500": 1, "200": 2}
    assert s["min_interval_s"] >= 1.5
    assert len(f.s.calls) == 3


def test_measure_fetcher_without_cap_is_unchanged(measure, monkeypatch):
    monkeypatch.setattr(measure, "time", FakeClock(LATEST_TS))
    monkeypatch.setattr(measure.requests, "Session", lambda: FakeSession(lambda url: FakeResp(200, "ok")))
    f = measure.Fetcher(delay=0.5)
    for i in range(5):
        assert f.get(f"https://www.ptt.cc/{i}") == "ok"
    s = f.summary()
    assert (s["requests"], s["capped"], s["max_requests"]) == (5, False, None)
    assert f.s.cookies.get("over18", domain="www.ptt.cc") == "1"


def test_measure_main_writes_csv_and_summary_to_out_dir(measure, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(measure, "time", FakeClock(LATEST_TS + 600))
    monkeypatch.setattr(measure.requests, "Session", FakeSession)
    _run_main(measure, monkeypatch, ["--pages", "2", "--articles", "5", "--delay", "1.5",
                                     "--out-dir", str(tmp_path), "--max-requests", "4"])
    [summary] = _runs(tmp_path / "ptt_Gossiping_summary.jsonl")
    assert (summary["requests"], summary["capped"], summary["exit"]) == (4, True, "capped")  # 2 個列表 + 2 篇文章後停下
    assert summary["min_interval_s"] >= 1.5
    with open(tmp_path / "ptt_Gossiping.csv", encoding="utf-8-sig") as fp:
        assert len(list(csv.DictReader(fp))) == 15 + 20
    out = capsys.readouterr().out
    assert "已達上限" in out
    assert not any("over18" in p.read_text(encoding="utf-8-sig") for p in tmp_path.iterdir())


def test_q3_measure_main_respects_cap_and_out(q3, monkeypatch, tmp_path, capsys):
    default_out = DATA / "ptt_q3_out" / "index_entries.csv"
    before = default_out.stat().st_mtime_ns
    monkeypatch.setattr(q3, "time", FakeClock(LATEST_TS + 600))
    monkeypatch.setattr(q3.requests, "Session", FakeSession)
    _run_main(q3, monkeypatch, ["--days", "1", "--per-day", "3", "--delay", "1.5",
                                "--out", str(tmp_path / "run"), "--max-requests", "3"])
    [run] = _runs(tmp_path / "run" / "run_summary.jsonl")
    # 每頁回同一頁 39386，永遠碰不到 1 天前，靠上限停下：3 個請求全是列表
    assert (run["requests"], run["capped"], run["by_kind"], run["exit"]) == (3, True, {"index": 3}, "capped")
    assert run["min_interval_s"] >= 1.5
    assert (tmp_path / "run" / "index_entries.csv").exists()
    assert (tmp_path / "run" / "article_sample.json").exists()
    assert default_out.stat().st_mtime_ns == before  # 沒有寫進 repo 的 q3/data
    out = capsys.readouterr().out
    assert "請求數 3（列表 3、文章 0）" in out and "已達上限" in out
    assert "不可外推" in out  # 視窗不到一天


def test_measure_writes_summary_when_interrupted(measure, monkeypatch, tmp_path):
    monkeypatch.setattr(measure, "time", FakeClock(LATEST_TS + 600))
    monkeypatch.setattr(measure.requests, "Session", lambda: FakeSession(_interrupt_on(3)))
    with pytest.raises(KeyboardInterrupt):
        _run_main(measure, monkeypatch, ["--pages", "2", "--articles", "5", "--out-dir", str(tmp_path)])
    [run] = _runs(tmp_path / "ptt_Gossiping_summary.jsonl")
    # 2 個列表有回應，第 3 個（第一篇文章）送出後被中斷：仍算 1 個請求
    assert (run["exit"], run["requests"], run["status"]) == ("interrupted", 3, {"200": 2, "no_response": 1})
    assert run["delay_s"] == 1.5  # 預設間隔
    assert not (tmp_path / "ptt_Gossiping.csv").exists()  # CSV 只在跑完時寫


def test_q3_measure_refuses_repo_out(q3, monkeypatch):
    before = _mtimes(DATA / "ptt_q3_out")
    monkeypatch.setattr(q3.requests, "Session", lambda: pytest.fail("不該建立連線"))
    for argv in ([], ["--out", str(DATA / "ptt_q3_out")]):
        with pytest.raises(SystemExit, match="--out"):
            _run_main(q3, monkeypatch, argv)
    assert _mtimes(DATA / "ptt_q3_out") == before  # 也沒有多出 run_summary.jsonl


def test_q3_measure_summary_on_early_exit_and_interrupt(q3, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(q3, "time", FakeClock(LATEST_TS + 600))
    out = tmp_path / "run"
    base = ["--days", "1", "--per-day", "3", "--out", str(out)]

    monkeypatch.setattr(q3.requests, "Session", FakeSession)
    with pytest.raises(SystemExit, match="請求上限 0"):  # 不是「連不到列表頁」
        _run_main(q3, monkeypatch, base + ["--max-requests", "0"])

    monkeypatch.setattr(q3.requests, "Session", lambda: FakeSession(lambda url: FakeResp(404)))
    with pytest.raises(SystemExit, match="連不到列表頁"):
        _run_main(q3, monkeypatch, base)

    monkeypatch.setattr(q3.requests, "Session", lambda: FakeSession(_interrupt_on(2)))
    with pytest.raises(KeyboardInterrupt):
        _run_main(q3, monkeypatch, base)

    runs = _runs(out / "run_summary.jsonl")
    assert [(r["exit"].split(":")[0], r["requests"], r["capped"]) for r in runs] == [
        ("exit", 0, True), ("exit", 1, False), ("interrupted", 2, False)]
    assert runs[1]["status"] == {"404": 1}
    assert runs[2]["status"] == {"200": 1, "no_response": 1}
    assert {r["delay_s"] for r in runs} == {1.5}  # 預設間隔
    assert "累計 3 次執行、3 個請求" in capsys.readouterr().err


def test_short_window_note(q3):
    assert "不可外推" in q3.short_window_note(1.2)
    assert q3.short_window_note(24) == ""


def _write_targets(tmp_path, end):
    """6 列：視窗內 4 篇（1 篇是 404）、比視窗新 1 篇、比視窗舊 1 篇、刪文 1 篇。"""
    rows = [
        ("M.%d.A.001" % (end - 3600), end - 3600, "False"),
        ("M.%d.A.002" % (end - 7200), end - 7200, "False"),
        ("M.%d.A.003" % (end - 10800), end - 10800, "False"),
        ("M.%d.A.004" % (end - 14400), end - 14400, "False"),
        ("M.%d.A.005" % (end + 60), end + 60, "False"),
        ("M.%d.A.006" % (end - 2 * 86400), end - 2 * 86400, "False"),
        ("", "", "True"),
    ]
    idx = tmp_path / "index_entries.csv"
    with open(idx, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["page", "aid", "ts", "nrec", "deleted", "author", "title", "url"])
        for aid, ts, deleted in rows:
            url = f"https://www.ptt.cc/bbs/Gossiping/{aid}.html" if aid else ""
            w.writerow([1, aid, ts, 5, deleted, "x", "t", url])
    sample = tmp_path / "article_sample.json"
    sample.write_text(json.dumps([{"aid": rows[2][0]}, {"aid": rows[5][0]}]), encoding="utf-8")
    return idx, sample, [r[0] for r in rows]


def test_census_window_cap_and_resume(census, monkeypatch, tmp_path, capsys):
    end = LATEST_TS
    idx, sample, aids = _write_targets(tmp_path, end)
    sess = FakeSession(lambda url: FakeResp(404) if aids[3] in url else FakeResp(200, _read("ptt_article.html")))
    monkeypatch.setattr(census, "time", FakeClock(end + 600))
    monkeypatch.setattr(census.requests, "Session", lambda: sess)
    out = tmp_path / "census"
    base = ["--index", str(idx), "--sample", str(sample), "--days", "1", "--delay", "1.5",
            "--window-end", census.datetime.fromtimestamp(end, census.TW).isoformat(), "--out", str(out)]

    _run_main(census, monkeypatch, base + ["--max-requests", "2"])
    [run] = _runs(out / "run_summary.jsonl")
    assert (run["targets"], run["requests"], run["capped"], run["exit"]) == (4, 2, True, "capped")
    assert run["min_interval_s"] >= 1.5
    lines = [json.loads(x) for x in (out / "articles.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["aid"] for x in lines] == [aids[2], aids[3]]  # A 的抽樣排最前面，其餘由舊到新
    assert "已達上限" in capsys.readouterr().out

    _run_main(census, monkeypatch, base)  # 不設上限：只補抓剩下 2 篇
    first, second = _runs(out / "run_summary.jsonl")  # 第一次的紀錄還在，續抓加一行
    assert first == run
    assert (second["already_done"], second["todo"], second["requests"], second["exit"]) == (2, 2, 2, "ok")
    assert first["requests"] + second["requests"] == len(sess.calls) == 4
    assert "累計 2 次執行、4 個請求" in capsys.readouterr().out
    assert not any(aids[4] in u or aids[5] in u for u in sess.calls)  # 視窗外的不抓
    result_b = (out / "result_B.txt").read_text(encoding="utf-8")
    assert "目標 4 篇；成功 3、已被刪（404）1、失敗 0" in result_b
    assert "對照先前的抽樣推估" not in result_b  # 只屬於預設視窗
    assert "原抽樣 1 篇" in (out / "result_A.txt").read_text(encoding="utf-8")


def test_census_writes_summary_when_interrupted(census, monkeypatch, tmp_path):
    end = LATEST_TS
    idx, sample, aids = _write_targets(tmp_path, end)
    monkeypatch.setattr(census, "time", FakeClock(end + 600))
    monkeypatch.setattr(census.requests, "Session",
                        lambda: FakeSession(_interrupt_on(2, lambda url: FakeResp(200, _read("ptt_article.html")))))
    out = tmp_path / "census"
    argv = ["--index", str(idx), "--sample", str(sample), "--days", "1",
            "--window-end", census.datetime.fromtimestamp(end, census.TW).isoformat(), "--out", str(out)]
    with pytest.raises(KeyboardInterrupt):
        _run_main(census, monkeypatch, argv)
    [run] = _runs(out / "run_summary.jsonl")
    # 第 1 篇抓完寫進 JSONL；第 2 篇的請求送出後被中斷，JSONL 沒有它，但 run_summary 有算到
    assert (run["exit"], run["requests"], run["status"]) == ("interrupted", 2, {"200": 1, "no_response": 1})
    assert run["delay_s"] == 1.5  # 預設間隔
    assert len((out / "articles.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def _write_index(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["page", "aid", "ts", "nrec", "deleted", "author", "title", "url"])
        for page, ts in rows:
            aid = "M.%d.A.001" % ts
            w.writerow([page, aid, ts, 0, "False", "x", "t", f"https://www.ptt.cc/bbs/Gossiping/{aid}.html"])


def test_census_abort_still_writes_summary(census, monkeypatch, tmp_path, capsys):
    end = LATEST_TS
    idx = tmp_path / "index_entries.csv"
    _write_index(idx, [(2, end - 60 * k) for k in range(1, 32)] + [(1, end - 2 * 86400)])  # 視窗內 31 篇
    sample = tmp_path / "article_sample.json"
    sample.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(census, "time", FakeClock(end + 600))
    monkeypatch.setattr(census.requests, "Session", lambda: FakeSession(lambda url: FakeResp(500)))
    out = tmp_path / "census"
    _run_main(census, monkeypatch, ["--index", str(idx), "--sample", str(sample), "--days", "1", "--out", str(out),
                                    "--window-end", census.datetime.fromtimestamp(end, census.TW).isoformat()])
    [run] = _runs(out / "run_summary.jsonl")
    # 每篇試 3 次都是 500，連續 30 篇失敗就停；第 30 篇沒寫進 JSONL，但它的 3 個請求有算到
    assert (run["exit"], run["requests"], run["status"], run["capped"]) == ("abort", 90, {"500": 90}, False)
    assert len((out / "articles.jsonl").read_text(encoding="utf-8").splitlines()) == 29
    assert "ABORT" in capsys.readouterr().out


def test_census_refuses_index_that_does_not_cover_window(census, monkeypatch, tmp_path, capsys):
    end = LATEST_TS
    sample = tmp_path / "article_sample.json"
    sample.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(census.requests, "Session", lambda: pytest.fail("不該建立連線"))
    base = ["--sample", str(sample), "--days", "1", "--out", str(tmp_path / "out"),
            "--window-end", census.datetime.fromtimestamp(end, census.TW).isoformat()]
    cases = {
        "沒往回翻到視窗開頭": [(2, end - 3600), (1, end - 7200)],  # 最舊一篇還在視窗裡
        "缺了 1 頁": [(3, end - 3600), (1, end - 2 * 86400)],  # index2 沒抓到
    }
    for i, (msg, rows) in enumerate(cases.items()):
        idx = tmp_path / f"index_{i}.csv"
        _write_index(idx, rows)
        with pytest.raises(SystemExit, match="沒有涵蓋整個視窗"):
            _run_main(census, monkeypatch, base + ["--index", str(idx)])
        assert msg in capsys.readouterr().err
    assert not (tmp_path / "out").exists()

    problems, warnings = census.index_coverage(DATA / "ptt_q3_out" / "index_entries.csv", census.WINDOW_START, 7)
    assert (problems, warnings) == ([], [])  # repo 內的列表涵蓋 10/19–10/26 整個視窗


def test_census_refuses_other_window_into_default_out(census, monkeypatch, tmp_path):
    default_out = DATA / "ptt_q3_census_out"
    before = {p.name: p.stat().st_mtime_ns for p in default_out.iterdir()}
    idx, sample, _ = _write_targets(tmp_path, LATEST_TS)
    monkeypatch.setattr(census.requests, "Session", lambda: pytest.fail("不該建立連線"))
    for argv in (["--window-end", "2026-10-30T00:00", "--days", "1"],
                 ["--index", str(idx), "--sample", str(sample)],
                 ["--report", "--days", "1"]):
        with pytest.raises(SystemExit, match="--out"):
            _run_main(census, monkeypatch, argv)
    assert {p.name: p.stat().st_mtime_ns for p in default_out.iterdir()} == before


def test_census_report_reproduces_committed_results(census, monkeypatch, tmp_path):
    shutil.copy(DATA / "ptt_q3_census_out" / "articles.jsonl", tmp_path / "articles.jsonl")
    _run_main(census, monkeypatch, ["--report", "--out", str(tmp_path)])
    for name in ("result_A.txt", "result_B.txt"):
        assert (tmp_path / name).read_bytes() == (DATA / "ptt_q3_census_out" / name).read_bytes(), name
    assert not (tmp_path / "run_summary.jsonl").exists()  # 只產報告時沒有請求
    result_b = (tmp_path / "result_B.txt").read_text(encoding="utf-8")
    # 6,237 是篇數（每篇的最後狀態），不是請求數
    assert "文章 6,237 篇（最後狀態）" in result_b and "請求 6,237 次" not in result_b
    # 有沒有重試只是推測，報告裡要寫成「可能」
    assert "回應時間 ≥5 秒的文章 10 篇（5.6–7.2 秒）：可能是第一次失敗" in result_b


def test_make_charts_reproduces_committed_html(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("_q3_make_charts", REPO / "q3" / "answer" / "src" / "make_charts.py")
    charts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(charts)
    monkeypatch.setattr(charts, "ANSWER", tmp_path)  # 寫到暫存資料夾，不動 repo 內的檔案
    (tmp_path / "src").mkdir()
    charts.main()
    made = sorted(p.relative_to(tmp_path) for p in tmp_path.glob("*/chart-*.html"))
    assert len(made) == 9
    for rel in made + [Path("src/ptt_targets.json")]:
        assert (tmp_path / rel).read_bytes() == (REPO / "q3" / "answer" / rel).read_bytes(), rel
    crawl = (tmp_path / "5-not-monitored" / "chart-crawl-stats.html").read_text(encoding="utf-8")
    assert "最後仍抓不到的文章" in crawl and "0 / 6,237 篇" in crawl and "請求失敗" not in crawl
    assert "中途若有失敗也由退避重試補回" in crawl  # 有沒有失敗過只是推測，不寫成事實


def test_recheck_matches_readme_figures(monkeypatch, tmp_path, capsys):
    recheck = _load("ptt_q3_recheck")
    _run_main(recheck, monkeypatch, ["--out", str(tmp_path / "r.json")])
    r = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert r["index"]["full_pages"] == r["index"]["full_pages_with_20"] == 334
    assert r["index"]["ts_inversions"] == 0
    assert (r["daily"]["full_day_mean"], r["daily"]["full_day_min"], r["daily"]["full_day_max"]) == (890.0, 744, 1109)
    assert r["census"]["targets_equal_window_alive"] and r["census"]["comments"] == 234577
    # 下限是篇數；10 篇的回應時間是 5.6–7.2 秒，可能含一次 5 秒退避，所以上限約多 10 次（中斷當下的請求沒有紀錄）
    assert r["census"]["http_requests_range"] == [6237, 6247]
    assert {k: r["spec_cost"]["readme"][k] for k in ("spec1", "spec2")} == {"spec1": 960, "spec2": 5340}
    assert round(r["spec_cost"]["readme"]["ratio"], 1) == 5.6
    w = {x["window_hours"]: x for x in r["short_windows"]}
    assert w[1.2]["p95"] > 10 * w[1.2]["p5"]  # 1.2 小時視窗推出的日均散到十倍以上
    assert (w[1.2]["windows"], w[1.2]["empty_windows"], w[1.2]["min"]) == (857, 13, 0)  # 沒有文章的視窗算 0 篇/天
    assert 600 < w[24]["min"] <= w[24]["max"] < 1200
    y = r["young_articles"]
    # 文章太新只解釋得了 37.7 → 約 18 則；日均跟 Windows 那次一樣高的視窗，平均也看得到 17 則以上，到不了 6.0
    assert 17 < y["visible_comments_age_uniform_0_72_min"] < 19
    assert y["visible_at_1_2h_window_end_ge_short_run"]["min"] > 2 * y["short_run_implied_comments_per_article"]


@pytest.mark.parametrize("name, argv, copy_census", [
    ("ptt_q3_recheck", ["--out", "{tmp}/r.json"], False),
    ("ptt_q3_census", ["--report", "--out", "{tmp}"], True),
])
def test_report_scripts_survive_cp950_stdout(name, argv, copy_census, tmp_path):
    # 繁中 Windows 把輸出導向檔案或管線時預設 cp950，報告裡的 ≥、≈ 曾讓程式在寫完檔案後以退出碼 1 中斷
    import os
    import subprocess
    if copy_census:
        shutil.copy(DATA / "ptt_q3_census_out" / "articles.jsonl", tmp_path / "articles.jsonl")
    env = {**os.environ, "PYTHONIOENCODING": "cp950", "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, str(CODE / f"{name}.py"), *[a.format(tmp=tmp_path) for a in argv]],
                       env=env, capture_output=True, timeout=120)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-500:]
    assert "≥".encode("utf-8") in p.stdout or "≈".encode("utf-8") in p.stdout
