"""PTT 抓取與解析。

- Fetcher：單一連線、兩次請求至少隔 min_interval_s 秒、帶 over18 cookie。
  429／5xx／連線錯誤會退避再試（5、10 秒，有 Retry-After 就照它），連續失敗就整體暫停。
- parse_index：列表頁 → 每一格（含刪文佔位），最新頁只取分隔線以上（下面是置底文）。
- parse_article：文章頁 → 作者、標題、內文、每一則留言（推/噓/→、帳號、內容、IP、時間）。
"""
from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

TW = timezone(timedelta(hours=8))
AID_RE = re.compile(r"M\.(\d+)\.A\.([0-9A-F]+)")
PAGE_RE = re.compile(r"index(\d+)\.html")
PUSH_TIME_RE = re.compile(r"(?:(\d{1,3}(?:\.\d{1,3}){3})\s+)?(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?")
PER_PAGE = 20  # 填滿的列表頁固定 20 格（文章＋刪文佔位）


def aid_ts(aid: str | None) -> int | None:
    m = AID_RE.search(aid or "")
    return int(m.group(1)) if m else None


def parse_nrec(text: str) -> int:
    """列表推文數：爆＝100、X1..X9＝-10..-90、XX＝-100、空白＝0。"""
    text = (text or "").strip()
    if not text:
        return 0
    if text == "爆":
        return 100
    if text == "XX":
        return -100
    if text.startswith("X") and text[1:].isdigit():
        return -10 * int(text[1:])
    return int(text) if text.isdigit() else 0


def nrec_of(push: int, boo: int) -> int:
    """文章內的 推−噓 換成列表上的顯示值（→ 不計）。"""
    d = push - boo
    if d >= 100:
        return 100
    if d <= -100:
        return -100
    if d <= -10:
        return -10 * (abs(d) // 10)
    return max(d, 0)


@dataclass
class Response:
    status: int | None
    text: str
    nbytes: int
    latency_s: float
    url: str
    error: str | None = None


class Fetcher:
    def __init__(self, cfg, on_request=None, on_backoff=None, sleep=time.sleep, clock=time.monotonic, session=None,
                 log=None):
        self.cfg = cfg
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = cfg.user_agent
        # 八卦板的「已滿 18 歲」確認頁就是設這個 cookie；只放在記憶體
        self.s.cookies.set("over18", "1", domain=cfg.base_url.split("://", 1)[-1].split("/")[0])
        self.on_request = on_request or (lambda **kw: None)
        self.on_backoff = on_backoff or (lambda seconds: None)
        self.log = log or (lambda event, **kw: None)
        self.sleep, self.clock = sleep, clock
        self._last = None
        self._done = None  # 上一個請求結束的時間
        self.consecutive_failures = 0
        self.requests = 0

    def _drop_idle_connections(self):
        """閒置超過 reuse_idle_s 的連線不再沿用：關掉連線池，下一個請求重新連線，只多一次 TLS 握手。
        常駐運行時，每輪第一個請求離上一輪將近一小時，沿用舊連線會失敗：有時等滿 request_timeout_s 才逾時，
        有時幾秒內就被斷線。整體暫停 5 分鐘之後的下一個請求也會重新連線。"""
        if self._done is None or self.clock() - self._done <= self.cfg.reuse_idle_s:
            return
        close = getattr(self.s, "close", None)
        if close is not None:
            close()

    def _wait_turn(self):
        if self._last is not None:
            gap = self.cfg.min_interval_s - (self.clock() - self._last)
            if gap > 0:
                self.sleep(gap)
        self._last = self.clock()

    def get(self, url: str, kind: str) -> Response:
        last = Response(None, "", 0, 0.0, url, "not_sent")
        for attempt in range(1, self.cfg.max_attempts + 1):
            self._wait_turn()
            self._drop_idle_connections()
            t0 = self.clock()
            retry_after = None
            try:
                r = self.s.get(url, timeout=self.cfg.request_timeout_s)
                status, text, nbytes, err = r.status_code, r.text, len(r.content), None
                retry_after = r.headers.get("Retry-After")
                if status == 200 and ("/ask/over18" in (r.url or "") or 'class="over18-notice"' in text):
                    status, err = None, "over18_gate"  # 拿到年齡確認頁而不是內容（cookie 沒生效），當成失敗
            except requests.RequestException as e:
                status, text, nbytes, err = None, "", 0, type(e).__name__
            latency = self.clock() - t0
            self._done = self.clock()
            self.requests += 1
            self.on_request(kind=kind, status=status, latency_s=latency, nbytes=nbytes, url=url, attempt=attempt, error=err)
            last = Response(status, text, nbytes, latency, url, err if status is None else (None if status in (200, 404) else f"HTTP {status}"))
            if status in (200, 404):
                self.consecutive_failures = 0
                self.on_backoff(0)
                return last
            # 看板只數得到次數；每一次失敗是逾時、被斷線還是 HTTP 5xx，記在 log
            self.log("request_failed", kind=kind, attempt=attempt, status=status, error=last.error,
                     latency_s=round(latency, 1), url=url)
            self.consecutive_failures += 1
            wait = self.cfg.backoff_base_s * attempt
            if retry_after and retry_after.strip().isdigit():
                wait = max(wait, float(retry_after))
            if self.consecutive_failures >= self.cfg.pause_after_failures:
                wait = max(wait, self.cfg.pause_s)  # 連續失敗：整體暫停，不硬打
            if attempt < self.cfg.max_attempts or self.consecutive_failures >= self.cfg.pause_after_failures:
                self.on_backoff(wait)
                self.sleep(wait)
        return last


def parse_index(html: str, newest: bool, base_url: str = "https://www.ptt.cc"):
    """回傳 (entries, prev_page_no)。entries 依畫面由上到下，slot 從 0 起算。"""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("div.r-list-container")
    if container is None:
        raise ValueError("不是列表頁：找不到 div.r-list-container")
    entries = []
    for div in container.find_all("div", recursive=False):
        cls = div.get("class", [])
        if "r-list-sep" in cls:
            if newest:
                break  # 最新頁分隔線下面是置底公告
            continue
        if "r-ent" not in cls:
            continue
        a = div.select_one("div.title a")
        href = a.get("href") if a else None
        m = AID_RE.search(href or "")
        title_el = a or div.select_one("div.title")
        author_el = div.select_one("div.author")
        nrec_el = div.select_one("div.nrec")
        entries.append({
            "slot": len(entries),
            "aid": f"M.{m.group(1)}.A.{m.group(2)}" if m else None,
            "ts": int(m.group(1)) if m else None,
            "title": title_el.get_text(strip=True) if title_el else "",
            "author": author_el.get_text(strip=True) if author_el else "",
            "nrec": parse_nrec(nrec_el.get_text() if nrec_el else ""),
            "deleted": a is None,  # 「(本文已被刪除)」這類佔位沒有連結
            "url": base_url + href if href else None,
        })
    prev = None
    for btn in soup.select("div.btn-group-paging a"):
        if "上頁" in btn.get_text() and btn.get("href"):
            m = PAGE_RE.search(btn["href"])
            prev = int(m.group(1)) if m else None
    return entries, prev


def parse_push_time(text: str, post_dt: datetime):
    """「101.8.247.147 10/29 22:04」→ (ip, UTC datetime)。年份跟著發文時間，跨年就加一年。"""
    m = PUSH_TIME_RE.search(text or "")
    if not m:
        return None, None
    ip, mo, d, hh, mm = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5)
    year = post_dt.year + (1 if mo < post_dt.month else 0)
    try:
        local = datetime(year, mo, d, int(hh or 0), int(mm or 0), tzinfo=TW)
    except ValueError:
        return ip, None
    return ip, local.astimezone(timezone.utc)


def parse_article(html: str, published_ts: int):
    """文章頁 → dict；不是文章頁（找不到 #main-content）回 None。"""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content")
    if main is None:
        return None
    meta = {}
    for el in main.select("div.article-metaline, div.article-metaline-right"):
        tag, val = el.select_one(".article-meta-tag"), el.select_one(".article-meta-value")
        if tag and val:
            meta[tag.get_text(strip=True)] = val.get_text(strip=True)
    post_dt = datetime.fromtimestamp(published_ts, TW)
    pushes, warn_rows, seen = [], 0, Counter()
    for p in main.select("div.push"):
        tag = p.select_one("span.push-tag")
        if tag is None:
            warn_rows += 1  # 「檔案過大！部分文章無法顯示」這類提示列
            continue
        user_el, content_el, when_el = p.select_one("span.push-userid"), p.select_one("span.push-content"), p.select_one("span.push-ipdatetime")
        user = user_el.get_text(strip=True) if user_el else ""
        raw = content_el.get_text() if content_el else ""
        content = (raw[1:] if raw.startswith(":") else raw).strip()
        when = when_el.get_text(strip=True) if when_el else ""
        ip, at = parse_push_time(when, post_dt)
        key = (user, when, content)
        pushes.append({"floor": len(pushes), "tag": tag.get_text(strip=True), "user": user, "content": content,
                       "ip": ip or "", "time_text": when, "comment_at": at, "dup_n": seen[key]})
        seen[key] += 1  # 同一人同一分鐘講同一句話的第幾次，讓 MERGE 的鍵不會撞
    for el in main.select("div.article-metaline, div.article-metaline-right, div.push"):
        el.decompose()
    body = main.get_text().split("※ 發信站")[0].rstrip("-\n ").strip()
    tags = Counter(p["tag"] for p in pushes)
    return {
        "author": meta.get("作者"), "title": meta.get("標題"), "board": meta.get("看板"), "time_text": meta.get("時間"),
        "content": body, "pushes": pushes, "push": tags.get("推", 0), "boo": tags.get("噓", 0), "arrow": tags.get("→", 0),
        "comment_count": len(pushes), "truncated": warn_rows > 0, "warn_rows": warn_rows,
    }
