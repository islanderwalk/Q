# Shared crawl telemetry + adaptive throttle for the deep crawlers (Meta / PTT / YouTube).
# Every network request -> api_trace.jsonl (+ Loki stream kind=trace); one row per minute -> metrics.jsonl
# (+ Loki kind=metrics, + Pushgateway gauges); session/anomaly/screenshot events -> events.jsonl (+ Loki kind=event).
# Telemetry is best-effort: a Loki/Pushgateway outage never breaks a crawl, but it is not silent either
# (one push_failed event + console warning per sink per minute). No secret values are ever written here;
# callers pass sanitized URLs and hashed header values only.
import json, time, hashlib, threading, statistics, collections, pathlib, re, socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qsl
import requests

TW = timezone(timedelta(hours=8))
# 127.0.0.1, not localhost: on Windows "localhost" resolves to ::1 first, WSL only forwards IPv4, and every push then
# waits out a 5 s IPv6 connect timeout before falling back (measured 2026-11-03: ::1 5204 ms, 127.0.0.1 46 ms).
LOKI = "http://127.0.0.1:3100/loki/api/v1/push"
PGW = "http://127.0.0.1:9091/metrics/job"
# Local network loss (not a platform signal): Chromium net errors, browser fetch failures, requests' ConnectionError family.
DISCONNECT_RE = re.compile(r"ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED|ERR_NETWORK_CHANGED|ERR_NETWORK_IO_SUSPENDED|"
                           r"ERR_ADDRESS_UNREACHABLE|ConnectionError|NameResolutionError|Failed to fetch|NetworkError", re.I)
NET_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443), ("www.google.com", 443))
SECRET_Q = re.compile(r"token|lsd|dtsg|csrf|key|sig|auth|session|jazoest|__a|__req|__hs|__rev|__s|__hsi|__dyn|__csr|__ccg|__spin|oh|oe|_nc_", re.I)


def now_iso():
    return datetime.now(TW).isoformat(timespec="milliseconds")


def h10(v):
    return "sha256:" + hashlib.sha256(str(v).encode()).hexdigest()[:10] if v else None


def clean_url(u):
    """Drop every query value; keep the names (values of token-like names never leave the process)."""
    s = urlsplit(u)
    names = sorted({k for k, _ in parse_qsl(s.query, keep_blank_values=True)})
    return f"{s.scheme}://{s.netloc}{s.path}", names


def pct(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))], 1)


def is_disconnect(err):
    """True when an exception / error string looks like local connectivity loss rather than a platform response."""
    if err is None:
        return False
    if isinstance(err, BaseException):
        if isinstance(err, requests.exceptions.ConnectionError):
            return True
        err = f"{type(err).__name__}: {err}"
    return bool(DISCONNECT_RE.search(str(err)))


def net_up(timeout=5):
    for host, port in NET_PROBES:
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


class NetGuard:
    """Disconnects pause the crawl until the network is back; they never touch the throttle.
    If the network probe is fine but the same platform keeps refusing connections, the 3rd one in a row
    is handed back to the caller as a normal anomaly (the platform may be refusing us)."""

    def __init__(self, tel, platform, probe_every=15.0, max_conn_errors_online=3):
        self.tel, self.platform, self.every, self.max_online = tel, platform, probe_every, max_conn_errors_online
        self.online_errors = 0

    def ok(self):
        self.online_errors = 0

    def handle(self, err, target=None, sleeper=time.sleep):
        """Returns True if the failure was absorbed as a disconnect (caller retries, no anomaly)."""
        if net_up():
            if not is_disconnect(err):
                return False
            self.online_errors += 1
            self.tel.event("conn_error_network_up", platform=self.platform, target=target, error=str(err)[:160], run=self.online_errors)
            if self.online_errors >= self.max_online:
                self.online_errors = 0
                return False
            sleeper(self.every)
            return True
        t0 = time.time()
        prev = self.tel.state.get("session")
        self.tel.set_state(session="offline")
        self.tel.event("network_down", platform=self.platform, target=target, error=str(err)[:160])
        while not net_up():
            sleeper(self.every)
            self.tel._roll()
        self.tel.set_state(session=prev if prev != "offline" else "ok")
        self.tel.event("network_restored", platform=self.platform, target=target, down_s=round(time.time() - t0, 1))
        self.online_errors = 0
        return True


class Throttle:
    """Base interval; any anomaly doubles it (cap 10 min); 3 anomalies in a row -> 30 min pause;
    20 normal requests in a row -> halve back toward base."""

    def __init__(self, base=5.0, cap=600.0, pause=1800.0):
        self.base, self.cap, self.pause_s = base, cap, pause
        self.delay, self.level, self.bad_run, self.ok_run = base, 0, 0, 0
        self.last = 0.0

    def anomaly(self):
        self.ok_run = 0
        self.bad_run += 1
        self.delay = min(self.cap, self.delay * 2)
        self.level += 1
        if self.bad_run >= 3:
            self.bad_run = 0
            return self.pause_s
        return 0.0

    def ok(self):
        self.bad_run = 0
        self.ok_run += 1
        if self.ok_run >= 20 and self.delay > self.base:
            self.delay = max(self.base, self.delay / 2)
            self.level = max(0, self.level - 1)
            self.ok_run = 0

    def wait(self, sleeper=time.sleep):
        w = self.delay - (time.time() - self.last)
        if w > 0:
            sleeper(w)
        self.last = time.time()


class Telemetry:
    def __init__(self, job, outdir, platform_label="all"):
        self.job, self.dir = job, pathlib.Path(outdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.f_trace = open(self.dir / "api_trace.jsonl", "a", encoding="utf-8")
        self.f_metrics = open(self.dir / "metrics.jsonl", "a", encoding="utf-8")
        self.f_events = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        self.lock = threading.Lock()
        self.buf = collections.defaultdict(list)  # loki stream labels(json) -> [[ns, line]]
        self.minute = self._new_minute()
        self.recent_lat = collections.deque(maxlen=300)  # API latencies for the "2x p95" anomaly rule
        self.lat_spikes = 0  # consecutive 200 responses above 2x p95
        self.state = {"delay_s": None, "backoff_level": 0, "session": "unknown", "platform": platform_label, "target": None}
        self.posts_total = collections.Counter()
        self.push_fail_at = {}  # sink -> last time a push failure was reported
        self._stop = False
        threading.Thread(target=self._flusher, daemon=True).start()

    def _new_minute(self):
        return {"start": time.time(), "n": 0, "status": collections.Counter(), "lat": [], "api_lat": [],
                "bytes": 0, "new_posts": 0, "failed": 0}

    # ---- writers
    def _loki(self, labels, rec):
        key = json.dumps({"job": self.job, **labels}, sort_keys=True)
        with self.lock:
            self.buf[key].append([str(time.time_ns()), json.dumps(rec, ensure_ascii=False)])

    def trace(self, rec, api=False):
        rec = {"t": now_iso(), "job": self.job, **rec, "api": api, "delay_s": self.state["delay_s"], "backoff_level": self.state["backoff_level"]}
        self.f_trace.write(json.dumps(rec, ensure_ascii=False) + "\n")
        m = self.minute
        m["n"] += 1
        st = rec.get("status")
        m["status"][str(st) if st is not None else "failed"] += 1
        if st is None:
            m["failed"] += 1
        if isinstance(rec.get("latency_ms"), (int, float)):
            m["lat"].append(rec["latency_ms"])
            if api:
                m["api_lat"].append(rec["latency_ms"])
                self.recent_lat.append(rec["latency_ms"])
        m["bytes"] += rec.get("bytes") or 0
        sc = f"{str(st)[0]}xx" if st else "failed"
        self._loki({"kind": "trace", "platform": rec.get("platform", "na"), "status_class": sc, "api": str(api).lower()}, rec)
        self._roll()

    def latency_anomalous(self, ms):
        """Call for 200 responses only. One spike is noise; the 2nd consecutive spike counts as an anomaly."""
        if len(self.recent_lat) < 30 or ms is None:
            return False
        if ms <= 2 * pct(list(self.recent_lat), 0.95):
            self.lat_spikes = 0
            return False
        self.lat_spikes += 1
        if self.lat_spikes >= 2:
            self.lat_spikes = 0
            return True
        return False

    def event(self, kind, **kw):
        rec = {"t": now_iso(), "job": self.job, "event": kind, **kw}
        self.f_events.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f_events.flush()
        self._loki({"kind": "event", "platform": kw.get("platform", "na"), "event": kind}, rec)
        print("EVENT", json.dumps(rec, ensure_ascii=False), flush=True)

    def _push_failed(self, sink, err):
        now = time.time()
        if now - self.push_fail_at.get(sink, 0) < 60:
            return
        self.push_fail_at[sink] = now
        rec = {"t": now_iso(), "job": self.job, "event": "push_failed", "sink": sink, "platform": self.state["platform"],
               "error": str(err)[:200]}
        self.f_events.write(json.dumps(rec, ensure_ascii=False) + "\n")  # local file only: the sink itself may be the one down
        self.f_events.flush()
        print("WARN telemetry push failed:", sink, str(err)[:200], flush=True)

    def add_posts(self, platform, target, n):
        self.minute["new_posts"] += n
        self.posts_total[(platform, target)] += n

    def set_state(self, **kw):
        self.state.update(kw)

    def _roll(self, force=False):
        m = self.minute
        if not force and time.time() - m["start"] < 60:
            return
        row = {"t": now_iso(), "job": self.job, "window_s": round(time.time() - m["start"], 1), "requests": m["n"],
               "status_counts": dict(m["status"]), "failed": m["failed"], "p50_ms": pct(m["lat"], .5), "p95_ms": pct(m["lat"], .95),
               "api_requests": len(m["api_lat"]), "api_p50_ms": pct(m["api_lat"], .5), "api_p95_ms": pct(m["api_lat"], .95),
               "bytes": m["bytes"], "new_posts": m["new_posts"], **{k: self.state[k] for k in ("delay_s", "backoff_level", "session", "platform", "target")},
               "posts_total": sum(self.posts_total.values())}
        self.f_metrics.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.f_metrics.flush()
        self.f_trace.flush()
        self._loki({"kind": "metrics", "platform": str(self.state["platform"])}, row)
        self._pgw(row)
        self.minute = self._new_minute()

    def _pgw(self, row):
        p = str(self.state["platform"])
        L = [f'crawl_delay_seconds {row["delay_s"] or 0}', f'crawl_backoff_level {row["backoff_level"] or 0}',
             f'crawl_session_ok {1 if row["session"] == "ok" else 0}', f'crawl_requests_per_min {row["requests"]}',
             f'crawl_api_p95_ms {row["api_p95_ms"] or 0}', f'crawl_p95_ms {row["p95_ms"] or 0}',
             f'crawl_new_posts_per_min {row["new_posts"]}']
        for (pl, tg), n in self.posts_total.items():
            L.append(f'crawl_posts_total{{platform="{pl}",target="{tg}"}} {n}')
        for code, n in row["status_counts"].items():
            L.append(f'crawl_status_per_min{{code="{code}"}} {n}')
        try:
            r = requests.put(f"{PGW}/{self.job}/platform/{p}", data=("\n".join(L) + "\n").encode(), timeout=5)
            if r.status_code >= 300:
                self._push_failed("pushgateway", f"http_{r.status_code} {r.text[:120]}")
        except Exception as e:
            self._push_failed("pushgateway", repr(e))

    def _flusher(self):
        while not self._stop:
            time.sleep(5)
            self.flush_loki()

    def flush_loki(self):
        with self.lock:
            buf, self.buf = self.buf, collections.defaultdict(list)
        if not buf:
            return
        streams = [{"stream": json.loads(k), "values": v} for k, v in buf.items()]
        for i in range(0, len(streams), 20):
            try:
                r = requests.post(LOKI, json={"streams": streams[i:i + 20]}, timeout=10)
                if r.status_code >= 300:
                    self._push_failed("loki", f"http_{r.status_code} {r.text[:120]}")
            except Exception as e:
                self._push_failed("loki", repr(e))

    def close(self):
        self._roll(force=True)
        self._stop = True
        self.flush_loki()
        for f in (self.f_trace, self.f_metrics, self.f_events):
            f.close()
