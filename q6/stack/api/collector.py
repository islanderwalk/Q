"""真實來源採集器：PTT 看板 Atom、YouTube 頻道（Data API；RSS 只限 POC）、中央社／公視 RSS → 當成 parser 的輸出：文章本體寫 ES、
訊息發到 Pub/Sub 主題 parsed-posts，串流斷詞服務 v0／v1／v2 各自斷詞寫回同一篇（與 scripts/publish_channels.py 同一條路，
差別只是來源是現在正在發生的貼文，不是重播樣本）。

規則：只抓站方公開給閱讀器的 feed；不登入、不存 cookie（Gossiping 只多一個 over18=1 的年齡同意標頭）；
同一主機兩次請求至少隔 GAP 秒；帶 ETag／If-Modified-Since（304 就什麼都不做）；429／403／5xx 退避（間隔加倍，最多 6 小時）；
透明 UA（寫明是哪個 repo 的採集器）。PM 頁「立刻抓一次」→ collector_control.run_now；「暫停」→ paused。

每次請求（計畫書 P0 第 3、4 項）：
- 一行結構化 JSON（event=collect）：outcome（ok／empty／not_modified／login_wall／content_unavailable／rate_limited／forbidden／
  server_error／network_error／parse_error／skipped）、posts_parsed、posts_new、posts_updated、final_url（有轉址才有）、
  ttfb_ms（送出到收到標頭）、total_ms（含下載內文）、robots（allow／disallow／unknown）。本機由 Alloy 收進 Loki，GCP 上進 Cloud Logging。
- Prometheus 指標在 COLLECTOR_METRICS_PORT（預設 9111）：collector_requests_total{feed,channel,outcome}、
  collector_posts_{parsed,new,updated}_total、collector_request_seconds{channel,phase}、collector_first_seen_lag_seconds{channel}
  （抓到時間 − 發文時間，只算發文 24 小時內，桶有 2700／3600）、collector_last_ok_timestamp_seconds{feed}、collector_backoff_level{feed}、
  collector_robots_info{host,sha}、collector_youtube_quota_units_total。label 啟動時預建。
- collector_state：last_ok_at 只在成功時更新（失敗只更新 last_poll_at）；退避等級與 next_poll_at 寫表，重啟後接著退避，不會重新打爆對方。
- 原文改了（content_sha 不同）：重寫本體並重發斷詞（訊息屬性 edited=1）；feed 看不到刪文，tombstone 要靠會逛頁面的爬蟲（這裡沒有）。
- YouTube：youtube.com/robots.txt 禁止 /feeds/videos.xml，預設改走 Data API v3 的 playlistItems.list（每次 1 單位配額；金鑰只從環境變數
  YOUTUBE_API_KEY 或檔案 YOUTUBE_API_KEY_FILE 讀，日誌與資料表一律遮掉）；沒有金鑰時跳過，除非 YOUTUBE_FEED_POC=1 明確要走 RSS（只限 POC）。
- 各主機的 robots.txt 每天抓一次記內容雜湊，一變就寫 live_events 與計數（平台改規則要看得到）。
COLLECT_SKIP=ptt（逗號分隔：feed 名稱前綴、渠道或主機）可跳過某些來源，例如在不該抓 PTT 的機器上。
"""
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from prometheus_client import Counter, Gauge, Histogram, start_http_server

import ops

ES_URL = os.environ.get("ES_URL", "http://es:9200").rstrip("/")
INDEX = os.environ.get("ES_INDEX", "posts")
PG_DSN = os.environ.get("PG_DSN", "postgresql://qsearch:qsearch@pg:5432/qsearch")
PROJECT = os.environ.get("PUBSUB_PROJECT", "qsearch-local")
EMULATOR = os.environ.get("PUBSUB_EMULATOR_HOST", "")
TOPIC = os.environ.get("PARSED_TOPIC", "parsed-posts")
FEEDS_PATH = Path(os.environ.get("FEEDS", str(Path(__file__).resolve().parent / "feeds.json")))
LIVE_SQL = Path(os.environ.get("LIVE_SQL", str(Path(__file__).resolve().parent / "live.sql")))
SKIP = [s.strip() for s in os.environ.get("COLLECT_SKIP", "").split(",") if s.strip()]
GAP = float(os.environ.get("COLLECT_GAP_SEC", "2.5"))
UA = os.environ.get("COLLECT_UA", "qsearch-q6-collector/1.0 (+https://github.com/islanderwalk/claude-code; feed reader; polite)")
METRICS_PORT = int(os.environ.get("COLLECTOR_METRICS_PORT", "9111"))
YT_FEED_POC = os.environ.get("YOUTUBE_FEED_POC", "") == "1"
YT_API = "https://www.googleapis.com/youtube/v3/playlistItems"
MAX_BACKOFF = 6 * 3600
LAG_WINDOW = 24 * 3600
ROBOTS_TTL = 24 * 3600
NS = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/", "yt": "http://www.youtube.com/xml/schemas/2015"}
BR_RE = re.compile(r"<\s*(?:br|/p|/div|/li|/tr|/h\d)\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]+>")
PTT_URL_RE = re.compile(r"/bbs/([^/]+)/(M\.\d+\.A\.[0-9A-Fa-f]+)\.html")
CNA_URL_RE = re.compile(r"cna\.com\.tw/news/[a-z]+/(\d+)\.aspx", re.I)
LOGIN_RE = re.compile(r"/login|accounts\.google\.com|consent\.youtube\.com|/checkpoint|/accounts/login|/signin", re.I)
OUTCOMES = ("ok", "empty", "not_modified", "login_wall", "content_unavailable", "rate_limited", "forbidden", "server_error",
            "network_error", "parse_error", "skipped")
GOOD = ("ok", "empty", "not_modified")

REQ = Counter("collector_requests_total", "採集器每次請求的結果（outcome：ok／empty／not_modified／login_wall／content_unavailable／rate_limited／forbidden／server_error／network_error／parse_error／skipped）", ["feed", "channel", "outcome"])
PARSED = Counter("collector_posts_parsed_total", "解析出的篇數（含已在 ES 裡的）", ["feed", "channel"])
NEW = Counter("collector_posts_new_total", "寫進 ES 並發到 parsed-posts 的新篇數", ["feed", "channel"])
UPDATED = Counter("collector_posts_updated_total", "原文改了（content_sha 不同）而重寫本體、重發斷詞的篇數", ["feed", "channel"])
REQ_SEC = Histogram("collector_request_seconds", "請求耗時拆段：ttfb（送出到收到狀態列與標頭）、total（含下載內文）", ["channel", "phase"],
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
LAG = Histogram("collector_first_seen_lag_seconds", "首抓延遲＝抓到時間 − 發文時間，只算發文 24 小時內的新文章；桶有 2700（45 分）與 3600（60 分）", ["channel"],
                buckets=(60, 300, 600, 900, 1800, 2700, 3600, 7200, 14400, 43200, 86400))
LAST_OK = Gauge("collector_last_ok_timestamp_seconds", "每個 feed 最後一次成功（ok／empty／not_modified）的 Unix 時間；從未成功＝0；停用的 feed 沒有這條序列", ["feed"])
ENABLED = Gauge("collector_feed_enabled", "feed 會不會被抓（0＝沒有 YouTube API key 而跳過；COLLECT_SKIP 的 feed 不在 feeds 清單裡）；FeedDead／FeedStale 只看 1 的", ["feed", "channel"])
BACKOFF = Gauge("collector_backoff_level", "退避等級（0＝正常；每次失敗加 1、間隔加倍，最多 6 小時）", ["feed"])
ROBOTS = Gauge("collector_robots_info", "各主機 robots.txt 的內容雜湊（值恆為 1；雜湊一變舊序列就移除）", ["host", "sha"])
ROBOTS_CHANGED = Counter("collector_robots_changed_total", "robots.txt 內容變了的次數", ["host"])
YT_QUOTA = Counter("collector_youtube_quota_units_total", "YouTube Data API 配額用量（playlistItems.list 每次 1 單位；自己記，再對 Cloud Monitoring 的配額指標）")

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "application/atom+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
                  "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
_last_hit: dict = {}
_robots: dict = {}          # host → {"at", "sha", "rules": [disallow prefixes for *]}


def youtube_key() -> str:
    """只從環境變數或檔案讀，絕不寫進程式、日誌、資料表。"""
    k = os.environ.get("YOUTUBE_API_KEY", "").strip()
    f = os.environ.get("YOUTUBE_API_KEY_FILE", "").strip()
    if not k and f and Path(f).exists():
        k = Path(f).read_text(encoding="utf-8").strip()
    return k


YT_KEY = youtube_key()


def redact(s) -> str:
    """任何可能含金鑰的字串（例外訊息、轉址網址）先遮掉再記錄。"""
    s = str(s or "")
    return s.replace(YT_KEY, "***") if YT_KEY else s


def log(event, severity="INFO", **f):
    rec = {"severity": severity, "event": event, "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **f}
    sys.stdout.write(redact(json.dumps(rec, ensure_ascii=False, default=str)) + "\n")
    sys.stdout.flush()


# ---------- 解析（純函式，tests 直接測） ----------
def html_to_text(s: str) -> str:
    s = BR_RE.sub("\n", s or "")
    s = html.unescape(TAG_RE.sub("", s))
    return re.sub(r"\n{3,}", "\n\n", s.replace("\r", "")).strip()


def parse_time(s: str):
    """Atom 的 ISO 8601 或 RSS 的 RFC 822 → +08:00 ISO 字串；解析不了回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ops.TW).isoformat(timespec="seconds")


def _text(el, path, ns=NS):
    x = el.find(path, ns)
    return (x.text or "").strip() if x is not None and x.text else ""


def _atom_link(entry):
    for l in entry.findall("a:link", NS):
        if l.get("rel") in (None, "alternate") and l.get("href"):
            return l.get("href")
    return _text(entry, "a:id")


def post_identity(feed: dict, url: str, published_at: str, video_id: str = ""):
    """與 data/channels 樣本同一套 post_id 規則，重播樣本與現抓的同一篇才會對上同一份文件。"""
    ch = feed["channel"]
    if ch == "ptt":
        m = PTT_URL_RE.search(url or "")
        if not m:
            return None, feed.get("source")
        return f"ptt-{m.group(2)}", m.group(1)
    if ch == "youtube":
        return (video_id or None), feed.get("source")
    m = CNA_URL_RE.search(url or "")
    if m:
        return f"CNA/{(published_at or '')[:10] or 'undated'}/{m.group(1)}", feed.get("source")
    if "pts.org.tw" in (url or ""):
        return url, feed.get("source")
    return f"media-{hashlib.sha1((url or '').encode('utf-8')).hexdigest()[:16]}", feed.get("source")


def parse_feed(xml_bytes: bytes, feed: dict) -> list:
    """Atom（PTT、YouTube）或 RSS 2.0（中央社、公視）→ [post]；每篇都走 ops.make_post 的欄位檢查。"""
    root = ET.fromstring(xml_bytes)
    posts = []
    if root.tag.endswith("feed"):
        for e in root.findall("a:entry", NS):
            url = _atom_link(e)
            title = _text(e, "a:title")
            when = parse_time(_text(e, "a:published") or _text(e, "a:updated"))
            vid = _text(e, "yt:videoId")
            author = _text(e, "a:author/a:name")
            if feed["channel"] == "youtube":
                body = _text(e, "media:group/media:description")
                url = url or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
                sub = "video"
            else:
                body = html_to_text(_text(e, "a:content") or _text(e, "a:summary"))
                sub = "article"
            pid, source = post_identity(feed, url, when, vid)
            if not pid or not when:
                continue
            posts.append(ops.make_post(feed["channel"], f"{title}\n{body}".strip(), url, source, when, pid, sub, "feed", author=author or None))
    else:
        for it in root.findall(".//item"):
            url = _text(it, "link") or _text(it, "guid")
            title = _text(it, "title")
            when = parse_time(_text(it, "pubDate") or _text(it, "{http://purl.org/dc/elements/1.1/}date"))
            body = html_to_text(_text(it, "description"))
            author = _text(it, "author") or _text(it, "{http://purl.org/dc/elements/1.1/}creator")
            pid, source = post_identity(feed, url, when)
            if not pid or not when:
                continue
            posts.append(ops.make_post(feed["channel"], f"{title}\n{body}".strip(), url, source, when, pid, "news", "feed", author=author or None))
    return posts


def parse_youtube_api(payload: dict, feed: dict) -> list:
    """YouTube Data API v3 playlistItems.list（上傳清單 UU…）→ [post]；與 RSS 同一套 post_id（videoId）。"""
    posts = []
    for it in payload.get("items", []):
        sn = it.get("snippet") or {}
        vid = ((sn.get("resourceId") or {}).get("videoId")) or ""
        when = parse_time(sn.get("publishedAt"))
        if not vid or not when:
            continue
        text = f"{sn.get('title', '')}\n{sn.get('description', '')}".strip()
        posts.append(ops.make_post("youtube", text, f"https://www.youtube.com/watch?v={vid}", feed.get("source"), when, vid, "video", "api",
                                   author=sn.get("channelTitle") or None))
    return posts


def youtube_playlist(feed_url: str) -> str:
    """頻道 id UC… 的上傳清單是 UU…（同一串尾碼）。"""
    cid = (parse_qs(urlsplit(feed_url).query).get("channel_id") or [""])[0]
    return "UU" + cid[2:] if cid.startswith("UC") else ""


def classify(status, final_url=None, posts_parsed=None, exc=None, parse_failed=False) -> str:
    """每次請求歸一個 outcome：HTTP 200 不等於抓到（回 200 卻 0 篇、被轉去登入頁都要分開算）。"""
    if exc is not None:
        return "network_error"
    if status is None:
        return "skipped"
    if status == 304:
        return "not_modified"
    if status == 429:
        return "rate_limited"
    if status == 403:
        return "forbidden"
    if status == 401:
        return "login_wall"
    if status >= 500:
        return "server_error"
    if status in (404, 410) or 400 <= status < 500:
        return "content_unavailable"
    if final_url and LOGIN_RE.search(final_url):
        return "login_wall"
    if parse_failed:
        return "parse_error"
    if not posts_parsed:
        return "empty"
    return "ok"


def first_seen_lag(published_at: str, now: datetime):
    """抓到時間 − 發文時間（秒）；沒有發文時間、未來時間或超過 24 小時的舊文回 None（不算進首抓延遲）。"""
    if not published_at:
        return None
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    lag = (now - pub).total_seconds()
    return lag if 0 <= lag <= LAG_WINDOW else None


def robots_verdict(rules: list, path: str) -> str:
    """只看 User-agent: * 的 Disallow 前綴（含 * 萬用字元、$ 結尾）；不做完整 robots 解析。"""
    if rules is None:
        return "unknown"
    for rule in rules:
        if not rule:
            continue
        pat = re.escape(rule).replace(r"\*", ".*")
        if pat.endswith(r"\$"):
            pat = pat[:-2] + "$"
        if re.match(pat, path):
            return "disallow"
    return "allow"


def parse_robots(text: str) -> list:
    """User-agent: * 區塊的 Disallow 路徑清單。"""
    rules, current_star = [], False
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        if k.lower() == "user-agent":
            current_star = v == "*"
        elif k.lower() == "disallow" and current_star and v:
            rules.append(v)
    return rules


def skipped(feed: dict) -> bool:
    host = urlsplit(feed["url"]).netloc
    return any(feed["name"].startswith(s) or feed["channel"] == s or host.endswith(s) for s in SKIP)


def enabled(feed: dict) -> bool:
    """會真的去抓的 feed。YouTube 沒有 API key（也沒開 RSS POC）就只記 skipped，不算 dead／stale。"""
    return not (feed["channel"] == "youtube" and not YT_KEY and not YT_FEED_POC)


def precreate_labels(feeds: list):
    """帶 label 的 counter 啟動時先建成 0，第一次 login_wall／429 才不會被 increase() 算成 0。
    停用的 feed 不建 last_ok／backoff 序列：真實環境驗證時三個 YouTube feed 因為沒有 key、last_ok 永遠是 0，
    CollectorFeedDead（page）啟動一小時後就響。"""
    for f in feeds:
        for o in OUTCOMES:
            REQ.labels(f["name"], f["channel"], o)
        PARSED.labels(f["name"], f["channel"])
        NEW.labels(f["name"], f["channel"])
        UPDATED.labels(f["name"], f["channel"])
        ENABLED.labels(f["name"], f["channel"]).set(1 if enabled(f) else 0)
        if enabled(f):
            LAST_OK.labels(f["name"])
            BACKOFF.labels(f["name"])
    for ch in {f["channel"] for f in feeds}:
        LAG.labels(ch)
        for ph in ("ttfb", "total"):
            REQ_SEC.labels(ch, ph)


# ---------- 寫入端 ----------
def _pg():
    import psycopg
    return psycopg.connect(PG_DSN, autocommit=True)


def ensure_tables():
    if LIVE_SQL.exists():
        with _pg() as c:
            c.execute(LIVE_SQL.read_text(encoding="utf-8"))


def control():
    with _pg() as c:
        row = c.execute("SELECT run_now, paused, poll_sec FROM collector_control WHERE id=1").fetchone()
    return {"run_now": row[0], "paused": row[1], "poll_sec": row[2]} if row else {"run_now": False, "paused": False, "poll_sec": 300}


def clear_run_now():
    with _pg() as c:
        c.execute("UPDATE collector_control SET run_now=false, updated_at=now() WHERE id=1")


def save_state(feed: dict, **cols):
    with _pg() as c:
        c.execute("""INSERT INTO collector_state (feed, channel, source, url) VALUES (%s,%s,%s,%s)
                     ON CONFLICT (feed) DO NOTHING""", (feed["name"], feed["channel"], feed.get("source"), feed["url"]))
        if cols:
            sets = ", ".join(f"{k}=%s" for k in cols)
            c.execute(f"UPDATE collector_state SET {sets} WHERE feed=%s", (*cols.values(), feed["name"]))


def load_states(feeds: list) -> dict:
    """重啟後從 collector_state 接回 ETag／Last-Modified／退避等級／下次輪詢時間（重啟不會把對方重新打一輪）。"""
    states = {f["name"]: {} for f in feeds}
    try:
        with _pg() as c:
            rows = c.execute("SELECT feed, etag, last_modified, backoff_level, next_poll_at, last_ok_at FROM collector_state").fetchall()
        by_name = {f["name"]: f for f in feeds}
        for feed, etag, lm, lvl, nxt, ok_at in rows:
            if feed in states:
                states[feed].update(etag=etag, last_modified=lm, backoff=lvl or 0, next=nxt.timestamp() if nxt else None,
                                    last_ok=ok_at.timestamp() if ok_at else None)
                if not enabled(by_name[feed]):
                    continue
                if ok_at:
                    LAST_OK.labels(feed).set(ok_at.timestamp())
                BACKOFF.labels(feed).set(lvl or 0)
    except Exception as e:
        log("load_states_failed", "WARNING", error=str(e)[:200])
    return states


def event(kind: str, actor: str, **detail):
    try:
        with _pg() as c:
            c.execute("INSERT INTO live_events (kind, actor, detail) VALUES (%s,%s,%s)", (kind, actor, redact(json.dumps(detail, ensure_ascii=False, default=str))))
    except Exception as e:
        log("event_failed", "WARNING", error=str(e)[:200])


def index_exists() -> bool:
    return S.head(f"{ES_URL}/{INDEX}", timeout=15).status_code == 200


def existing_docs(ids: list) -> dict:
    """ES 裡已有的 post_id → content_sha（舊文件沒有這個欄位時為 None）。"""
    if not ids:
        return {}
    r = S.post(f"{ES_URL}/{INDEX}/_mget?_source=content_sha", json={"ids": ids}, timeout=60)
    r.raise_for_status()
    return {d["_id"]: (d.get("_source") or {}).get("content_sha") for d in r.json()["docs"] if d.get("found")}


def parser_write(posts: list):
    """parser 自己那一段：只寫文章本體欄位（doc_as_upsert），斷詞欄位留給斷詞服務。"""
    lines = []
    for p in posts:
        lines.append(json.dumps({"update": {"_index": INDEX, "_id": p["post_id"], "retry_on_conflict": 3}}))
        lines.append(json.dumps({"doc": ops.base_doc(p), "doc_as_upsert": True}, ensure_ascii=False))
    r = S.post(f"{ES_URL}/_bulk", data=("\n".join(lines) + "\n").encode("utf-8"), headers={"Content-Type": "application/x-ndjson"}, timeout=120)
    r.raise_for_status()
    res = r.json()
    return sum(1 for it in res["items"] if "error" in it["update"])


def polite_wait(url: str):
    host = urlsplit(url).netloc
    wait = _last_hit.get(host, 0) + GAP - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()


# ---------- robots.txt ----------
def robots_for(url: str) -> dict:
    """每個主機每天抓一次 robots.txt：內容雜湊一變就寫事件與計數；回 {sha, rules}。抓不到回 rules=None（unknown）。"""
    host = urlsplit(url).netloc
    cur = _robots.get(host)
    if cur and time.time() - cur["at"] < ROBOTS_TTL:
        return cur
    polite_wait(f"https://{host}/robots.txt")
    entry = {"at": time.time(), "sha": None, "rules": None}
    try:
        r = S.get(f"https://{host}/robots.txt", timeout=20)
        if r.status_code == 200:
            entry["sha"] = hashlib.sha256(r.content).hexdigest()[:16]
            entry["rules"] = parse_robots(r.text)
        elif r.status_code == 404:
            entry["sha"], entry["rules"] = "none", []        # 沒有 robots.txt＝全部允許
    except Exception as e:
        log("robots_fetch_failed", "WARNING", host=host, error=str(e)[:200])
    if entry["sha"]:
        old = (cur or {}).get("sha") or _robots_sha_in_pg(host)
        if old and old != entry["sha"]:
            ROBOTS_CHANGED.labels(host).inc()
            event("robots_changed", "collector", host=host, old_sha=old, new_sha=entry["sha"])
            log("robots_changed", "WARNING", host=host, old_sha=old, new_sha=entry["sha"])
            try:
                ROBOTS.remove(host, old)
            except KeyError:
                pass
        ROBOTS.labels(host, entry["sha"]).set(1)
        try:
            with _pg() as c:
                c.execute("""INSERT INTO collector_robots (host, sha, fetched_at, changed_at) VALUES (%s,%s,now(), CASE WHEN %s THEN now() ELSE NULL END)
                             ON CONFLICT (host) DO UPDATE SET sha=EXCLUDED.sha, fetched_at=now(),
                             changed_at=CASE WHEN collector_robots.sha<>EXCLUDED.sha THEN now() ELSE collector_robots.changed_at END""",
                          (host, entry["sha"], bool(old and old != entry["sha"])))
        except Exception as e:
            log("robots_save_failed", "WARNING", error=str(e)[:200])
    _robots[host] = entry
    return entry


def _robots_sha_in_pg(host: str):
    try:
        with _pg() as c:
            row = c.execute("SELECT sha FROM collector_robots WHERE host=%s", (host,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


# ---------- 抓一個 feed ----------
def fetch(feed: dict, state: dict):
    """送請求：回 (response, posts, meta)。YouTube 走 Data API（有金鑰）或 RSS（YOUTUBE_FEED_POC=1）。"""
    headers = dict(feed.get("headers") or {})
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    meta = {"method": "feed"}
    if feed["channel"] == "youtube" and YT_KEY:
        meta["method"] = "api"
        polite_wait(YT_API)
        t0 = time.perf_counter()
        r = S.get(YT_API, params={"part": "snippet", "playlistId": youtube_playlist(feed["url"]), "maxResults": 50, "key": YT_KEY},
                  headers={"If-None-Match": state["etag"]} if state.get("etag") else {}, timeout=30)
        meta["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        YT_QUOTA.inc()
        posts = parse_youtube_api(r.json(), feed) if r.status_code == 200 else []
        return r, posts, meta
    polite_wait(feed["url"])
    t0 = time.perf_counter()
    r = S.get(feed["url"], headers=headers, timeout=30)
    meta["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    posts = parse_feed(r.content, feed) if r.status_code == 200 else []
    return r, posts, meta


def poll_feed(feed: dict, state: dict, pubsub: ops.PubSub, poll_sec: int) -> dict:
    """抓一個 feed：條件式請求 → 解析 → 只處理 ES 裡還沒有或原文改了的 → 寫本體＋發訊息。每次都記 outcome；回傳摘要（也寫進 collector_state）。"""
    now = datetime.now(timezone.utc)
    out = {"feed": feed["name"], "channel": feed["channel"], "method": "feed"}
    path = urlsplit(feed["url"]).path + ("?" + urlsplit(feed["url"]).query if urlsplit(feed["url"]).query else "")
    rb = robots_for(feed["url"])
    out["robots"] = robots_verdict(rb.get("rules"), path)
    if feed["channel"] == "youtube" and not YT_KEY and not YT_FEED_POC:
        out.update(outcome="skipped", reason="沒有 YOUTUBE_API_KEY／YOUTUBE_API_KEY_FILE；RSS 路徑被 youtube.com/robots.txt 禁止，只在 YOUTUBE_FEED_POC=1 時走")
        REQ.labels(feed["name"], feed["channel"], "skipped").inc()
        save_state(feed, last_poll_at=now, outcome="skipped", error=out["reason"], robots=out["robots"])
        log("collect", **out)
        return out
    r = None
    try:
        r, posts, meta = fetch(feed, state)
        out.update(method=meta["method"], status=r.status_code, total_ms=meta["total_ms"], ttfb_ms=round(r.elapsed.total_seconds() * 1000, 1))
        REQ_SEC.labels(feed["channel"], "ttfb").observe(r.elapsed.total_seconds())
        REQ_SEC.labels(feed["channel"], "total").observe(meta["total_ms"] / 1000)
        if meta["method"] == "feed" and r.url != feed["url"]:
            out["final_url"] = redact(r.url)
        outcome = classify(r.status_code, out.get("final_url"), len(posts))
        out["outcome"] = outcome
        if outcome == "not_modified":
            state.update(backoff=0, last_ok=now.timestamp())
            save_state(feed, last_poll_at=now, last_ok_at=now, last_status=304, last_new=0, error=None, next_poll_at=None, outcome=outcome,
                       backoff_level=0, robots=out["robots"], total_ms=out["total_ms"], ttfb_ms=out["ttfb_ms"])
        elif outcome in ("ok", "empty"):
            out["posts_parsed"] = len(posts)
            PARSED.labels(feed["name"], feed["channel"]).inc(len(posts))
            have = existing_docs([p["post_id"] for p in posts])
            new = [p for p in posts if p["post_id"] not in have]
            changed = [p for p in posts if p["post_id"] in have and have[p["post_id"]] and have[p["post_id"]] != ops.content_sha(p["text"])]
            backfill_sha = [p for p in posts if p["post_id"] in have and not have[p["post_id"]]]   # 舊文件還沒有 content_sha：只補欄位、不重發
            errors = parser_write(new + changed + backfill_sha) if (new or changed or backfill_sha) else 0
            # 首抓延遲只在「上次成功抓過之後」的新文章上量：feed 第一次成功（從沒抓過、或 collector_state 被清掉）抓到的是整個 feed 的歷史，
            # 不是我們慢，不算（真實環境驗證：冷啟動第一輪把 24 小時內的舊文全算成延遲，CollectorFirstSeenLagCritical 直接 page）
            first_success = state.get("last_ok") is None
            lag_skipped = 0
            for p in new:
                pubsub.publish(TOPIC, p, channel=p["channel"], post_id=p["post_id"])
                lag = first_seen_lag(p.get("published_at"), now)
                if lag is None:
                    continue
                if first_success:
                    lag_skipped += 1
                else:
                    LAG.labels(feed["channel"]).observe(lag)
            if lag_skipped:
                out["lag_skipped_first_success"] = lag_skipped
            for p in changed:
                pubsub.publish(TOPIC, p, channel=p["channel"], post_id=p["post_id"], edited="1")
            NEW.labels(feed["name"], feed["channel"]).inc(len(new))
            UPDATED.labels(feed["name"], feed["channel"]).inc(len(changed))
            out.update(posts_new=len(new), posts_updated=len(changed), already=len(have), write_errors=errors,
                       newest=max((p["published_at"] for p in new), default=None))
            state.update(etag=r.headers.get("ETag"), last_modified=r.headers.get("Last-Modified"), backoff=0, last_ok=now.timestamp())
            save_state(feed, last_poll_at=now, last_ok_at=now, last_status=r.status_code, items_seen=len(posts), last_new=len(new), error=None,
                       next_poll_at=None, etag=state["etag"], last_modified=state["last_modified"], outcome=outcome, backoff_level=0,
                       posts_parsed=len(posts), final_url=out.get("final_url"), robots=out["robots"], total_ms=out["total_ms"], ttfb_ms=out["ttfb_ms"])
            with _pg() as c:
                c.execute("UPDATE collector_state SET total_new=total_new+%s WHERE feed=%s", (len(new), feed["name"]))
        else:
            raise RuntimeError(f"HTTP {r.status_code}（{outcome}）")
        LAST_OK.labels(feed["name"]).set(now.timestamp())
        BACKOFF.labels(feed["name"]).set(0)
        log("collect", **out)
    except ET.ParseError as e:
        out["outcome"] = "parse_error"
        _fail(feed, state, out, now, poll_sec, e)
    except Exception as e:
        if "outcome" not in out or out["outcome"] in GOOD:
            out["outcome"] = classify(getattr(r, "status_code", None) if r is not None else None, out.get("final_url"), None,
                                      exc=e if isinstance(e, requests.RequestException) else None)
            if out["outcome"] in GOOD:          # 200 之後才失敗（寫 ES／發訊息）：記成 server_error 以外的獨立原因
                out["outcome"] = "network_error" if isinstance(e, requests.RequestException) else "parse_error"
        _fail(feed, state, out, now, poll_sec, e)
    REQ.labels(feed["name"], feed["channel"], out["outcome"]).inc()
    event("collect", "collector", **out)
    return out


def _fail(feed, state, out, now, poll_sec, e):
    """失敗：退避等級＋1（間隔加倍，最多 6 小時），寫表（不碰 last_ok_at），記一行 WARNING。"""
    state["backoff"] = min(state.get("backoff", 0) + 1, 8)
    delay = min(poll_sec * 2 ** state["backoff"], MAX_BACKOFF)
    state["next"] = time.time() + delay
    out.update(error=redact(str(e))[:200], backoff_sec=delay, backoff_level=state["backoff"])
    BACKOFF.labels(feed["name"]).set(state["backoff"])
    save_state(feed, last_poll_at=now, last_status=out.get("status"), last_new=0, error=out["error"], next_poll_at=now + timedelta(seconds=delay),
               outcome=out["outcome"], backoff_level=state["backoff"], final_url=out.get("final_url"), robots=out.get("robots"))
    log("collect", "WARNING", **out)


def load_feeds() -> tuple:
    spec = json.loads(FEEDS_PATH.read_text(encoding="utf-8"))
    feeds = [f for f in spec["feeds"] if not skipped(f)]
    for f in spec["feeds"]:
        if skipped(f):
            log("feed_skipped", feed=f["name"], reason=f"COLLECT_SKIP={','.join(SKIP)}")
    return feeds, int(spec.get("poll_sec", 300))


def run():
    ensure_tables()
    feeds, default_poll = load_feeds()
    precreate_labels(feeds)
    start_http_server(METRICS_PORT)
    pubsub = ops.PubSub(PROJECT, EMULATOR or None)
    pubsub.ensure(TOPIC)
    for f in feeds:
        save_state(f)
    states = load_states(feeds)
    with _pg() as c:
        c.execute("UPDATE collector_control SET poll_sec=%s WHERE id=1 AND poll_sec=300 AND %s<>300", (default_poll, default_poll))
    log("collector_start", feeds=[f["name"] for f in feeds], skip=SKIP, ua=UA, gap_sec=GAP, metrics_port=METRICS_PORT,
        youtube=("api" if YT_KEY else "rss-poc" if YT_FEED_POC else "skipped"))
    last_run = 0.0
    while True:
        try:
            ctl = control()
            due = ctl["run_now"] or time.time() - last_run >= ctl["poll_sec"]
            if due and not ctl["paused"]:
                if not index_exists():
                    for f in feeds:
                        save_state(f, error="posts 索引還沒建立（先跑 ./run.sh seed）", last_poll_at=datetime.now(timezone.utc))
                    log("index_missing", "WARNING")
                else:
                    forced = ctl["run_now"]
                    for f in feeds:
                        st = states[f["name"]]
                        if not forced and st.get("next") and time.time() < st["next"]:
                            continue   # 退避中（重啟後也接著退避）
                        poll_feed(f, st, pubsub, ctl["poll_sec"])
                last_run = time.time()
                if ctl["run_now"]:
                    clear_run_now()
        except Exception as e:
            log("collector_loop_error", "ERROR", error=str(e)[:300])
        time.sleep(3)


if __name__ == "__main__":
    run()
