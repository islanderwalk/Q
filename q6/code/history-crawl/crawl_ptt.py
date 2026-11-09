# PTT board index history crawl: head increment (today's newest page -> the newest page of the last run),
# then the backfill (checkpoint -> back to 2023-01), then article bodies newest-first.
# Public pages, read-only, over18=1 cookie for Gossiping. One request per 3-5 s to ptt.cc with the
# shared adaptive throttle; every request goes to telemetry (api_trace / metrics / Loki / Pushgateway).
# Resumable: state/<board>.json holds the next index page; lists/<board>.jsonl dedups by url.
import json, re, sys, time, random, pathlib, traceback
from datetime import datetime
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # telem.py sits next to this file
from telem import Telemetry, Throttle, NetGuard, TW, h10, clean_url, now_iso  # noqa: E402

import os  # noqa: E402
D = pathlib.Path(os.environ.get("CRAWL_WORKDIR") or pathlib.Path(__file__).parent / "work") / "ptt"
for sub in ("lists", "articles", "state"):
    (D / sub).mkdir(parents=True, exist_ok=True)
BOARDS = ["Gossiping", "Kaohsiung", "Tainan", "Stock", "Baseball", "MobileComm", "Tech_Job", "WomenTalk", "Boy-Girl"]
CUTOFF_YM = (2023, 1)
BASE = "https://www.ptt.cc"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
S.cookies.set("over18", "1", domain=".ptt.cc")
tel = Telemetry("ptt_crawl", D / "telemetry", "ptt")
thr = Throttle(base=4.0, cap=600.0, pause=1800.0)
tel.set_state(delay_s=thr.delay, backoff_level=0, session="ok")
net = NetGuard(tel, "ptt")
PROG = D / "progress.json"


def get(url, board):
    thr.wait(lambda s: time.sleep(s + random.uniform(-0.8, 1.0) if s > 1 else s))
    t0 = time.time()
    st, body, err, exc = None, "", None, None
    try:
        r = S.get(url, timeout=30)
        st, body = r.status_code, r.text
        hdr = r.headers
    except Exception as e:
        err, hdr, exc = repr(e)[:200], {}, e
    ms = round((time.time() - t0) * 1000, 1)
    u, qn = clean_url(url)
    tel.trace({"platform": "ptt", "target": board, "method": "GET", "url": u, "query_names": qn, "resource_type": "document",
               "status": st, "latency_ms": ms, "bytes": len(body.encode()) if body else 0,
               "trace_headers": {k: h10(v) for k, v in hdr.items() if re.search(r"cf-ray|x-request-id|trace", k, re.I)},
               "failure": err}, api=True)
    if st is None and net.handle(exc, board):
        return st, body, False  # local disconnect: waited for the network, no backoff; caller retries
    if st is not None:
        net.ok()
    bad = []
    if st != 200:
        bad.append(f"http_{st}")
    if st == 200 and tel.latency_anomalous(ms):
        bad.append("latency_gt_2x_p95")
    if st == 200 and len(body) < 500:
        bad.append("short_response")
    if st == 200 and "over18" in body and "r-ent" not in body and "/bbs/" in url and "index" in url:
        bad.append("over18_gate")
    if bad:
        pause = thr.anomaly()
        tel.set_state(delay_s=thr.delay, backoff_level=thr.level)
        tel.event("anomaly", platform="ptt", target=board, url=u, why=bad, status=st, latency_ms=ms, new_delay_s=thr.delay,
                  backoff_level=thr.level, pause_s=pause)
        if pause:
            time.sleep(pause)
    else:
        thr.ok()
        tel.set_state(delay_s=thr.delay, backoff_level=thr.level)
    return st, body, bool(bad)


def ym_of(url):
    m = re.search(r"/M\.(\d{9,10})\.A\.", url)
    if not m:
        return None, None
    dt = datetime.fromtimestamp(int(m.group(1)), TW)
    return dt, (dt.year, dt.month)


def parse_index(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for ent in soup.select("div.r-ent"):
        a = ent.select_one("div.title a")
        if ent.find_previous_sibling("div", class_="r-list-sep") is not None:
            continue  # pinned announcements below the separator
        if not a:
            continue
        href = a["href"]
        dt, _ = ym_of(href)
        rows.append({"url": BASE + href, "title": a.get_text(strip=True),
                     "author": (ent.select_one("div.author").get_text(strip=True) if ent.select_one("div.author") else None),
                     "date_mmdd": (ent.select_one("div.date").get_text(strip=True) if ent.select_one("div.date") else None),
                     "published_at": dt.isoformat() if dt else None,
                     "push": (ent.select_one("div.nrec").get_text(strip=True) if ent.select_one("div.nrec") else "")})
    prev = None
    for a in soup.select("div.btn-group-paging a"):
        if "上頁" in a.get_text() and a.get("href"):
            prev = int(re.search(r"index(\d+)\.html", a["href"]).group(1))
    return rows, prev


def load_state(b):
    f = D / "state" / f"{b}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"next_page": None, "done": False, "pages": 0, "rows": 0,
                                                                        "earliest": None, "latest": None}


def save_state(b, s):
    (D / "state" / f"{b}.json").write_text(json.dumps({**s, "updated": now_iso()}, ensure_ascii=False, indent=1), encoding="utf-8")


def progress(phase, states):
    PROG.write_text(json.dumps({"updated": now_iso(), "phase": phase, "delay_s": thr.delay, "backoff_level": thr.level,
                                "boards": states}, ensure_ascii=False, indent=1), encoding="utf-8")


def load_seen():
    seen = {b: set() for b in BOARDS}
    for b in BOARDS:
        f = D / "lists" / f"{b}.jsonl"
        if f.exists():
            seen[b] = {json.loads(l)["url"] for l in open(f, encoding="utf-8") if l.strip()}
    return seen


def add_rows(b, s, rows, seen):
    new = [r for r in rows if r["url"] not in seen[b]]
    with open(D / "lists" / f"{b}.jsonl", "a", encoding="utf-8") as fo:
        for r in new:
            seen[b].add(r["url"])
            fo.write(json.dumps({"board": b, **r, "fetched_at": now_iso()}, ensure_ascii=False) + "\n")
    tel.add_posts("ptt", b, len(new))
    s["pages"] += 1
    s["rows"] += len(new)
    ds = [r["published_at"] for r in rows if r["published_at"]]
    if ds:
        s["earliest"] = min([x for x in (s["earliest"], min(ds)) if x])
        s["latest"] = max([x for x in (s["latest"], max(ds)) if x])
    return new


def crawl_head(states, seen):
    # Head increment: the backfill only walks toward older pages, so posts newer than the last run's newest page
    # are picked up here first: index.html -> back to the stored newest_page (inclusive), then newest_page moves up.
    # Progress lives in state["head"], so an interrupted head walk resumes where it stopped.
    active = []
    for b in BOARDS:
        s = states[b]
        if s.get("newest_page") and not s.get("head"):
            s["head"] = {"page": None, "top": None, "stop_at": s["newest_page"], "rows": 0, "started": now_iso()}
        if s.get("head"):
            active.append(b)
    while active:
        for b in list(active):
            s, h = states[b], states[b]["head"]
            url = f"{BASE}/bbs/{b}/index{h['page'] or ''}.html"
            st, html, bad = get(url, b)
            if st != 200:
                continue
            rows, prev = parse_index(html)
            cur = h["page"] or ((prev or 0) + 1)
            h["top"] = h["top"] or cur
            h["rows"] += len(add_rows(b, s, rows, seen))
            if cur <= h["stop_at"] or not prev:
                s["newest_page"] = h["top"]
                s["head_last"] = {"rows": h["rows"], "pages": h["top"] - h["stop_at"] + 1, "from": h["stop_at"], "to": h["top"],
                                  "finished": now_iso()}
                tel.event("head_done", platform="ptt", target=b, new_rows=h["rows"], from_page=h["stop_at"], to_page=h["top"])
                del s["head"]
                active.remove(b)
            else:
                h["page"] = prev
            save_state(b, s)
            progress("head", states)
    return states


def crawl_lists(states, seen):
    active = [b for b in BOARDS if not states[b]["done"]]
    while active:  # round-robin boards so one huge board (Gossiping) doesn't starve the rest
        for b in list(active):
            s = states[b]
            url = f"{BASE}/bbs/{b}/index{s['next_page'] if s['next_page'] else ''}.html"
            st, html, bad = get(url, b)
            if st == 404 and s["next_page"] is not None:
                s["next_page"] -= 1
                continue
            if st != 200:
                continue  # throttle already slowed down; retry same page next round
            rows, prev = parse_index(html)
            add_rows(b, s, rows, seen)
            yms = [ym_of(r["url"])[1] for r in rows if ym_of(r["url"])[1]]
            if s["next_page"] is None and prev:
                s["newest_page"] = prev + 1
            s["next_page"] = prev
            if (yms and max(yms) < CUTOFF_YM) or not prev:
                s["done"] = True
                s["reason"] = "reached_before_2023-01" if yms and max(yms) < CUTOFF_YM else "first_page"
                tel.event("board_list_done", platform="ptt", target=b, pages=s["pages"], rows=s["rows"], earliest=s["earliest"])
                active.remove(b)
            save_state(b, s)
            progress("lists", states)
            if s["pages"] % 50 == 0:
                print(f"[ptt/{b}] page {s['pages']} idx={s['next_page']} rows={s['rows']} earliest={s['earliest']} delay={thr.delay}", flush=True)
    return states


def crawl_articles(states):
    # Bodies after every list is finished; articles are fetched newest-first per board, round-robin.
    queues = {}
    for b in BOARDS:
        done = set()
        f = D / "articles" / f"{b}.jsonl"
        if f.exists():
            done = {json.loads(l)["url"] for l in open(f, encoding="utf-8") if l.strip()}
        rows = [json.loads(l) for l in open(D / "lists" / f"{b}.jsonl", encoding="utf-8") if l.strip()]
        queues[b] = sorted((r for r in rows if r["url"] not in done and (r.get("published_at") or "") >= "2023-01-01"),
                           key=lambda r: r["published_at"], reverse=True)  # head-increment rows are appended last; fetch newest first
    while any(queues.values()):
        for b in BOARDS:
            if not queues[b]:
                continue
            r = queues[b].pop(0)
            st, html, bad = get(r["url"], b)
            if st != 200:
                if st == 404:
                    rec = {"url": r["url"], "status": 404, "deleted": True}
                else:
                    queues[b].append(r)
                    continue
            else:
                soup = BeautifulSoup(html, "html.parser")
                main = soup.select_one("#main-content")
                meta = {m.select_one(".article-meta-tag").get_text(strip=True): m.select_one(".article-meta-value").get_text(strip=True)
                        for m in soup.select("div.article-metaline, div.article-metaline-right") if m.select_one(".article-meta-tag")}
                pushes = [{"tag": p.select_one(".push-tag").get_text(strip=True), "user": p.select_one(".push-userid").get_text(strip=True),
                           "content": p.select_one(".push-content").get_text(strip=True)[2:],
                           "time": p.select_one(".push-ipdatetime").get_text(strip=True)}
                          for p in soup.select("div.push") if p.select_one(".push-userid")]
                if main:
                    for x in main.select("div.article-metaline, div.article-metaline-right, div.push"):
                        x.extract()
                rec = {"url": r["url"], "status": 200, "board": b, "title": meta.get("標題", r["title"]), "author": meta.get("作者", r["author"]),
                       "time_text": meta.get("時間"), "published_at": r.get("published_at"),
                       "text": main.get_text("\n", strip=True) if main else "", "push_count": len(pushes),
                       "push_up": sum(p["tag"] == "推" for p in pushes), "push_down": sum(p["tag"] == "噓" for p in pushes),
                       "pushes": pushes, "fetched_at": now_iso()}
            with open(D / "articles" / f"{b}.jsonl", "a", encoding="utf-8") as fo:
                fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            states[b]["articles_done"] = states[b].get("articles_done", 0) + 1
            states[b]["articles_left"] = len(queues[b])
        progress("articles", states)


if __name__ == "__main__":
    try:
        states = {b: load_state(b) for b in BOARDS}
        seen = load_seen()
        crawl_head(states, seen)
        tel.event("head_all_done", platform="ptt", boards={b: s.get("head_last") for b, s in states.items()})
        st = crawl_lists(states, seen)
        tel.event("lists_done", platform="ptt", boards={b: {"rows": s["rows"], "earliest": s["earliest"]} for b, s in st.items()})
        crawl_articles(st)
        tel.event("articles_done", platform="ptt")
    except Exception as e:
        tel.event("crash", platform="ptt", error=repr(e)[:300], tb=traceback.format_exc()[-800:])
        raise
    finally:
        tel.close()
    print("PTT_DONE", flush=True)
