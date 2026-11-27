# Backfill Loki / Prometheus from the crawlers' own telemetry files. The jsonl files under <workdir>/*/telemetry are the
# source of truth; the monitoring stack only ever gets a copy, and loses it whenever it is down while a crawler runs
# (reboot with the stack not up yet, WSL restart, a push that timed out). This finds the minutes the stack is missing and
# writes exactly those back with their original timestamps. Running it twice changes nothing.
#
# Loki: per (job, kind, platform, minute) the local line count is compared with Loki's. Short minutes are fetched line by
#   line and only lines Loki does not have are pushed, into a stream with the extra label backfill=<YYYYMMDDHH>. One stream
#   per hour keeps every push inside Loki's out-of-order window, and Loki drops an identical (timestamp, line) in the same
#   stream, so a rerun pushes nothing new. Dashboard selectors don't name the backfill label, so these lines show up as usual.
#   The same rule means a line the crawler wrote twice (same ms, same text) is stored once, so local lines are counted once too.
#   Loki's querier only asks the ingester about the last 3 h (query_ingesters_within); older lines pushed now stay invisible
#   until their chunk is flushed, so every push ends with POST /flush.
# Prometheus: minutes with no crawl_requests_per_min sample for a (job, platform) get that minute's metrics.jsonl row
#   written back through remote write. Needs --web.enable-remote-write-receiver and storage.tsdb.out_of_order_time_window
#   (observability/docker-compose.yml, prometheus.yml). Per-target crawl_posts_total is not in metrics.jsonl, so it is not
#   backfilled; everything else the crawler pushes per minute is.
import argparse, json, pathlib, struct, sys, time, collections
from datetime import datetime, timedelta
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from telem import TW  # noqa: E402

LOKI = "http://127.0.0.1:3100"
PROM = "http://127.0.0.1:9090"
KIND = {"api_trace.jsonl": "trace", "events.jsonl": "event", "metrics.jsonl": "metrics"}
GLOBS = ("*/telemetry/*.jsonl", "*/telemetry/*/*.jsonl")
LOKI_MAX_AGE_H = 167  # Loki rejects samples older than 1 week (reject_old_samples_max_age default)
SETTLE_S = 120  # the newest two minutes may still be in a crawler's 5 s Loki buffer or before the next scrape
API_HINT = ("/api/graphql", "/graphql/query", "/api/v1/", "/ajax/")  # crawl_deep.API_RE, for traces written before "api" was logged


def ts_of(rec):
    return datetime.fromisoformat(rec["t"]).timestamp()


def minute_end(ts):
    """Prometheus/Loki range steps are right-closed: a point at T counts (T-60s, T]."""
    return (int(ts) // 60 + 1) * 60


def loki_labels(kind, rec):
    plat = rec.get("platform")
    lab = {"job": rec["job"], "kind": kind, "platform": "na" if plat is None else str(plat)}
    if kind == "trace":
        st = rec.get("status")
        lab["status_class"] = f"{str(st)[0]}xx" if st else "failed"
        api = rec.get("api")
        if api is None:
            api = rec["job"] != "meta_crawl" or rec.get("resource_type") == "document" or any(h in (rec.get("url") or "") for h in API_HINT)
        lab["api"] = str(bool(api)).lower()
    elif kind == "event":
        lab["event"] = rec.get("event")
    return lab


def read_local(roots, since, until):
    """-> [(kind, loki labels, ts, line, record)] for every telemetry line with since <= t < until."""
    lo, hi = datetime.fromtimestamp(since, TW).isoformat(timespec="milliseconds"), datetime.fromtimestamp(until, TW).isoformat(timespec="milliseconds")
    out, seen = [], set()
    for root in roots:
        for g in GLOBS:
            for f in sorted(pathlib.Path(root).glob(g)):
                kind = KIND.get(f.name)
                if not kind:
                    continue
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.rstrip("\n")
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue  # a half-written last line while the crawler is mid-write
                        t = rec.get("t")
                        if not t or not (lo <= t < hi) or "job" not in rec:
                            continue
                        lab = loki_labels(kind, rec)
                        key = (tuple(sorted(lab.items())), t, line)
                        if key in seen:
                            continue  # written twice by the crawler; Loki keeps one copy
                        seen.add(key)
                        out.append((kind, lab, ts_of(rec), line, rec))
    return out


# ---------------------------------------------------------------- Loki
def loki_counts(job, since, until):
    q = f'sum by (kind, platform) (count_over_time({{job="{job}"}}[1m]))'
    r = requests.get(f"{LOKI}/loki/api/v1/query_range", params={"query": q, "start": int(since) * 10**9, "end": int(until) * 10**9,
                                                               "step": "60s"}, timeout=60)
    r.raise_for_status()
    got = collections.Counter()
    for s in r.json()["data"]["result"]:
        for t, v in s["values"]:
            got[(s["metric"].get("kind"), s["metric"].get("platform"), int(float(t)))] += int(float(v))
    return got


def loki_lines(lab, t_end):
    sel = ",".join(f'{k}="{lab[k]}"' for k in ("job", "kind", "platform"))
    r = requests.get(f"{LOKI}/loki/api/v1/query_range", params={"query": "{" + sel + "}", "start": (t_end - 63) * 10**9,
                                                               "end": (t_end + 3) * 10**9, "limit": 5000, "direction": "forward"}, timeout=60)
    r.raise_for_status()
    return {v[1] for s in r.json()["data"]["result"] for v in s["values"]}


def loki_push(entries, dry):
    """entries: [(labels, ts, line)] -> pushes into backfill=<hour> streams, oldest first."""
    streams = collections.defaultdict(list)
    for lab, ts, line in entries:
        hour = datetime.fromtimestamp(ts, TW).strftime("%Y%m%d%H")
        streams[json.dumps({**lab, "backfill": hour}, sort_keys=True)].append([str(int(round(ts * 1000)) * 10**6), line])
    if dry or not streams:
        return 0
    body, size, sent = [], 0, 0
    for k, vals in streams.items():
        vals.sort(key=lambda v: int(v[0]))
        body.append({"stream": json.loads(k), "values": vals})
        size += sum(len(v[1]) for v in vals)
        if size > 1_000_000:
            sent += _loki_post(body)
            body, size = [], 0
    if body:
        sent += _loki_post(body)
    requests.post(f"{LOKI}/flush", timeout=60).raise_for_status()  # make lines older than 3 h queryable now (see top)
    return sent


def _loki_post(body):
    r = requests.post(f"{LOKI}/loki/api/v1/push", json={"streams": body}, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"loki push http_{r.status_code}: {r.text[:200]}")
    time.sleep(0.2)  # stay well under Loki's default 4 MB/s ingestion limit
    return sum(len(s["values"]) for s in body)


def backfill_loki(local, since, until, dry):
    by_job = collections.defaultdict(lambda: collections.defaultdict(list))
    for kind, lab, ts, line, _ in local:
        by_job[lab["job"]][(kind, lab["platform"], minute_end(ts))].append((lab, ts, line))
    report = {}
    for job, minutes in by_job.items():
        have = loki_counts(job, since, until)
        short = {k: v for k, v in minutes.items() if have.get(k, 0) < len(v)}
        missing = []
        for (kind, plat, t_end), rows in sorted(short.items(), key=lambda kv: kv[0][2]):
            if have.get((kind, plat, t_end), 0) == 0:
                missing += rows
                continue
            seen = loki_lines(rows[0][0], t_end)
            missing += [r for r in rows if r[2] not in seen]
        pushed = loki_push(missing, dry)
        report[job] = {"local_lines": sum(len(v) for v in minutes.values()), "short_minutes": len(short),
                       "missing_lines": len(missing), "pushed": pushed,
                       "first_missing": datetime.fromtimestamp(min(m[1] for m in missing), TW).isoformat(timespec="seconds") if missing else None,
                       "last_missing": datetime.fromtimestamp(max(m[1] for m in missing), TW).isoformat(timespec="seconds") if missing else None}
    return report


# ---------------------------------------------------------------- Prometheus (remote write, protobuf + snappy by hand)
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(num, payload):  # length-delimited field
    return _varint(num << 3 | 2) + _varint(len(payload)) + payload


def _series(labels, samples):
    b = b"".join(_field(1, _field(1, k.encode()) + _field(2, str(v).encode())) for k, v in sorted(labels.items()))
    for ts_ms, val in samples:
        b += _field(2, b"\x09" + struct.pack("<d", float(val)) + b"\x10" + _varint(int(ts_ms)))
    return b


def _snappy(data):
    """Snappy block format using literals only: valid for any decoder, no compression needed for a few KB."""
    out = bytearray(_varint(len(data)))
    for i in range(0, len(data), 65536):
        chunk = data[i:i + 65536]
        n = len(chunk) - 1  # literal tag: (len-1) << 2 when it fits in 6 bits, else tag 61 = 2-byte little-endian length
        out += (bytes([n << 2]) if n < 60 else bytes([61 << 2]) + struct.pack("<H", n)) + chunk
    return bytes(out)


def row_samples(row, ts_ms):
    yield "crawl_delay_seconds", {}, row.get("delay_s") or 0
    yield "crawl_backoff_level", {}, row.get("backoff_level") or 0
    yield "crawl_session_ok", {}, 1 if row.get("session") == "ok" else 0
    yield "crawl_requests_per_min", {}, row.get("requests") or 0
    yield "crawl_api_p95_ms", {}, row.get("api_p95_ms") or 0
    yield "crawl_p95_ms", {}, row.get("p95_ms") or 0
    yield "crawl_new_posts_per_min", {}, row.get("new_posts") or 0
    yield "push_time_seconds", {}, ts_ms / 1000
    for code, n in (row.get("status_counts") or {}).items():
        yield "crawl_status_per_min", {"code": str(code)}, n


def prom_present(job, plat, since, until):
    q = f'count_over_time(crawl_requests_per_min{{job="{job}",platform="{plat}"}}[60s])'
    r = requests.get(f"{PROM}/api/v1/query_range", params={"query": q, "start": since, "end": until, "step": "60s"}, timeout=60)
    r.raise_for_status()
    return {int(float(t)) for s in r.json()["data"]["result"] for t, _ in s["values"]}


def backfill_prom(local, since, until, dry):
    rows = collections.defaultdict(list)
    for kind, lab, ts, _, rec in local:
        if kind == "metrics":
            rows[(lab["job"], lab["platform"])].append((ts, rec))
    report = {}
    for (job, plat), rs in rows.items():
        present = prom_present(job, plat, since, until)
        gap = sorted((ts, r) for ts, r in rs if minute_end(ts) not in present)
        series = collections.defaultdict(list)
        for ts, r in gap:
            for name, extra, val in row_samples(r, int(ts * 1000)):
                series[json.dumps({"__name__": name, "job": job, "platform": plat, **extra}, sort_keys=True)].append((int(ts * 1000), val))
        err = None
        if series and not dry:
            body = b"".join(_field(1, _series(json.loads(k), v)) for k, v in series.items())
            r = requests.post(f"{PROM}/api/v1/write", data=_snappy(body), timeout=60, headers={
                "Content-Encoding": "snappy", "Content-Type": "application/x-protobuf", "X-Prometheus-Remote-Write-Version": "0.1.0"})
            if r.status_code >= 300:
                err = f"http_{r.status_code} {r.text[:200]}"
        report[f"{job}/{plat}"] = {"rows": len(rs), "missing_minutes": len(gap), "written": 0 if (dry or err) else len(gap), "error": err,
                                   "first_missing": datetime.fromtimestamp(gap[0][0], TW).isoformat(timespec="seconds") if gap else None,
                                   "last_missing": datetime.fromtimestamp(gap[-1][0], TW).isoformat(timespec="seconds") if gap else None}
    return report


def run(roots, hours=24.0, dry=False):
    until = (time.time() - SETTLE_S) // 60 * 60  # whole minutes: range steps must land on the same T as minute_end()
    since = max(until - hours * 3600, (time.time() - LOKI_MAX_AGE_H * 3600) // 60 * 60 + 60)
    local = read_local(roots, since, until)
    return {"t": datetime.now(TW).isoformat(timespec="seconds"), "since": datetime.fromtimestamp(since, TW).isoformat(timespec="seconds"),
            "until": datetime.fromtimestamp(until, TW).isoformat(timespec="seconds"), "dry_run": dry,
            "loki": backfill_loki(local, since, until, dry), "prometheus": backfill_prom(local, since, until, dry)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("roots", nargs="+", help="crawl workdirs (CRAWL_WORKDIR / META_WORKDIR), the folders that hold */telemetry")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run(a.roots, a.hours, a.dry_run), ensure_ascii=False, indent=1))
