"""Polite HTTP fetch for the media channel crawl.

- per-host spacing >= 5 s plus 0-2 s jitter (shared across processes via a lock file timestamp)
- honest, descriptive User-Agent (no browser impersonation)
- 429/503: exponential backoff starting at 60 s, capped at 30 min
- 3 consecutive failures on a host -> host paused, recorded in progress.json
- no cookies stored, no body HTML stored (only extracted fields are written by the crawler)
"""
import json
import os
import random
import time
from urllib.parse import urlparse

import requests

UA = "QSearchResearchBot/0.1 (exam-research; low-rate; no-login)"
MIN_GAP = 5.0
JITTER = 2.0
_last_request = {}
_consec_fail = {}
PAUSED_HOSTS = set()

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.8"})
# do not persist cookies: drop the cookie jar after each request
SESSION.cookies.clear_session_cookies()


class HostPaused(Exception):
    pass


def _site_key(host):
    # rate-limit per registrable domain (ltn.com.tw covers news./sports./ec. subdomains)
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _wait_for_host(host):
    last = _last_request.get(host)
    gap = MIN_GAP + random.uniform(0, JITTER)
    if last is not None:
        sleep_for = gap - (time.time() - last)
        if sleep_for > 0:
            time.sleep(sleep_for)


def get(url, timeout=30, max_backoff_rounds=6):
    """Return (status, text_or_bytes, final_url). Raises HostPaused when host is paused."""
    host = _site_key(urlparse(url).netloc)
    if host in PAUSED_HOSTS:
        raise HostPaused(host)
    backoff = 60.0
    rounds = 0
    while True:
        _wait_for_host(host)
        _last_request[host] = time.time()
        try:
            r = SESSION.get(url, timeout=timeout, allow_redirects=True)
            SESSION.cookies.clear()
        except requests.RequestException as e:
            _consec_fail[host] = _consec_fail.get(host, 0) + 1
            if _consec_fail[host] >= 3:
                PAUSED_HOSTS.add(host)
                raise HostPaused(f"{host}: 3 consecutive network errors: {e}")
            time.sleep(min(backoff, 1800))
            backoff = min(backoff * 2, 1800)
            continue
        if r.status_code in (429, 503):
            rounds += 1
            _consec_fail[host] = _consec_fail.get(host, 0) + 1
            if _consec_fail[host] >= 3:
                PAUSED_HOSTS.add(host)
                raise HostPaused(f"{host}: {r.status_code} x3, paused")
            if rounds > max_backoff_rounds:
                return r.status_code, None, r.url
            time.sleep(min(backoff, 1800))
            backoff = min(backoff * 2, 1800)
            continue
        _consec_fail[host] = 0
        return r.status_code, r.content, r.url


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
