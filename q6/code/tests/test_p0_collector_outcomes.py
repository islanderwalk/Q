"""採集器（計畫書 P0 第 3、4 項）：每次請求的 outcome 分類、首抓延遲、robots.txt 解析、YouTube Data API 路徑與金鑰遮蔽。
全部純函式或 monkeypatch 替身，不連網、不連 ES／PostgreSQL。"""
import json
import sys
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
                                       "server_error", "network_error", "parse_error", "skipped"}
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
