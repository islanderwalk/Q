#!/usr/bin/env python3
"""Media channel history crawler (resumable, checkpointed, polite).

Sub-commands
  udn-meta                     full 2023-01..now URL+time list from UDN weekly news sitemaps (metadata only)
  sample SITE [--months ...] [--k N]
                               monthly random sample of article bodies for SITE in {ltn, setn, pts}
                               (ID-based: article IDs are time-ordered; boundaries found by search)
  probe SITE ID                parse one article (debug)
  merge                        concatenate per-site parts into raw/media/<yyyy-mm>.jsonl

Output root: $Q6HIST_ROOT/media (default ./q6hist/media; keep it on a local Linux filesystem)
"""
import argparse
import datetime as dt
import html
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from polite import HostPaused, get, load_json, save_json_atomic  # noqa: E402

OUT = os.path.join(os.environ.get("Q6HIST_ROOT", "q6hist"), "media")
TZ = dt.timezone(dt.timedelta(hours=8))
MONTHS = [(y, m) for y in (2023, 2024, 2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 11)]
T_START = dt.datetime(2023, 1, 1, tzinfo=TZ).timestamp()

SITES = {
    # id-based sites: url template, sitemap used for the latest id, regex for ids in that sitemap
    "ltn": {
        "source": "news.ltn.com.tw",
        "url": lambda i: f"https://news.ltn.com.tw/news/x/breakingnews/{i}",
        "sitemap": "https://news.ltn.com.tw/sitemap.xml?page=2",
        "id_re": r"/breakingnews/(\d+)",
    },
    "setn": {
        "source": "www.setn.com",
        "url": lambda i: f"https://www.setn.com/news/{i}",
        "sitemap": "https://www.setn.com/sitemapgooglenews.xml",
        "id_re": r"/news/(\d+)",
    },
    "pts": {
        "source": "news.pts.org.tw",
        "url": lambda i: f"https://news.pts.org.tw/article/{i}",
        "sitemap": "https://news.pts.org.tw/xml/sitemap.xml",
        "id_re": r"/article/(\d+)",
    },
}


def now_iso():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def log(site, msg):
    line = f"{now_iso()} [{site}] {msg}"
    print(line, flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "crawl.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def state_path(name):
    d = os.path.join(OUT, "state")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def update_progress(site, data):
    """Per-site progress file, plus merged progress.json for the whole channel."""
    d = os.path.join(OUT)
    os.makedirs(d, exist_ok=True)
    save_json_atomic(os.path.join(d, f"progress_{site}.json"), data)
    merged = {}
    for fn in sorted(os.listdir(d)):
        if fn.startswith("progress_") and fn.endswith(".json"):
            merged[fn[len("progress_"):-5]] = load_json(os.path.join(d, fn), {})
    merged["_updated"] = now_iso()
    save_json_atomic(os.path.join(d, "progress.json"), merged)


# ---------------------------------------------------------------- parsing
LD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _meta(txt, prop):
    m = re.search(r'<meta[^>]+property="%s"[^>]+content="([^"]*)"' % re.escape(prop), txt)
    if not m:
        m = re.search(r'<meta[^>]+content="([^"]*)"[^>]+property="%s"' % re.escape(prop), txt)
    return html.unescape(m.group(1)) if m else None


def page_title(txt):
    t = _meta(txt, "og:title")
    if not t:
        m = re.search(r"<title>(.*?)</title>", txt, re.S | re.I)
        t = html.unescape(m.group(1)).strip() if m else None
    return t


def parse_ld_article(txt):
    for m in LD_RE.finditer(txt):
        try:
            obj = json.loads(m.group(1).strip(), strict=False)
        except Exception:
            continue
        for d in _walk(obj):
            if isinstance(d, dict) and "datePublished" in d and (
                "articleBody" in d or d.get("@type") in ("NewsArticle", "Article", "ReportageNewsArticle")
            ):
                return {"published": d.get("datePublished"), "text": d.get("articleBody")}
    return None


def parse_article_common(txt):
    got = parse_ld_article(txt)
    if got is None or not got.get("text"):
        m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
        if m:
            try:
                body = json.loads('"%s"' % m.group(1))
            except Exception:
                body = None
            pub = got["published"] if got and got.get("published") else None
            if not pub:
                d = re.search(r'"datePublished"\s*:\s*"([^"]+)"', txt)
                pub = d.group(1) if d else None
            got = {"published": pub, "text": body}
    if got is None:
        m = re.search(r'article:published_time"\s+content="([^"]+)"', txt) or re.search(
            r'property="article:published_time"[^>]*content="([^"]+)"', txt)
        got = {"published": m.group(1) if m else None, "text": None}
    return got


def parse_setn_body(txt):
    """SETN has no articleBody; paragraphs sit in div#newsContent. Text is kept as-is (tags removed, entities decoded)."""
    start = txt.find('id="newsContent"')
    if start < 0:
        return None
    end = txt.find("</article>", start)
    seg = txt[start:end] if end > 0 else txt[start:start + 300000]
    paras = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", seg, re.S):
        t = html.unescape(re.sub(r"<[^>]+>", "", p)).strip()
        if t:
            paras.append(t)
    return "\n".join(paras) if paras else None


def norm_pub(s, naive_tz="+08:00"):
    """Return ISO8601 with offset. Naive or unknown offset is treated as Taipei time."""
    if not s:
        return None
    m = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?\s*(Z|[+-]\d{2}:?\d{2})?", s.strip())
    if not m:
        return None
    y, mo, d, hh, mi, ss, tz = m.groups()
    ss = ss or "00"
    if not tz:
        tzs = naive_tz
    elif tz == "Z":
        tzs = "+00:00"
    else:
        tzs = tz if ":" in tz else tz[:3] + ":" + tz[3:]
    return f"{y}-{mo}-{d}T{hh}:{mi}:{ss}{tzs}"


def epoch(iso):
    return dt.datetime.fromisoformat(iso).timestamp()


def month_of(iso):
    d = dt.datetime.fromisoformat(iso).astimezone(TZ)
    return d.year, d.month


# ---------------------------------------------------------------- fetch
def fetch_article(site, aid):
    """Return (status, parsed_dict_or_None, final_url)."""
    cfg = SITES[site]
    url = cfg["url"](aid)
    st, body, final = get(url)
    if body is None:
        return st, None, final
    txt = body.decode("utf-8", "replace")
    got = parse_article_common(txt)
    if site == "setn" and not got.get("text"):
        got["text"] = parse_setn_body(txt)
    pub = norm_pub(got.get("published"))
    if site == "setn" and pub and "+00:00" in (got.get("published") or ""):
        # SETN's '+00:00' label on local wall-clock time is verified in the foreground test (see RESULTS)
        pub = norm_pub(got.get("published").replace("+00:00", ""), naive_tz="+08:00")
    return st, {
        "published": pub,
        "title": page_title(txt),
        "text": got.get("text"),
        "final": final,
    }, final


def latest_id(site):
    st = state_path(f"{site}_latest.json")
    cached = load_json(st, None)
    if cached and cached.get("id"):
        return cached["id"]
    cfg = SITES[site]
    _, body, _ = get(cfg["sitemap"])
    ids = [int(x) for x in re.findall(cfg["id_re"], body.decode("utf-8", "replace"))]
    lid = max(ids)
    save_json_atomic(st, {"id": lid, "from": cfg["sitemap"], "at": now_iso()})
    return lid


# ---------------------------------------------------------------- id -> time search
class IdIndex:
    """Caches id -> epoch (or -1 for 404, -2 for no date). Persisted, no article text."""

    def __init__(self, site):
        self.site = site
        self.path = state_path(f"{site}_idcache.json")
        self.cache = load_json(self.path, {})
        self.dirty = 0

    def save(self):
        save_json_atomic(self.path, self.cache)
        self.dirty = 0

    def epoch(self, aid):
        k = str(aid)
        if k in self.cache:
            return self.cache[k]
        st, parsed, _ = fetch_article(self.site, aid)
        if parsed is None:
            v = -1
        elif parsed.get("published") is None:
            v = -2
        else:
            v = epoch(parsed["published"])
        self.cache[k] = v
        self.dirty += 1
        if self.dirty >= 20:
            self.save()
        return v

    def valid_near(self, aid, direction=1, tries=6):
        """Nearest id at/after aid (direction=+1) or at/before (-1) that has a date."""
        cur = aid
        for _ in range(tries):
            v = self.epoch(cur)
            if v >= 0:
                return cur, v
            cur += direction
        return None, None


def find_boundary(idx, T, lo_hint, hi_id, hi_t, max_probes=60):
    """Smallest id whose published epoch >= T. Returns (id, epoch)."""
    if lo_hint is None:
        # exponential search downward from hi_id until a probe is before T
        step = 2000
        lo = hi_id - step
        while True:
            lid, lt = idx.valid_near(max(lo, 1), direction=1)
            if lid is None:
                lo -= step
                step *= 2
                continue
            if lt < T:
                lo, lo_t = lid, lt
                break
            hi_id, hi_t = lid, lt
            step *= 2
            lo = hi_id - step
            if lo < 1:
                raise RuntimeError("boundary search underflow")
    else:
        lo, lo_t = lo_hint
    hi, hi_t = hi_id, hi_t
    probes = 0
    while hi - lo > 1 and probes < max_probes:
        probes += 1
        if probes % 3 == 0:
            mid = (lo + hi) // 2  # bisection step guarantees progress
        else:
            frac = (T - lo_t) / max(hi_t - lo_t, 1.0)
            mid = lo + int(frac * (hi - lo))
        mid = min(max(mid, lo + 1), hi - 1)
        mid_id, mid_t = idx.valid_near(mid, direction=1, tries=4)
        if mid_id is None or mid_id >= hi:
            mid_id, mid_t = idx.valid_near(mid, direction=-1, tries=4)
            if mid_id is None or mid_id <= lo:
                break
        if mid_t >= T:
            hi, hi_t = mid_id, mid_t
        else:
            lo, lo_t = mid_id, mid_t
    idx.save()
    return hi, hi_t, (lo, lo_t)


def month_bounds(site, latest, months):
    """bounds['yyyy-mm'] = first id published in that month (Taipei time). Computed for the given months only."""
    p = state_path(f"{site}_bounds.json")
    bounds = load_json(p, {})
    idx = IdIndex(site)
    for (y, m) in sorted(set(months)):
        key = f"{y}-{m:02d}"
        T = dt.datetime(y, m, 1, tzinfo=TZ).timestamp()
        if key in bounds and bounds[key].get("id"):
            continue
        # lower bracket: nearest already-known boundary strictly before T (None -> exponential search)
        lower = None
        for k2 in sorted(bounds):
            if bounds[k2]["t"] < T:
                lower = (bounds[k2]["id"], bounds[k2]["t"])
        bid, bt, _ = find_boundary(idx, T, lower, latest, idx.epoch(latest))
        bounds[key] = {"id": bid, "t": bt, "at": now_iso()}
        save_json_atomic(p, bounds)
        log(site, f"boundary {key} -> id {bid}")
    return bounds


# ---------------------------------------------------------------- sampling
def sample_month(site, ym, k, bounds, latest, max_attempts_factor=3):
    y, m = ym
    key = f"{y}-{m:02d}"
    nkey = f"{y + (m == 12)}-{(m % 12) + 1:02d}"
    lo = bounds[key]["id"]
    hi = bounds[nkey]["id"] - 1 if nkey in bounds else latest
    if hi < lo:
        return 0, 0
    rng = random.Random(f"{site}-{key}")
    out_dir = os.path.join(OUT, "raw_parts", site)
    os.makedirs(out_dir, exist_ok=True)
    part = os.path.join(out_dir, f"{key}.jsonl")
    tmp = part + ".tmp"
    seen_ids = set()
    accepted = 0
    attempts = 0
    max_attempts = k * max_attempts_factor
    span = hi - lo + 1
    with open(tmp, "w", encoding="utf-8") as f:
        while accepted < k and attempts < max_attempts and len(seen_ids) < span:
            aid = rng.randint(lo, hi)
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            attempts += 1
            st, parsed, final = fetch_article(site, aid)
            if parsed is None or not parsed.get("published") or not parsed.get("text"):
                continue
            pub = parsed["published"]
            if month_of(pub) != (y, m):
                continue
            rec = {
                "post_id": f"{site}:{aid}",
                "channel": "media",
                "sub_type": "article",
                "source": SITES[site]["source"],
                "url": final,
                "published_at": pub,
                "fetched_at": now_iso(),
                "title": parsed.get("title"),
                "text": parsed["text"],
                "time_basis": "real",
                "collect_method": f"{site}_id_sample_v1",
                "sample_month": key,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            accepted += 1
    os.replace(tmp, part)
    return accepted, attempts


def run_sample(site, months, k):
    latest = latest_id(site)
    log(site, f"latest id from sitemap = {latest}")
    need = []
    for (y, m) in months:
        need.append((y, m))
        need.append((y + (m == 12), (m % 12) + 1))  # upper bound of the month
    need = [x for x in need if x <= (2026, 11)]
    bounds = month_bounds(site, latest, need)
    prog_path = state_path(f"{site}_sample_progress.json")
    prog = load_json(prog_path, {"done": {}})
    t0 = time.time()
    for ym in months:
        key = f"{ym[0]}-{ym[1]:02d}"
        if key in prog["done"]:
            continue
        acc, att = sample_month(site, ym, k, bounds, latest)
        prog["done"][key] = {"accepted": acc, "attempts": att, "at": now_iso()}
        save_json_atomic(prog_path, prog)
        total_acc = sum(v["accepted"] for v in prog["done"].values())
        total_att = sum(v["attempts"] for v in prog["done"].values())
        update_progress(site, {
            "months_done": len(prog["done"]),
            "months_total": len(MONTHS),
            "articles_sampled": total_acc,
            "requests": total_att,
            "last_month": key,
            "last_time": now_iso(),
            "elapsed_s": int(time.time() - t0),
        })
        log(site, f"month {key}: accepted {acc} of {att} attempts")


# ---------------------------------------------------------------- UDN metadata
UDN_URL_RE = re.compile(r"<url>(.*?)</url>", re.S)


def udn_meta():
    site = "udn"
    prog_path = state_path("udn_weeks.json")
    prog = load_json(prog_path, {"done": {}})
    out_dir = os.path.join(OUT, "index", "udn_meta_parts")
    os.makedirs(out_dir, exist_ok=True)
    total_urls = 0
    files_ok = 0
    t0 = time.time()
    for (y, m) in MONTHS:
        for w in range(1, 6):
            for typ in ("1015T", "2T"):
                fname = f"{typ}{y}{m:02d}W{w}.xml"
                if fname in prog["done"]:
                    continue
                url = f"https://udn.com/sitemapxml/news/{fname}"
                st, body, final = get(url)
                n = 0
                if body is not None and st == 200:
                    txt = body.decode("utf-8", "replace")
                    rows = []
                    for block in UDN_URL_RE.findall(txt):
                        loc = re.search(r"<loc>([^<]+)</loc>", block)
                        lm = re.search(r"<lastmod>([^<]+)</lastmod>", block)
                        cm = re.search(r"<!--\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*-->", block)
                        if not loc:
                            continue
                        pub = norm_pub(cm.group(1)) if cm else None
                        rows.append({
                            "post_id": "udn:" + loc.group(1).rstrip("/").split("/")[-1],
                            "channel": "media",
                            "sub_type": "article",
                            "source": "udn.com",
                            "url": html.unescape(loc.group(1)),
                            "published_at": pub,
                            "published_at_basis": "html_comment_in_sitemap [推測: 與 lastmod 不同, 待驗證]" if pub else None,
                            "modified_at": lm.group(1) if lm else None,
                            "title": None,
                            "text": None,
                            "time_basis": "real" if pub else "unknown",
                            "collect_method": "udn_weekly_sitemap_meta",
                            "sitemap_file": fname,
                            "fetched_at": now_iso(),
                        })
                    n = len(rows)
                    with open(os.path.join(out_dir, fname + ".jsonl"), "w", encoding="utf-8") as f:
                        for r in rows:
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    files_ok += 1
                total_urls += n
                prog["done"][fname] = {"status": st, "urls": n, "at": now_iso()}
                save_json_atomic(prog_path, prog)
                update_progress(site, {
                    "weeks_checked": len(prog["done"]),
                    "sitemaps_with_data": files_ok,
                    "urls_listed": sum(v["urls"] for v in prog["done"].values()),
                    "last_file": fname,
                    "last_time": now_iso(),
                    "elapsed_s": int(time.time() - t0),
                })
                log(site, f"{fname} status {st} urls {n}")


# ---------------------------------------------------------------- merge
def merge():
    raw_dir = os.path.join(OUT, "raw", "media")
    os.makedirs(raw_dir, exist_ok=True)
    counts = {}
    for site in SITES:
        pdir = os.path.join(OUT, "raw_parts", site)
        if not os.path.isdir(pdir):
            continue
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith(".jsonl"):
                continue
            ym = fn[:-6]
            with open(os.path.join(pdir, fn), encoding="utf-8") as src, \
                    open(os.path.join(raw_dir, ym + ".jsonl"), "a", encoding="utf-8") as dst:
                for line in src:
                    dst.write(line)
                    counts[ym] = counts.get(ym, 0) + 1
    log("merge", f"raw/media rows by month: {counts}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("udn-meta")
    s = sub.add_parser("sample")
    s.add_argument("site", choices=sorted(SITES))
    s.add_argument("--months", nargs="*", default=None, help="yyyy-mm list; default all")
    s.add_argument("--k", type=int, default=30)
    p = sub.add_parser("probe")
    p.add_argument("site", choices=sorted(SITES))
    p.add_argument("id", type=int)
    sub.add_parser("merge")
    a = ap.parse_args()
    try:
        if a.cmd == "udn-meta":
            udn_meta()
        elif a.cmd == "sample":
            months = MONTHS
            if a.months:
                months = [tuple(int(x) for x in mm.split("-")) for mm in a.months]
            run_sample(a.site, months, a.k)
        elif a.cmd == "probe":
            st, parsed, final = fetch_article(a.site, a.id)
            print(json.dumps({"status": st, "final": final, "parsed": parsed and {
                k: (v[:200] + "..." if isinstance(v, str) and len(v) > 200 else v) for k, v in parsed.items()}},
                ensure_ascii=False, indent=1))
        elif a.cmd == "merge":
            merge()
    except HostPaused as e:
        log(a.cmd, f"HOST PAUSED: {e}")
        sys.exit(3)


if __name__ == "__main__":
    main()
