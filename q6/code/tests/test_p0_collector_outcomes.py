"""採集器（計畫書 P0 第 3、4 項）：每次請求的 outcome 分類、首抓延遲、robots.txt 解析、YouTube Data API 路徑與金鑰遮蔽。
全部純函式或 monkeypatch fake，不連網、不連 ES／PostgreSQL。"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from _p0_fakes import API, ensure_prometheus_client, has_series

ensure_prometheus_client()
requests = pytest.importorskip("requests")
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
import collector  # noqa: E402
import ops  # noqa: E402


# ---------- outcome ----------
@pytest.mark.parametrize("status,final,parsed,exc,pf,want", [
    (200, None, 12, None, False, "ok"),
    (200, None, 0, None, False, "empty"),
    (304, None, 0, None, False, "not_modified"),
    (200, "https://www.youtube.com/signin?next=/feeds", 0, None, False, "login_wall"),
    (200, "https://www.facebook.com/checkpoint/?next=x", 3, None, False, "login_wall"),
    (401, None, 0, None, False, "login_wall"),
    (429, None, 0, None, False, "rate_limited"),
    (403, None, 0, None, False, "forbidden"),
    (404, None, 0, None, False, "content_unavailable"),
    (410, None, 0, None, False, "content_unavailable"),
    (503, None, 0, None, False, "server_error"),
    (200, None, 0, None, True, "parse_error"),
    (None, None, None, requests.exceptions.SSLError("tls"), False, "network_error"),
    (None, None, None, None, False, "skipped"),
])
def test_classify(status, final, parsed, exc, pf, want):
    assert collector.classify(status, final, parsed, exc=exc, parse_failed=pf) == want


def test_outcome_vocabulary_is_fixed_and_precreated():
    feeds = [{"name": "ptt/Test", "channel": "ptt", "url": "https://www.ptt.cc/atom/Test.xml"}]
    collector.precreate_labels(feeds)
    for o in collector.OUTCOMES:
        assert has_series(collector.REQ, "ptt/Test", "ptt", o), o
    assert set(collector.OUTCOMES) == {"ok", "empty", "not_modified", "login_wall", "content_unavailable", "rate_limited", "forbidden",
                                       "quota_exceeded", "server_error", "network_error", "parse_error", "skipped"}
    assert has_series(collector.LAST_OK, "ptt/Test") and has_series(collector.BACKOFF, "ptt/Test")


# ---------- 首抓延遲 ----------
def test_first_seen_lag_only_counts_posts_published_within_24h():
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    assert collector.first_seen_lag("2026-11-02T19:15:00+08:00", now) == 2700          # 45 分鐘前（台北時間）
    assert collector.first_seen_lag("2026-11-01T11:59:00+00:00", now) is None           # 超過 24 小時的舊文不算
    assert collector.first_seen_lag("2026-11-02T12:30:00+00:00", now) is None           # 未來時間（時鐘不對）不算
    assert collector.first_seen_lag("", now) is None and collector.first_seen_lag("not-a-date", now) is None
    buckets = collector.LAG._upper_bounds if hasattr(collector.LAG, "_upper_bounds") else None
    if buckets is not None:
        assert 2700 in buckets and 3600 in buckets                                       # 45／60 分鐘的門檻要有桶，p95 才準


# ---------- robots.txt ----------
def test_parse_robots_only_star_block_and_verdict_prefix_wildcard_dollar():
    txt = "User-agent: Googlebot\nDisallow: /private\n\nUser-agent: *\nDisallow: /feeds/videos.xml\nDisallow: /tmp/*\nDisallow: /exact$\nAllow: /\n"
    rules = collector.parse_robots(txt)
    assert rules == ["/feeds/videos.xml", "/tmp/*", "/exact$"]
    assert collector.robots_verdict(rules, "/feeds/videos.xml?channel_id=UC1") == "disallow"
    assert collector.robots_verdict(rules, "/tmp/x/y") == "disallow"
    assert collector.robots_verdict(rules, "/exact") == "disallow" and collector.robots_verdict(rules, "/exactly") == "allow"
    assert collector.robots_verdict(rules, "/atom/Gossiping.xml") == "allow"
    assert collector.robots_verdict(None, "/anything") == "unknown"
    assert collector.robots_verdict([], "/anything") == "allow"


# ---------- YouTube ----------
def test_youtube_playlist_from_channel_id():
    assert collector.youtube_playlist("https://www.youtube.com/feeds/videos.xml?channel_id=UC5nwNW4KdC0SzrhF9BXEYOQ") == "UU5nwNW4KdC0SzrhF9BXEYOQ"
    assert collector.youtube_playlist("https://www.youtube.com/feeds/videos.xml") == ""


def test_parse_youtube_api_uses_same_post_id_as_rss_and_records_author():
    feed = {"name": "youtube/x", "channel": "youtube", "source": "X 新聞", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"}
    payload = {"items": [{"snippet": {"publishedAt": "2026-11-02T03:00:00Z", "title": "颱風假", "description": "說明", "channelTitle": "X 新聞",
                                      "resourceId": {"videoId": "vid123"}}},
                         {"snippet": {"publishedAt": "", "title": "no-time", "resourceId": {"videoId": "vid999"}}}]}
    posts = collector.parse_youtube_api(payload, feed)
    assert len(posts) == 1
    p = posts[0]
    assert p["post_id"] == "vid123" and p["url"] == "https://www.youtube.com/watch?v=vid123" and p["collect_method"] == "api"
    assert p["published_at"] == "2026-11-02T11:00:00+08:00" and p["author"] == "X 新聞" and p["text"] == "颱風假\n說明"
    doc = ops.base_doc(p)
    assert doc["author"] == "X 新聞" and doc["content_sha"] == ops.content_sha("颱風假\n說明") and len(doc["content_sha"]) == 16


def test_redact_hides_api_key_in_logs_and_errors(monkeypatch, capsys):
    monkeypatch.setattr(collector, "YT_KEY", "AIzaSECRETKEY123")
    assert collector.redact("GET https://www.googleapis.com/youtube/v3/playlistItems?key=AIzaSECRETKEY123 failed") == \
        "GET https://www.googleapis.com/youtube/v3/playlistItems?key=*** failed"
    collector.log("collect", error="HTTPSConnectionPool key=AIzaSECRETKEY123")
    out = capsys.readouterr().out
    assert "AIzaSECRETKEY123" not in out and "key=***" in out
    monkeypatch.setattr(collector, "YT_KEY", "")
    assert collector.redact("nothing") == "nothing"


def test_youtube_key_read_only_from_env_or_file(monkeypatch, tmp_path):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY_FILE", raising=False)
    assert collector.youtube_key() == ""
    f = tmp_path / "k.txt"
    f.write_text("  filekey\n", encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_API_KEY_FILE", str(f))
    assert collector.youtube_key() == "filekey"
    monkeypatch.setenv("YOUTUBE_API_KEY", "envkey")
    assert collector.youtube_key() == "envkey"


def test_youtube_without_key_is_skipped_not_fetched(monkeypatch):
    """沒有金鑰、沒開 POC：不打 youtube.com（robots 禁止 RSS 路徑），outcome=skipped 並寫表。"""
    monkeypatch.setattr(collector, "YT_KEY", "")
    monkeypatch.setattr(collector, "YT_FEED_POC", False)
    monkeypatch.setattr(collector, "robots_for", lambda url: {"sha": "x", "rules": ["/feeds/videos.xml"]})
    saved = {}
    monkeypatch.setattr(collector, "save_state", lambda feed, **cols: saved.update(cols))
    monkeypatch.setattr(collector, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該連網")))
    feed = {"name": "youtube/x", "channel": "youtube", "source": "X", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"}
    collector.precreate_labels([feed])
    out = collector.poll_feed(feed, {}, pubsub=None, poll_sec=300)
    assert out["outcome"] == "skipped" and out["robots"] == "disallow"
    assert saved["outcome"] == "skipped" and "last_ok_at" not in saved


# ---------- 失敗不更新 last_ok_at、退避寫表 ----------
class _Resp:
    def __init__(self, status, content=b"", url="https://www.ptt.cc/atom/Test.xml", headers=None):
        self.status_code, self.content, self.url, self.headers = status, content, url, headers or {}
        self.elapsed = timedelta(milliseconds=120)

    def json(self):
        return {}


def test_failure_keeps_last_ok_at_and_persists_backoff(monkeypatch):
    feed = {"name": "ptt/Test", "channel": "ptt", "source": "Test", "url": "https://www.ptt.cc/atom/Test.xml"}
    collector.precreate_labels([feed])
    monkeypatch.setattr(collector, "robots_for", lambda url: {"sha": "x", "rules": []})
    monkeypatch.setattr(collector, "polite_wait", lambda url: None)
    monkeypatch.setattr(collector, "event", lambda *a, **k: None)
    monkeypatch.setattr(collector.S, "get", lambda url, **k: _Resp(429))
    saved = []
    monkeypatch.setattr(collector, "save_state", lambda feed, **cols: saved.append(cols))
    state = {}
    out = collector.poll_feed(feed, state, pubsub=None, poll_sec=300)
    assert out["outcome"] == "rate_limited" and out["status"] == 429 and out["backoff_level"] == 1 and out["backoff_sec"] == 600
    assert state["backoff"] == 1 and state["next"] > 0
    assert "last_ok_at" not in saved[-1] and saved[-1]["backoff_level"] == 1 and saved[-1]["outcome"] == "rate_limited"
    assert saved[-1]["next_poll_at"] is not None


def test_success_records_outcome_fields_and_new_posts(monkeypatch):
    feed = {"name": "ptt/Test", "channel": "ptt", "source": "Test", "url": "https://www.ptt.cc/atom/Test.xml"}
    collector.precreate_labels([feed])
    now_tw = datetime.now(timezone(timedelta(hours=8))) - timedelta(minutes=20)
    atom = f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>颱風假</title><link href="https://www.ptt.cc/bbs/Test/M.1700000000.A.ABC.html"/>
      <published>{now_tw.isoformat(timespec="seconds")}</published><author><name>someone</name></author><content type="html">內文</content></entry></feed>'''
    monkeypatch.setattr(collector, "robots_for", lambda url: {"sha": "x", "rules": []})
    monkeypatch.setattr(collector, "polite_wait", lambda url: None)
    monkeypatch.setattr(collector, "event", lambda *a, **k: None)
    monkeypatch.setattr(collector.S, "get", lambda url, **k: _Resp(200, atom.encode("utf-8"), headers={"ETag": "e1"}))
    monkeypatch.setattr(collector, "existing_docs", lambda ids: {})
    monkeypatch.setattr(collector, "parser_write", lambda posts: 0)
    saved = []
    monkeypatch.setattr(collector, "save_state", lambda feed, **cols: saved.append(cols))

    class PG:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): return self
    monkeypatch.setattr(collector, "_pg", lambda: PG())
    published = []

    class PubSub:
        def publish(self, topic, p, **attrs): published.append((p["post_id"], attrs))
    lag_sum_before = collector.LAG.labels("ptt")._sum.get()
    out = collector.poll_feed(feed, {}, pubsub=PubSub(), poll_sec=300)
    assert out["outcome"] == "ok" and out["posts_parsed"] == 1 and out["posts_new"] == 1 and out["posts_updated"] == 0
    # 第一次成功抓到的是 feed 的歷史，不算首抓延遲；有上次成功時間的第二輪才量
    assert out["lag_skipped_first_success"] == 1 and collector.LAG.labels("ptt")._sum.get() == lag_sum_before
    out2 = collector.poll_feed(feed, {"last_ok": now_tw.timestamp()}, pubsub=PubSub(), poll_sec=300)
    assert "lag_skipped_first_success" not in out2 and collector.LAG.labels("ptt")._sum.get() > lag_sum_before
    assert out["ttfb_ms"] == 120.0 and out["total_ms"] >= 0 and "final_url" not in out and out["robots"] == "allow"
    assert published == [("ptt-M.1700000000.A.ABC", {"channel": "ptt", "post_id": "ptt-M.1700000000.A.ABC"})] * 2   # 兩輪各發一次
    assert saved[-1]["last_ok_at"] is not None and saved[-1]["backoff_level"] == 0 and saved[-1]["etag"] == "e1"
    assert json.dumps(out, ensure_ascii=False, default=str)   # 一行 JSON 日誌要能序列化


def test_detect_lag_is_time_since_last_ok_capped_by_publish_lag(monkeypatch):
    """我們這邊的延遲＝抓到 − 上一次成功檢查（文章最早在那之後才出現在 feed），超過發文到抓到就取後者；第一次成功兩個都不量。
    來源晚上架（中央社經 feedburner）時，發文到抓到很長、我們這邊只有一個輪詢週期：告警看後者。"""
    feed = {"name": "media/test", "channel": "media", "source": "test", "url": "https://feeds.example.com/rss"}
    collector.precreate_labels([feed])
    pub = datetime.now(timezone.utc) - timedelta(hours=3)
    rss = f"""<rss><channel><item><title>颱風</title><link>https://www.cna.com.tw/news/aipl/202611040076.aspx</link>
      <pubDate>{pub.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate><description>內文</description></item></channel></rss>"""
    monkeypatch.setattr(collector, "robots_for", lambda url: {"sha": "x", "rules": []})
    monkeypatch.setattr(collector, "polite_wait", lambda url: None)
    monkeypatch.setattr(collector, "event", lambda *a, **k: None)
    monkeypatch.setattr(collector.S, "get", lambda url, **k: _Resp(200, rss.encode("utf-8"), url=feed["url"]))
    monkeypatch.setattr(collector, "existing_docs", lambda ids: {})
    monkeypatch.setattr(collector, "parser_write", lambda posts: 0)
    monkeypatch.setattr(collector, "save_state", lambda feed, **cols: None)

    class PG:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): return self
    monkeypatch.setattr(collector, "_pg", lambda: PG())

    class PubSub:
        def publish(self, *a, **k): pass

    def sums():
        return collector.LAG.labels("media")._sum.get(), collector.DETECT.labels("media")._sum.get()
    lag0, det0 = sums()
    out = collector.poll_feed(feed, {}, pubsub=PubSub(), poll_sec=300)
    assert out["posts_new"] == 1 and out["lag_skipped_first_success"] == 1 and "since_last_ok_s" not in out and sums() == (lag0, det0)
    five_min_ago = datetime.now(timezone.utc).timestamp() - 300
    out = collector.poll_feed(feed, {"last_ok": five_min_ago}, pubsub=PubSub(), poll_sec=300)
    lag1, det1 = sums()
    assert 3 * 3600 - 5 <= lag1 - lag0 <= 3 * 3600 + 30          # 發文到抓到：3 小時（來源晚上架）
    assert 300 <= det1 - det0 <= 330 and 300 <= out["since_last_ok_s"] <= 330    # 我們這邊：一個輪詢週期
    long_ago = datetime.now(timezone.utc).timestamp() - 5 * 3600
    collector.poll_feed(feed, {"last_ok": long_ago}, pubsub=PubSub(), poll_sec=300)
    lag2, det2 = sums()
    assert abs((det2 - det1) - (lag2 - lag1)) < 1                 # 上次成功比發文還早：取發文到抓到
    buckets = collector.DETECT._upper_bounds if hasattr(collector.DETECT, "_upper_bounds") else None
    if buckets is not None:
        assert 2700 in buckets and 3600 in buckets


def test_disabled_youtube_feed_has_enabled_0_and_no_last_ok_series(monkeypatch):
    """沒有 API key 的 YouTube feed：collector_feed_enabled=0，不建 last_ok／backoff 序列（FeedDead／FeedStale 不會把它判成死掉）；
    有 key 時照常建。"""
    yt = {"name": "youtube/y", "channel": "youtube", "source": "Y", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCy"}
    ptt = {"name": "ptt/Y", "channel": "ptt", "source": "Y", "url": "https://www.ptt.cc/atom/Y.xml"}
    monkeypatch.setattr(collector, "YT_KEY", "")
    monkeypatch.setattr(collector, "YT_FEED_POC", False)
    collector.precreate_labels([yt, ptt])
    assert not collector.enabled(yt) and collector.enabled(ptt)
    assert not has_series(collector.LAST_OK, "youtube/y") and not has_series(collector.BACKOFF, "youtube/y")
    assert has_series(collector.LAST_OK, "ptt/Y") and has_series(collector.BACKOFF, "ptt/Y")
    assert collector.ENABLED.labels("youtube/y", "youtube")._value.get() == 0 and collector.ENABLED.labels("ptt/Y", "ptt")._value.get() == 1
    monkeypatch.setattr(collector, "YT_KEY", "k")
    yt2 = dict(yt, name="youtube/z")
    collector.precreate_labels([yt2])
    assert collector.enabled(yt2) and has_series(collector.LAST_OK, "youtube/z") and collector.ENABLED.labels("youtube/z", "youtube")._value.get() == 1


# ---------- YouTube 配額用完（與歷史爬蟲共用同一把金鑰） ----------
class _ApiResp(_Resp):
    def __init__(self, status, body):
        super().__init__(status, url=collector.YT_API)
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _api_error(reason):
    return {"error": {"code": 403, "message": "x", "errors": [{"domain": "youtube.quota", "reason": reason, "message": "x"}]}}


def test_quota_reset_at_is_pacific_0005_across_dst():
    """配額在太平洋時間午夜歸零：冬令＝UTC 08:05、夏令＝UTC 07:05；午夜到 00:05 之間取當天那一次。"""
    utc = timezone.utc
    assert collector.quota_reset_at(datetime(2026, 11, 4, 1, 58, tzinfo=utc)) == datetime(2026, 11, 4, 8, 5, tzinfo=utc)   # 台灣 09:58 → 16:05
    assert collector.quota_reset_at(datetime(2026, 11, 4, 8, 2, tzinfo=utc)) == datetime(2026, 11, 4, 8, 5, tzinfo=utc)    # 剛過午夜、還沒 00:05
    assert collector.quota_reset_at(datetime(2026, 11, 4, 8, 6, tzinfo=utc)) == datetime(2026, 11, 5, 8, 5, tzinfo=utc)
    assert collector.quota_reset_at(datetime(2026, 7, 1, 12, 0, tzinfo=utc)) == datetime(2026, 7, 2, 7, 5, tzinfo=utc)     # 夏令
    assert collector.quota_reset_at(datetime(2026, 3, 7, 12, 0, tzinfo=utc)) == datetime(2026, 3, 8, 8, 5, tzinfo=utc)     # 3/8 02:00 才進夏令
    assert collector.quota_reset_at(datetime(2026, 3, 8, 12, 0, tzinfo=utc)) == datetime(2026, 3, 9, 7, 5, tzinfo=utc)
    assert collector.quota_reset_at(datetime(2026, 11, 1, 12, 0, tzinfo=utc)) == datetime(2026, 11, 2, 8, 5, tzinfo=utc)   # 11/1 02:00 回冬令


def test_youtube_error_reasons_reads_api_error_body():
    assert collector.youtube_error_reasons(_ApiResp(403, _api_error("quotaExceeded"))) == {"quotaExceeded"}
    assert collector.youtube_error_reasons(_ApiResp(403, None)) == set()                  # 不是 JSON（例如代理的錯誤頁）
    assert collector.youtube_error_reasons(_ApiResp(403, {"error": "denied"})) == set()
    assert collector.youtube_error_reasons(_ApiResp(403, [])) == set()


def test_youtube_quota_exceeded_waits_for_reset_and_is_not_counted_as_blocked(monkeypatch):
    """403 quotaExceeded：outcome＝quota_exceeded（不是 forbidden，CollectorBlocked 不會響）、配額用完的 gauge＝1、
    下一次輪詢排到太平洋時間 00:05；其他 403 照舊是 forbidden 且 gauge＝0；之後成功一次 gauge 歸零。"""
    feed = {"name": "youtube/q", "channel": "youtube", "source": "Q", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCq"}
    monkeypatch.setattr(collector, "YT_KEY", "k")
    collector.precreate_labels([feed])
    assert has_series(collector.YT_QUOTA_OUT, "youtube/q") and collector.YT_QUOTA_OUT.labels("youtube/q")._value.get() == 0
    monkeypatch.setattr(collector, "robots_for", lambda url: {"sha": "x", "rules": ["/feeds/videos.xml"]})
    monkeypatch.setattr(collector, "polite_wait", lambda url: None)
    monkeypatch.setattr(collector, "event", lambda *a, **k: None)
    saved = []
    monkeypatch.setattr(collector, "save_state", lambda feed, **cols: saved.append(cols))
    resp = {"r": _ApiResp(403, _api_error("quotaExceeded"))}
    monkeypatch.setattr(collector.S, "get", lambda url, **k: resp["r"])
    forbidden_before = collector.REQ.labels("youtube/q", "youtube", "forbidden")._value.get()
    quota_before = collector.REQ.labels("youtube/q", "youtube", "quota_exceeded")._value.get()

    state = {}
    out = collector.poll_feed(feed, state, pubsub=None, poll_sec=300)
    reset = collector.quota_reset_at(datetime.now(timezone.utc))
    assert out["outcome"] == "quota_exceeded" and out["status"] == 403 and out["method"] == "api"
    assert collector.YT_QUOTA_OUT.labels("youtube/q")._value.get() == 1
    assert abs((saved[-1]["next_poll_at"] - reset).total_seconds()) < 5                    # 不是 10 分鐘後再打一次
    assert saved[-1]["outcome"] == "quota_exceeded" and "last_ok_at" not in saved[-1] and state["next"] > 0
    assert collector.REQ.labels("youtube/q", "youtube", "quota_exceeded")._value.get() == quota_before + 1
    assert collector.REQ.labels("youtube/q", "youtube", "forbidden")._value.get() == forbidden_before

    resp["r"] = _ApiResp(403, _api_error("forbidden"))                                     # 對方拒絕：照舊退避、算 forbidden
    out = collector.poll_feed(feed, {}, pubsub=None, poll_sec=300)
    assert out["outcome"] == "forbidden" and out["backoff_sec"] == 600
    assert collector.YT_QUOTA_OUT.labels("youtube/q")._value.get() == 0

    collector.YT_QUOTA_OUT.labels("youtube/q").set(1)
    resp["r"] = _ApiResp(200, {"items": []})
    monkeypatch.setattr(collector, "existing_docs", lambda ids: {})

    class PG:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): return self
    monkeypatch.setattr(collector, "_pg", lambda: PG())
    out = collector.poll_feed(feed, {}, pubsub=None, poll_sec=300)
    assert out["outcome"] == "empty" and collector.YT_QUOTA_OUT.labels("youtube/q")._value.get() == 0


def test_restart_while_waiting_for_quota_keeps_exhausted_flag(monkeypatch):
    """重啟時 collector_state 還在等配額歸零（outcome＝quota_exceeded、next_poll_at 在未來）：gauge 照樣是 1，FeedDead 不會誤響。"""
    yt = {"name": "youtube/r", "channel": "youtube", "source": "R", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCr"}
    yt2 = {"name": "youtube/s", "channel": "youtube", "source": "S", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCs"}
    monkeypatch.setattr(collector, "YT_KEY", "k")
    collector.precreate_labels([yt, yt2])
    now = datetime.now(timezone.utc)
    rows = [("youtube/r", None, None, 1, now + timedelta(hours=5), now - timedelta(hours=2), "quota_exceeded", "api"),
            ("youtube/s", None, None, 1, now - timedelta(minutes=1), now - timedelta(hours=2), "quota_exceeded", "api")]   # 已過歸零時間

    class PG:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): return self
        def fetchall(self): return rows
    monkeypatch.setattr(collector, "_pg", lambda: PG())
    states = collector.load_states([yt, yt2])
    assert states["youtube/r"]["next"] > now.timestamp()
    assert collector.YT_QUOTA_OUT.labels("youtube/r")._value.get() == 1
    assert collector.YT_QUOTA_OUT.labels("youtube/s")._value.get() == 0


# ---------- 退避：成功一次就結束（表和記憶體一起清）；換了抓法當成新的 feed ----------
FAKE_KEY = "AIzaFAKE0000000000000000000000000000000"   # 長度和格式像正式金鑰：日誌遮金鑰時才不會把欄位名稱裡的字母也遮掉


class _NoopPG:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a): return self
    def fetchall(self): return self.rows


class _NoPubSub:
    def publish(self, *a, **k):
        pass


def _quiet_poll(monkeypatch):
    monkeypatch.setattr(collector, "robots_for", lambda url: {"sha": "x", "rules": []})
    monkeypatch.setattr(collector, "polite_wait", lambda url: None)
    monkeypatch.setattr(collector, "event", lambda *a, **k: None)
    monkeypatch.setattr(collector, "existing_docs", lambda ids: {})
    monkeypatch.setattr(collector, "parser_write", lambda posts: 0)
    monkeypatch.setattr(collector, "_pg", lambda: _NoopPG())


def test_forced_success_while_backing_off_resumes_normal_rounds(monkeypatch):
    """退避中被「立刻抓一次」抓成功：表裡的 next_poll_at 清掉，記憶體裡的下一次輪詢時間也要一起清，下一輪一般輪次才會接著抓
    （端對端驗證：YouTube 帶著 RSS 時期的退避改走 API，手動抓成功後，一般輪次又跳過 37 分鐘，直到重啟）。304 也一樣。"""
    yt = {"name": "youtube/bo-f", "channel": "youtube", "source": "F", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCf"}
    ptt = {"name": "ptt/bo-F", "channel": "ptt", "source": "F", "url": "https://www.ptt.cc/atom/F.xml"}
    monkeypatch.setattr(collector, "YT_KEY", FAKE_KEY)
    collector.precreate_labels([yt, ptt])
    _quiet_poll(monkeypatch)
    saved = []
    monkeypatch.setattr(collector, "save_state", lambda feed, **cols: saved.append((feed["name"], cols)))
    monkeypatch.setattr(collector.S, "get", lambda url, **k: _ApiResp(200, {"items": []}) if url == collector.YT_API else _Resp(304, url=ptt["url"]))
    now = time.time()
    states = {"youtube/bo-f": {"backoff": 5, "next": now + 9600, "last_ok": now - 3600},
              "ptt/bo-F": {"backoff": 3, "next": now + 2400, "etag": "e0", "last_ok": now - 3600}}
    feeds = [ptt, yt]
    assert collector.sweep(feeds, states, _NoPubSub(), 300) == []                                     # 一般輪次：兩個都在退避
    assert collector.sweep(feeds, states, _NoPubSub(), 300, forced=True) == ["ptt/bo-F", "youtube/bo-f"]   # 立刻抓一次
    for name in ("ptt/bo-F", "youtube/bo-f"):
        assert states[name]["backoff"] == 0 and states[name]["next"] is None and not collector.backing_off(states[name])
    assert collector.sweep(feeds, states, _NoPubSub(), 300) == ["ptt/bo-F", "youtube/bo-f"]          # 下一輪一般輪次照常抓
    last = dict(saved)
    assert last["youtube/bo-f"]["outcome"] == "empty" and last["youtube/bo-f"]["method"] == "api"
    assert last["youtube/bo-f"]["next_poll_at"] is None and last["youtube/bo-f"]["backoff_level"] == 0
    assert last["ptt/bo-F"]["outcome"] == "not_modified" and last["ptt/bo-F"]["method"] == "feed" and last["ptt/bo-F"]["next_poll_at"] is None


def test_backing_off_only_until_next_poll_time():
    now = 1_000_000.0
    assert collector.backing_off({"next": now + 1}, now) and not collector.backing_off({"next": now - 1}, now)
    assert not collector.backing_off({}, now) and not collector.backing_off({"next": None}, now)


def test_failed_request_records_how_it_was_fetched(monkeypatch):
    """請求本身就失敗（逾時、連線斷）：outcome＝network_error，表裡照樣記對抓法（api），重啟時才分得出換了抓法沒有。"""
    feed = {"name": "youtube/bo-t", "channel": "youtube", "source": "T", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCt"}
    monkeypatch.setattr(collector, "YT_KEY", FAKE_KEY)
    collector.precreate_labels([feed])
    _quiet_poll(monkeypatch)
    saved = []
    monkeypatch.setattr(collector, "save_state", lambda feed, **cols: saved.append(cols))

    def timeout(url, **k):
        raise requests.exceptions.ReadTimeout("read timed out")
    monkeypatch.setattr(collector.S, "get", timeout)
    out = collector.poll_feed(feed, {}, pubsub=None, poll_sec=300)
    assert out["outcome"] == "network_error" and out["method"] == "api"
    assert saved[-1]["method"] == "api" and saved[-1]["backoff_level"] == 1 and saved[-1]["next_poll_at"] is not None


def test_method_change_starts_the_feed_fresh(monkeypatch):
    """YouTube 從 RSS 改走 Data API（method 從 feed 變成 api）：舊端點的退避、ETag／Last-Modified、上次成功時間都不帶過去，
    而且先寫回表（新端點第一次就失敗、再重啟時，不會又拿舊端點的上次成功時間算延遲）；新端點第一次成功不量首抓延遲。
    抓法沒變的照舊接著退避；method 是 NULL（加這個欄位之前的列）不知道是哪種抓法，也照舊。"""
    a = {"name": "youtube/mc-a", "channel": "youtube", "source": "A", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCa"}
    b = {"name": "youtube/mc-b", "channel": "youtube", "source": "B", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCb"}
    c = {"name": "ptt/mc-C", "channel": "ptt", "source": "C", "url": "https://www.ptt.cc/atom/C.xml"}
    monkeypatch.setattr(collector, "YT_KEY", FAKE_KEY)
    collector.precreate_labels([a, b, c])
    now = datetime.now(timezone.utc)
    rows = [("youtube/mc-a", '"rss"', "Tue, 03 Nov 2026 21:00:00 GMT", 6, now + timedelta(hours=5), now - timedelta(days=2), "server_error", "feed"),
            ("youtube/mc-b", None, None, 2, now + timedelta(minutes=10), now - timedelta(hours=1), "server_error", "api"),
            ("ptt/mc-C", '"e"', None, 1, now + timedelta(minutes=5), now - timedelta(minutes=20), "rate_limited", None)]
    saved, events = [], []
    monkeypatch.setattr(collector, "save_state", lambda feed, **cols: saved.append((feed["name"], cols)))
    monkeypatch.setattr(collector, "event", lambda kind, actor, **d: events.append((kind, d)))
    monkeypatch.setattr(collector, "_pg", lambda: _NoopPG(rows))
    states = collector.load_states([a, b, c])
    sa, sb, sc = states["youtube/mc-a"], states["youtube/mc-b"], states["ptt/mc-C"]
    assert sa == {"etag": None, "last_modified": None, "backoff": 0, "next": None, "last_ok": None}
    assert collector.BACKOFF.labels("youtube/mc-a")._value.get() == 0
    assert saved == [("youtube/mc-a", {"method": "api", "backoff_level": 0, "next_poll_at": None, "etag": None, "last_modified": None, "last_ok_at": None})]
    assert [k for k, _ in events] == ["collector_method_changed"] and events[0][1]["old"] == "feed" and events[0][1]["backoff_level"] == 6
    assert events[0][1]["last_ok_at"] is not None                                 # 舊端點的上次成功時間留在事件裡
    assert sb["backoff"] == 2 and collector.backing_off(sb) and collector.BACKOFF.labels("youtube/mc-b")._value.get() == 2
    assert sc["backoff"] == 1 and collector.backing_off(sc) and sc["etag"] == '"e"' and sc["last_ok"] is not None

    # 寫回表之後再重啟：抓法一樣，不會再重設一次
    rows[0] = ("youtube/mc-a", None, None, 1, now + timedelta(minutes=10), None, "network_error", "api")
    saved.clear()
    events.clear()
    again = collector.load_states([a])["youtube/mc-a"]
    assert again["backoff"] == 1 and again["last_ok"] is None and saved == [] and events == []

    # 新端點第一次成功：抓到的是整份上傳清單，不量首抓延遲
    _quiet_poll(monkeypatch)
    pub = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"items": [{"snippet": {"publishedAt": pub, "title": "颱風", "description": "", "channelTitle": "A", "resourceId": {"videoId": "mcA1"}}}]}
    monkeypatch.setattr(collector.S, "get", lambda url, **k: _ApiResp(200, body))
    lag0, det0 = collector.LAG.labels("youtube")._sum.get(), collector.DETECT.labels("youtube")._sum.get()
    out = collector.poll_feed(a, sa, pubsub=_NoPubSub(), poll_sec=300)
    assert out["outcome"] == "ok" and out["method"] == "api" and out["posts_new"] == 1 and out["lag_skipped_first_success"] == 1
    assert (collector.LAG.labels("youtube")._sum.get(), collector.DETECT.labels("youtube")._sum.get()) == (lag0, det0)
    assert saved[-1][1]["method"] == "api" and saved[-1][1]["last_ok_at"] is not None

