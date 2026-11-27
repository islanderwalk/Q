#!/usr/bin/env python3
"""Robots-compliant YouTube collector for the q6 history task (channel = youtube).

Scope actually allowed by robots.txt (fetched 2026-11-01):
  - /@handle/videos (HTML, first page only, ~30 items) and /@handle/videos?sort=p
  - /watch?v=ID (HTML, ytInitialPlayerResponse -> microformat publishDate, shortDescription)
NOT used (robots.txt Disallow): /youtubei/* (continuation paging, comments), /feeds/videos.xml, /results.
Therefore full-history paging to 2023-01 is not possible here; see progress.json coverage notes.

Politeness: >=5 s (+0-2 s jitter) between any two requests to www.youtube.com; 429/5xx backoff
60s, 120s, then pause after 3 consecutive failures; verification/consent pages stop the run.
Cookies stay in memory only. No HTML is stored; only extracted fields. Uploader stored as hash.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

BASE = Path(os.environ.get("Q6HIST_ROOT", "q6hist")) / "youtube"
RAW = BASE / "raw" / "youtube"
STATE = BASE / "state.json"          # video ids and listing status only (no tokens/cookies)
PROGRESS = BASE / "progress.json"

TZ8 = dt.timezone(dt.timedelta(hours=8))
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 q6-history-research/1.0 (contact: see repository README)")
HOST = "https://www.youtube.com"

# Disallow prefixes copied from https://www.youtube.com/robots.txt (User-agent: *).
DISALLOW = ["/api/", "/channel_picker", "/comment", "/feeds/videos.xml", "/file_download",
            "/get_video", "/get_video_info", "/get_midroll_info", "/live_chat", "/login", "/qr",
            "/results", "/signup", "/t/terms", "/timedtext_video", "/verify_age", "/watch_ajax",
            "/watch_fragments_ajax", "/watch_popup", "/watch_queue_ajax", "/youtubei/"]

CHANNELS = [
    ("@PNNPTS", "@PNNPTS 公視新聞網"),
    ("@CNA_Taiwan", "@CNA_Taiwan 中央社"),
    ("@TVBSNEWS01", "@TVBSNEWS01 TVBS NEWS"),
    ("@newsebc", "@newsebc 東森新聞 CH51"),
    ("channel/UC3EXlNYqWlli5IjgWHMbQRA", "高雄都會台（中嘉寬頻） UC3EXlNYqWlli5IjgWHMbQRA"),
]
LIST_VARIANTS = [("newest", "/videos"), ("popular", "/videos?sort=p")]


class PauseError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class BudgetDone(Exception):
    pass


def now8():
    return dt.datetime.now(TZ8)


def iso8(t):
    return t.isoformat(timespec="seconds") if t else None


def allowed(url):
    p = urlsplit(url).path
    return not any(p.startswith(d) for d in DISALLOW)


def uhash(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:12]


def json_after(html, marker):
    i = html.find(marker)
    if i < 0:
        return None
    i += len(marker)
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[i:].lstrip())
        return obj
    except Exception:
        return None


def walk(o, key, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                acc.append(v)
            walk(v, key, acc)
    elif isinstance(o, list):
        for v in o:
            walk(v, key, acc)
    return acc


def parse_list(d):
    """Return ordered unique [(video_id, relative_time_text)] from one first-page listing."""
    out, seen = [], set()
    for lv in walk(d, "lockupViewModel", []):
        ct = lv.get("contentType")
        if ct not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
            continue
        vid = lv.get("contentId")
        if not vid or len(vid) != 11 or vid in seen:
            continue
        m = re.search(r'"content":"([^"]{1,30}?前)"', json.dumps(lv, ensure_ascii=False))
        seen.add(vid)
        out.append([vid, m.group(1) if m else None])
    for vr in walk(d, "videoRenderer", []):
        vid = vr.get("videoId")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        out.append([vid, (vr.get("publishedTimeText") or {}).get("simpleText")])
    return out


class Polite:
    def __init__(self, max_requests=None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
        self.last = 0.0
        self.requests = 0
        self.errors = 0
        self.consec_fail = 0
        self.max_requests = max_requests

    def _gap(self):
        wait = self.last + 5.0 + random.uniform(0, 2.0) - time.time()
        if wait > 0:
            time.sleep(wait)

    def get(self, url):
        if not allowed(url):
            raise PauseError("internal_guard: disallowed path " + urlsplit(url).path)
        backoff = 60
        while True:
            if self.max_requests is not None and self.requests >= self.max_requests:
                raise BudgetDone()
            self._gap()
            status, final, text = None, url, None
            try:
                r = self.s.get(url, timeout=40)
                status, final, text = r.status_code, r.url, r.text
            except requests.RequestException as e:
                print(f"[{now8().isoformat(timespec='seconds')}] request exception: {type(e).__name__}", flush=True)
            self.last = time.time()
            self.requests += 1
            if final and ("consent.youtube.com" in final or "/sorry/" in final):
                raise PauseError(f"verification_or_consent_page status={status} (not clicked)")
            if status == 200:
                self.consec_fail = 0
                return text
            if status == 404:
                self.consec_fail = 0
                return None
            self.errors += 1
            self.consec_fail += 1
            print(f"[{now8().isoformat(timespec='seconds')}] status={status} consec_fail={self.consec_fail} url_path={urlsplit(url).path}", flush=True)
            if self.consec_fail >= 3:
                raise PauseError(f"3 consecutive failures, last status={status}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 1800)


def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(p, obj):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def to_plus8(s):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ8)
    return t.astimezone(TZ8)


def detail_record(vid, handle, list_src, html, fetched):
    pr = json_after(html, "ytInitialPlayerResponse = ") or {}
    vd = pr.get("videoDetails") or {}
    mf = (pr.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    if vd and vd.get("videoId") not in (None, vid):
        return None, "videoId_mismatch"
    pub = to_plus8(mf.get("publishDate") or mf.get("uploadDate"))
    title = vd.get("title") or mf.get("title", {}).get("simpleText")
    desc = vd.get("shortDescription") or ""
    text = title if not desc else f"{title}\n\n{desc}"
    rec = {
        "post_id": f"yt:{vid}",
        "channel": "youtube",
        "sub_type": "video",
        "source": handle,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "published_at": iso8(pub),
        "fetched_at": iso8(fetched),
        "text": text,
        "title": title,
        "time_basis": "real" if pub else "missing",
        "collect_method": f"{list_src}_tab_html+watch_html(ytInitialPlayerResponse)",
        "uploader": vd.get("author"),
        "author_hash": uhash(vd.get("channelId")),
        "parent_id": None,
        "playability": (pr.get("playabilityStatus") or {}).get("status"),
        "length_seconds": vd.get("lengthSeconds"),
    }
    return rec, ("ok" if pub else "no_publishDate")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-requests", type=int, default=None)
    ap.add_argument("--only", default=None, help="handle or path substring, e.g. @PNNPTS")
    args = ap.parse_args()

    BASE.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE, {"listed": {}, "done_ids": []})
    done = set(state.get("done_ids", []))
    prog = load_json(PROGRESS, {"sources": {}, "counters": {}})
    prog["started_at"] = prog.get("started_at") or iso8(now8())
    prog["state"] = "running"
    prog["state_reason"] = None
    prog["blocked_paths_not_used"] = ["/youtubei/ (robots Disallow: continuation paging and comments)",
                                      "/feeds/videos.xml (robots Disallow: RSS)",
                                      "/results (robots Disallow: search)"]
    polite = Polite(args.max_requests)
    counters = {"records_written": 0, "detail_ok": 0, "detail_no_date": 0, "detail_failed": 0}

    def flush_progress():
        prog["updated_at"] = iso8(now8())
        prog["requests_total"] = polite.requests
        prog["errors_total"] = polite.errors
        prog["consecutive_fail"] = polite.consec_fail
        prog["counters"] = counters
        prog["done_ids_total"] = len(done)
        save_json(PROGRESS, prog)

    try:
        for path, label in CHANNELS:
            if args.only and args.only not in path:
                continue
            handle_key = path
            srcs = state["listed"].setdefault(handle_key, {})
            for mode, suffix in LIST_VARIANTS:
                if mode in srcs:
                    continue
                url = f"{HOST}/{path}{suffix}"
                html = polite.get(url)
                if html is None:
                    srcs[mode] = {"status": "404", "items": []}
                    continue
                d = json_after(html, "ytInitialData = ") or {}
                items = parse_list(d)
                title_meta = walk(d, "channelMetadataRenderer", [])
                srcs[mode] = {"status": "ok", "items": items,
                              "channel_title": (title_meta[0].get("title") if title_meta else None),
                              "channel_id": (title_meta[0].get("externalId") if title_meta else None),
                              "relative_times_first_and_last": [items[0][1] if items else None,
                                                                items[-1][1] if items else None]}
                prog["sources"].setdefault(label, {})["list_" + mode] = {
                    "items": len(items), "channel_title": srcs[mode]["channel_title"],
                    "relative_time_first": srcs[mode]["relative_times_first_and_last"][0],
                    "relative_time_last": srcs[mode]["relative_times_first_and_last"][1],
                    "continuation_followed": False}
                save_json(STATE, state)
                flush_progress()

            # detail pass: every listed video id, newest first, unique
            ids = []
            for mode, _ in LIST_VARIANTS:
                for vid, _rel in (srcs.get(mode, {}).get("items") or []):
                    if vid not in ids:
                        ids.append(vid)
            prog["sources"].setdefault(label, {})["detail_pending"] = len([i for i in ids if i not in done])
            for vid in ids:
                if vid in done:
                    continue
                url = f"{HOST}/watch?v={vid}"
                html = polite.get(url)
                fetched = now8()
                if html is None:
                    counters["detail_failed"] += 1
                    done.add(vid)
                    state["done_ids"] = sorted(done)
                    continue
                rec, status = detail_record(vid, label, "videos", html, fetched)
                if rec is None:
                    counters["detail_failed"] += 1
                else:
                    pub = to_plus8(rec["published_at"]) if rec["published_at"] else None
                    month = pub.strftime("%Y-%m") if pub else "nodate"
                    out = RAW / f"{month}.jsonl"
                    with out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    counters["records_written"] += 1
                    counters["detail_ok" if status == "ok" else "detail_no_date"] += 1
                done.add(vid)
                state["done_ids"] = sorted(done)
                save_json(STATE, state)
                flush_progress()

        prog["state"] = "done_all_listed_sources"
    except BudgetDone:
        prog["state"] = "stopped_request_budget"
    except PauseError as e:
        prog["state"] = "paused"
        prog["state_reason"] = e.reason
        print(f"PAUSED: {e.reason}", flush=True)
    finally:
        save_json(STATE, state)
        flush_progress()
        print(json.dumps({"state": prog["state"], "reason": prog.get("state_reason"),
                          "requests": polite.requests, "counters": counters}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())
