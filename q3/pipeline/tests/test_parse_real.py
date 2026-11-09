"""解析器對 F12 截圖時存下的同一批 PTT 頁面（tests/fixtures/real/）的結果。"""
import json
from pathlib import Path

import pytest

from q3pipe.ptt import Fetcher, aid_ts, nrec_of, parse_article, parse_index, parse_push_time, TW
from q3pipe.config import Config

REAL = Path(__file__).parent / "fixtures" / "real"


def read(name):
    return (REAL / name).read_text(encoding="utf-8")


def test_sources_recorded():
    src = json.loads(read("SOURCES.json"))
    assert src["files"]["index_full.html"]["status"] == 200
    assert src["files"]["article_404.html"]["status"] == 404
    assert src["over18_post"]["set_cookie"].startswith("over18=1")


def test_newest_page_stops_at_separator():
    entries, prev = parse_index(read("index_newest.html"), newest=True)
    assert prev == 38963  # 最新頁＝38964，「上頁」指向 index38963.html
    assert len(entries) == 6  # 分隔線下的置底公告不算
    assert all(e["aid"] and not e["deleted"] for e in entries)
    pinned, _ = parse_index(read("index_newest.html"), newest=False)
    assert len(pinned) > len(entries)  # 不切分隔線的話會把置底文也算進來


def test_full_page_has_20_slots_with_deleted_placeholder():
    entries, prev = parse_index(read("index_full.html"), newest=False)
    assert prev == 38957
    assert len(entries) == 20
    deleted = [e for e in entries if e["deleted"]]
    assert len(deleted) == 1 and deleted[0]["aid"] is None and "本文已被刪除" in deleted[0]["title"]
    ts = [e["ts"] for e in entries if e["ts"]]
    assert ts == sorted(ts)  # 不變量 (c)：相鄰 ID 時間戳不倒序


def test_article_pushes_and_nrec_match_list():
    aid = "M.1793703227.A.462"
    art = parse_article(read("article_pushes.html"), aid_ts(aid))
    assert art["board"] == "Gossiping" and art["title"].startswith("[新聞] 佛州私人飛機墜毀9死")
    assert (art["push"], art["boo"], art["arrow"], art["comment_count"]) == (45, 0, 32, 77)
    assert not art["truncated"]
    assert "※ 發信站" not in art["content"] and art["content"].startswith("媒體來源")
    first = art["pushes"][0]
    assert (first["tag"], first["user"], first["content"], first["ip"]) == ("→", "Shopeee", "私人飛機欸 有錢人", "36.233.146.96")
    assert first["comment_at"].astimezone(TW).strftime("%m/%d %H:%M") == "11/03 18:54"
    entries, _ = parse_index(read("index_full.html"), newest=False)
    listed = next(e for e in entries if e["aid"] == aid)
    assert listed["nrec"] == nrec_of(art["push"], art["boo"]) == 45  # 列表推文數＝推−噓


def test_404_and_over18_pages_are_not_articles():
    assert parse_article(read("article_404.html"), 1792887225) is None
    assert parse_article(read("over18.html"), 1792887225) is None
    with pytest.raises(ValueError):
        parse_index(read("over18.html"), newest=True)


def test_push_time_crosses_year():
    from datetime import datetime
    post = datetime(2026, 12, 31, 23, 50, tzinfo=TW)
    ip, at = parse_push_time("1.2.3.4 01/01 00:10", post)
    assert ip == "1.2.3.4" and at.astimezone(TW).year == 2027


class _Resp:
    def __init__(self, status, text="", url="https://www.ptt.cc/x", headers=None):
        self.status_code, self.text, self.url, self.headers = status, text, url, headers or {}
        self.content = text.encode("utf-8")


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers, self.cookies = {}, __import__("requests").cookies.RequestsCookieJar()

    def get(self, url, timeout):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_fetcher_backoff_retry_after_and_pause():
    cfg = Config(min_interval_s=0)
    waits = []
    s = _Session([_Resp(429, headers={"Retry-After": "30"}), _Resp(503), _Resp(200, "ok")])
    f = Fetcher(cfg, sleep=waits.append, session=s)
    r = f.get("https://www.ptt.cc/bbs/Gossiping/index.html", "list")
    assert r.status == 200 and waits == [30.0, 10.0]  # 有 Retry-After 照它，否則 5、10 秒
    s = _Session([_Resp(500)] * 6)
    waits.clear()
    f = Fetcher(cfg, sleep=waits.append, session=s)
    f.get("u1", "article_new")
    f.get("u2", "article_new")
    assert max(waits) >= cfg.pause_s  # 連續 3 次失敗：整體暫停


def test_fetcher_reconnects_after_idle_and_logs_each_failure():
    import requests
    now, closed, events = [0.0], [], []
    s = _Session([_Resp(200, "ok"), _Resp(200, "ok"), requests.ReadTimeout(), _Resp(503), _Resp(200, "ok")])
    s.close = lambda: closed.append(now[0])
    f = Fetcher(Config(min_interval_s=0), sleep=lambda x: now.__setitem__(0, now[0] + x), clock=lambda: now[0],
                session=s, log=lambda event, **kw: events.append((event, kw)))
    f.get("u", "list")
    now[0] += 30
    f.get("u", "list")
    assert closed == []                                # 閒置 30 秒：沿用原本的連線
    now[0] += 3600
    assert f.get("u", "list").status == 200            # 閒置一小時：先關掉舊連線再送；逾時、503 之後第 3 次成功
    assert closed == [3630.0]                          # 重試的間隔只有 5、10 秒，不會再關一次
    assert [(e, kw["attempt"], kw["status"], kw["error"]) for e, kw in events] == [
        ("request_failed", 1, None, "ReadTimeout"), ("request_failed", 2, 503, "HTTP 503")]
    g = Fetcher(Config(min_interval_s=0), sleep=lambda x: None, clock=lambda: now[0], session=_Session([_Resp(200, "ok")] * 2))
    g.get("u", "list")
    now[0] += 3600
    assert g.get("u", "list").status == 200            # 測試用、沒有 close 的 session 也不會出錯


def test_fetcher_treats_over18_page_as_failure():
    cfg = Config(min_interval_s=0, max_attempts=1)
    s = _Session([_Resp(200, read("over18.html"))])
    r = Fetcher(cfg, sleep=lambda x: None, session=s).get("u", "list")
    assert r.status is None and r.error == "over18_gate"
    assert Fetcher(cfg, session=_Session([])).s.cookies.get("over18") == "1"


def test_404_is_not_retried():
    cfg = Config(min_interval_s=0)
    s = _Session([_Resp(404, read("article_404.html"))])
    waits = []
    r = Fetcher(cfg, sleep=waits.append, session=s).get("u", "article_refresh")
    assert r.status == 404 and waits == []
