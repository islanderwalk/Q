# Deep (to 2023-01) logged-in crawl of the same FB / IG / Threads targets, using the operator's own logged-in profile (./profile).
# Read-only. The page loads normally once and is scrolled until it issues its own timeline pagination GraphQL
# request; that request (same URL, same in-page tokens) is then replayed from inside the page with only the
# cursor changed. Tokens never leave the browser; nothing secret is written to disk.
# The three platforms run round-robin, each with its own adaptive throttle (telem.Throttle).
# Stops a target when a whole page is older than 2023-01-01, the timeline ends, or it stalls.
# Stops a PLATFORM only on logout / checkpoint / lock. Resumable from deep/state/*.json.
# Every run starts each target that already has posts with a head increment: replay from the top of the timeline until a
# page holds nothing newer than the previous run's latest post, then switch back to the saved backfill cursor (finished
# targets stay finished). Local network loss pauses the crawl (telem.NetGuard) instead of counting as an anomaly.
import json, time, sys, re, random, ctypes, ctypes.wintypes as wt, pathlib, traceback
from datetime import datetime
from urllib.parse import urlencode, parse_qsl
from playwright.sync_api import sync_playwright
from PIL import Image, ImageGrab, ImageFilter, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "meta-login"))  # crawl_loggedin, redact
_argv = sys.argv
sys.argv = [sys.argv[0], "0"]
import crawl_loggedin as C  # noqa: E402  (parse_json_multi, walk, find_window, user32, PROFILE, devtools prefs)
import redact  # noqa: E402
from telem import Telemetry, Throttle, NetGuard, net_up, TW, h10, clean_url, now_iso  # noqa: E402
sys.argv = _argv

import os  # noqa: E402
D = pathlib.Path(os.environ.get("CRAWL_WORKDIR") or pathlib.Path(__file__).parent / "work")
DEEP = D / "deep"
for sub in ("raw", "clean", "dropped", "state", "evidence/redacted"):
    (DEEP / sub).mkdir(parents=True, exist_ok=True)
SHOTS = DEEP / "evidence" / "redacted"
CUTOFF = datetime(2023, 1, 1, tzinfo=TW).timestamp()
ONLY = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
HEAD_MAX_PAGES = 300
REPLAYING = ("replay", "head")  # modes in which the page's own pagination requests are ignored

# 民視 is pinned to FTVNews53: logged in, its tab title is "(1) Facebook" and the follower count renders after the first
# 6,000 body chars, so the @FTV probe (h1/og:title must name 民視 + >=100k followers) skipped the right page on 2026-11-03.
TARGETS = {"facebook": ["cnanewstaiwan", "PNNPTS", "NASA", "FTVNews53"],
           "instagram": ["cnanews_tw", "pts.news", "ftvnews", "nasa"],
           "threads": ["cnanews_tw", "pts.news", "ftvnews"]}
URL = {"facebook": "https://www.facebook.com/{}", "instagram": "https://www.instagram.com/{}/", "threads": "https://www.threads.com/@{}"}
TPL_RE = {"facebook": re.compile(r"^ProfileCometTimelineFeedRefetchQuery$"),
          "instagram": re.compile(r"^PolarisProfilePostsTabContentQuery_connection$"),
          "threads": re.compile(r"^BarcelonaProfileThreadsTabRefetchableDirectQuery$")}
PI_PREF = ("timeline_list_feed_units", "xdt_api__v1__feed__user_timeline_graphql_connection", "mediaData")
MAIN_COOKIE = {"facebook": ("https://www.facebook.com", "c_user"), "instagram": ("https://www.instagram.com", "sessionid"),
               "threads": ("https://www.threads.com", "sessionid")}
# FTVNews53 = 民視新聞 official page (160萬 followers, page id 100044539895059, confirmed logged-out via og:title)
FTV_CANDIDATES = ["profile.php?id=100044539895059", "FTVNews53"]
API_RE = re.compile(r"/api/graphql|/graphql/query|/api/v1/|/ajax/")
TRACE_HDR = re.compile(r"trace|request-id|x-fb-debug|x-fb-rev|x-ig-origin-region|x-fb-connection-quality|x-fb-server-cluster", re.I)
STOP_MARK = ("checkpoint_required", "/checkpoint/", "challenge_required", "login_required", '"require_login":true',
             "1357001", "account_suspended", "/accounts/suspended", "/accounts/login")
SLOW_MARK = ("please wait a few minutes", "rate limit", '"spam":true', "1675004", "too many requests", "try again later")
FORBID_HDR = re.compile(r"^(cookie|content-length|host|origin|referer|user-agent|sec-|accept-encoding|connection|priority)", re.I)

FORCE = """m => { window.__t = document.title; window.__qsev = setInterval(() => {
  if (!document.title.startsWith(m)) document.title = m + ' ' + (window.__t || ''); }, 150); }"""
UNFORCE = """() => { clearInterval(window.__qsev); if (window.__t !== undefined) document.title = window.__t; }"""
JS_FETCH = """async ({url, headers, body}) => {
  const t0 = performance.now();
  try {
    const r = await fetch(url, {method: 'POST', headers, body, credentials: 'include'});
    const text = await r.text();
    return {status: r.status, ms: performance.now() - t0, text};
  } catch (e) { return {status: null, ms: performance.now() - t0, text: '', error: String(e)}; }
}"""


# ---------------- extraction
def walk_paths(o):
    st = [(o, "")]
    while st:
        n, p = st.pop()
        if isinstance(n, dict):
            yield n, p
            st.extend((v, f"{p}.{k}") for k, v in n.items())
        elif isinstance(n, list):
            st.extend((v, p + "[]") for v in n)


def first(o, pred):
    for n in C.walk(o):
        v = pred(n)
        if v is not None:
            return v
    return None


def page_info(objs):
    best = None
    for o in objs:
        for n, p in walk_paths(o):
            pi = n.get("page_info")
            if isinstance(pi, dict) and "end_cursor" in pi:
                cand = (any(k in p for k in PI_PREF), p, pi)
                if best is None or cand[0] > best[0]:
                    best = cand
    return (best[2], best[1]) if best else (None, None)


def fb_stories(o, out):
    st = [o]
    while st:
        n = st.pop()
        if isinstance(n, list):
            st.extend(n)
            continue
        if not isinstance(n, dict):
            continue
        pid = n.get("post_id")
        if isinstance(pid, str) and pid.isdigit():
            txt = first(n, lambda d: d["message"]["text"] if isinstance(d.get("message"), dict) and isinstance(d["message"].get("text"), str) else None)
            ts = first(n, lambda d: d["creation_time"] if isinstance(d.get("creation_time"), int) else None)
            url = first(n, lambda d: next((d[k] for k in ("permalink_url", "url", "wwwURL") if isinstance(d.get(k), str)
                                           and re.search(r"facebook\.com/.+/(posts|videos|reel|photos)/|story_fbid|/permalink", d[k])), None))
            likes = first(n, lambda d: d["reaction_count"]["count"] if isinstance(d.get("reaction_count"), dict) and isinstance(d["reaction_count"].get("count"), int) else None)
            if likes is None:
                likes = first(n, lambda d: d["reactors"]["count"] if isinstance(d.get("reactors"), dict) and isinstance(d["reactors"].get("count"), int) else None)
            comments = first(n, lambda d: d["total_comment_count"] if isinstance(d.get("total_comment_count"), int) else None)
            if comments is None:
                comments = first(n, lambda d: d["comments"]["total_count"] if isinstance(d.get("comments"), dict) and isinstance(d["comments"].get("total_count"), int) else None)
            shares = first(n, lambda d: d["share_count"]["count"] if isinstance(d.get("share_count"), dict) and isinstance(d["share_count"].get("count"), int) else None)
            actor = first(n, lambda d: d["actors"][0].get("name") if isinstance(d.get("actors"), list) and d["actors"] and isinstance(d["actors"][0], dict) else None)
            prev = out.get(pid, {})
            out[pid] = {"pid": pid, "text": txt or prev.get("text") or "", "t": ts or prev.get("t"), "url": url or prev.get("url"),
                        "username": actor or prev.get("username"), "like_count": likes if likes is not None else prev.get("like_count"),
                        "comment_count": comments if comments is not None else prev.get("comment_count"),
                        "share_count": shares if shares is not None else prev.get("share_count")}
            continue  # outermost story only (nested shared stories stay inside it)
        st.extend(n.values())


def meta_posts(plat, objs):
    out = {}
    for o in objs:
        if plat == "facebook":
            fb_stories(o, out)
            continue
        for n in C.walk(o):
            if not (isinstance(n.get("code"), str) and isinstance(n.get("taken_at"), int)):
                continue
            user = n.get("user") if isinstance(n.get("user"), dict) else (n.get("owner") if isinstance(n.get("owner"), dict) else {})
            if not user.get("username"):
                continue
            cap = n.get("caption") if isinstance(n.get("caption"), dict) else {}
            info = n.get("text_post_app_info") if isinstance(n.get("text_post_app_info"), dict) else {}
            code, un = n["code"], user["username"]
            out[code] = {"pid": code, "text": cap.get("text") or "", "t": n["taken_at"], "username": un,
                         "url": (f"https://www.threads.com/@{un}/post/{code}" if plat == "threads" else f"https://www.instagram.com/p/{code}/"),
                         "product_type": n.get("product_type"), "like_count": n.get("like_count"),
                         "comment_count": n.get("comment_count") if plat == "instagram" else info.get("direct_reply_count"),
                         "play_count": n.get("play_count") or n.get("ig_play_count") or n.get("view_count"),
                         "repost_count": info.get("repost_count"), "quote_count": info.get("quote_count"),
                         "is_reply": bool(info.get("is_reply"))}
    return out


def to_rec(plat, slug, r):
    return {"post_id": f"{plat[:2]}-{r['pid']}", "channel": plat,
            "sub_type": "reel" if r.get("product_type") == "clips" else "comment_thread" if r.get("is_reply") else "post",
            "source": slug, "author": r.get("username"), "url": r.get("url"), "published_at": C.iso(r.get("t")),
            "fetched_at": now_iso(), "text": r.get("text") or "", "collect_method": "loggedin_graphql_cursor_replay",
            **{k: r.get(k) for k in ("like_count", "comment_count", "play_count", "share_count", "repost_count", "quote_count")}}


# ---------------- screenshots (redacted only; originals are never written)
def blur_save(im, name):
    im = im.convert("RGB")
    for x0, y0, x1, y1 in redact.boxes(name, *im.size):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)
        if x1 > x0 and y1 > y0:
            im.paste(im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(14)), (x0, y0))
            ImageDraw.Draw(im).rectangle((x0, y0, x1 - 1, y1 - 1), outline=(255, 64, 64), width=2)
    im.save(SHOTS / name)
    return name


class Target:
    def __init__(self, plat, slug):
        self.plat, self.slug = plat, slug
        self.f_state = DEEP / "state" / f"{plat}_{re.sub(r'[^0-9A-Za-z_-]', '_', slug)}.json"
        s = json.loads(self.f_state.read_text(encoding="utf-8")) if self.f_state.exists() else {}
        self.cursor, self.done, self.reason = s.get("cursor"), s.get("done", False), s.get("reason")
        self.pages, self.posts, self.dropped = s.get("pages", 0), s.get("posts", 0), s.get("dropped", 0)
        self.earliest, self.latest = s.get("earliest"), s.get("latest")
        self.head_last = s.get("head_last")
        self.head = s.get("head") or ({"floor": self.latest, "resume_cursor": self.cursor, "cursor": None, "added": 0, "pages": 0,
                                       "errors": 0, "started": now_iso()} if self.posts and self.latest else None)
        self.seen = set()
        raw = DEEP / "raw" / f"{plat}.jsonl"
        if raw.exists():
            for line in open(raw, encoding="utf-8"):
                try:
                    x = json.loads(line)
                except Exception:
                    continue
                if x.get("source") == slug:
                    self.seen.add(x["post_id"])
        self.page = None
        self.tpl = None
        self.mode = "init"
        self.stall = 0
        self.shot_pending = []
        self.next_shot_at = (self.posts // 100 + 1) * 100
        self.bumped = None

    def save(self):
        if self.head is not None and self.mode == "head":
            self.head["cursor"] = self.cursor
        cur = self.head["resume_cursor"] if self.head is not None else self.cursor
        self.f_state.write_text(json.dumps({"cursor": cur, "done": self.done, "reason": self.reason, "pages": self.pages,
                                            "posts": self.posts, "dropped": self.dropped, "earliest": self.earliest,
                                            "latest": self.latest, "head": self.head, "head_last": self.head_last,
                                            "updated": now_iso()}, ensure_ascii=False, indent=1), encoding="utf-8")


class Platform:
    def __init__(self, plat, ctx):
        self.plat, self.ctx = plat, ctx
        self.tel = Telemetry("meta_crawl", DEEP / "telemetry" / plat, plat)
        self.thr = Throttle(base=5.0, cap=600.0, pause=1800.0)
        self.tel.set_state(delay_s=self.thr.delay, backoff_level=0, session="ok")
        self.net = NetGuard(self.tel, plat)
        self.queue = [Target(plat, s) for s in TARGETS[plat] if s != "@FTV"]
        self.want_ftv = "@FTV" in TARGETS[plat]
        self.cur = None
        self.next_at = 0.0
        self.stopped = None
        self.steps = 0
        self.last_shot = 0.0
        self.cookie_hash = None

    # ---- network listeners
    def attach(self, t):
        starts = {}

        def on_req(rq):
            starts[rq] = time.time()
            if t.mode not in REPLAYING and rq.method == "POST" and API_RE.search(rq.url):
                try:
                    form = parse_qsl(rq.post_data or "", keep_blank_values=True)
                except Exception:
                    return
                fd = dict(form)
                fn = rq.headers.get("x-fb-friendly-name") or fd.get("fb_api_req_friendly_name")
                if fn and TPL_RE[self.plat].match(fn) and "variables" in fd:
                    t.tpl = {"url": rq.url, "form": form, "fn": fn,
                             "headers": {k: v for k, v in rq.headers.items() if not FORBID_HDR.match(k)}}

        def on_resp(resp):
            rq = resp.request
            if t.mode in REPLAYING or rq.method != "POST" or not t.tpl or rq.url != t.tpl["url"]:
                return
            fd = dict(parse_qsl(rq.post_data or "", keep_blank_values=True))
            fn = rq.headers.get("x-fb-friendly-name") or fd.get("fb_api_req_friendly_name")
            if not (fn and TPL_RE[self.plat].match(fn)):
                return
            try:
                objs = C.parse_json_multi(resp.text())
            except Exception:
                return
            self.ingest(t, objs)
            pi, _ = page_info(objs)
            if pi and pi.get("end_cursor"):
                t.cursor = pi["end_cursor"]

        def on_done(rq, failed=False):
            t0 = starts.pop(rq, None)
            try:
                resp = None if failed else rq.response()
            except Exception:
                resp = None
            tm = rq.timing or {}
            lat = tm.get("responseEnd") if (tm.get("responseEnd") or -1) > 0 else (round((time.time() - t0) * 1000, 1) if t0 else None)
            u, qn = clean_url(rq.url)
            try:
                hdrs = resp.headers if resp else {}
            except Exception:
                hdrs = {}
            fn = doc = None
            if rq.method == "POST":
                try:
                    fd = dict(parse_qsl(rq.post_data or "", keep_blank_values=True))
                    fn, doc = rq.headers.get("x-fb-friendly-name") or fd.get("fb_api_req_friendly_name"), fd.get("doc_id")
                except Exception:
                    pass
            api = bool(API_RE.search(rq.url)) or rq.resource_type == "document"
            st = resp.status if resp else None
            rec = {"platform": self.plat, "target": t.slug, "method": rq.method, "url": u, "query_names": qn,
                   "resource_type": rq.resource_type, "status": st, "latency_ms": round(lat, 1) if isinstance(lat, (int, float)) else None,
                   "ttfb_ms": round(tm["responseStart"], 1) if (tm.get("responseStart") or -1) > 0 else None,
                   "dns_ms": round(tm["domainLookupEnd"] - tm["domainLookupStart"], 1) if (tm.get("domainLookupStart") or -1) >= 0 and (tm.get("domainLookupEnd") or -1) >= 0 else None,
                   "connect_ms": round(tm["connectEnd"] - tm["connectStart"], 1) if (tm.get("connectStart") or -1) >= 0 and (tm.get("connectEnd") or -1) >= 0 else None,
                   "bytes": int(hdrs.get("content-length")) if str(hdrs.get("content-length", "")).isdigit() else None,
                   "friendly_name": fn, "doc_id": doc, "mode": t.mode,
                   "trace_headers": {k: h10(v) for k, v in hdrs.items() if TRACE_HDR.search(k)},
                   "failure": (rq.failure if failed else None)}
            self.tel.trace(rec, api=api)
            if api and st is not None and st != 200 and not (300 <= st < 400):
                t.shot_pending.append(f"http{st}")

        t.page.on("request", on_req)
        t.page.on("response", on_resp)
        t.page.on("requestfinished", lambda rq: on_done(rq))
        t.page.on("requestfailed", lambda rq: on_done(rq, True))

    # ---- helpers
    def ingest(self, t, objs):
        rows = meta_posts(self.plat, objs)
        new_raw, new_clean, new_drop, ts_all = [], [], [], []
        for r in rows.values():
            if r.get("t"):
                ts_all.append(r["t"])
            rec = to_rec(self.plat, t.slug, r)
            if rec["post_id"] in t.seen:
                continue
            t.seen.add(rec["post_id"])
            new_raw.append(rec)
            own = self.plat == "facebook" or (r.get("username") or "").lower() == t.slug.lower()
            (new_clean if own else new_drop).append(rec)
        for name, lst in (("raw", new_raw), ("clean", new_clean), ("dropped", new_drop)):
            if lst:
                with open(DEEP / name / f"{self.plat}.jsonl", "a", encoding="utf-8") as f:
                    for x in lst:
                        f.write(json.dumps(x, ensure_ascii=False) + "\n")
        t.posts += len(new_clean)
        t.dropped += len(new_drop)
        ds = [x["published_at"] for x in new_clean if x["published_at"]]
        if ds:
            t.earliest = min([t.earliest] + ds) if t.earliest else min(ds)
            t.latest = max([t.latest] + ds) if t.latest else max(ds)
        self.tel.add_posts(self.plat, t.slug, len(new_clean))
        if t.posts >= t.next_shot_at:
            t.shot_pending.append(f"milestone{t.posts // 100 * 100}")
            t.next_shot_at = (t.posts // 100 + 1) * 100
        return len(new_clean), (max(ts_all) if ts_all else None)

    def shoot(self, t, reason, force=False):
        if not t.page or t.page.is_closed():
            return
        if not force and time.time() - self.last_shot < 20:
            self.tel.event("shot_skipped_ratelimit", platform=self.plat, target=t.slug, reason=reason)
            return
        self.last_shot = time.time()
        stamp = datetime.now(TW).strftime("%Y%m%dT%H%M%S")
        base = f"{self.plat}_{t.slug.replace('.', '_')}_{stamp}_{reason}"
        names = []
        try:
            t.page.bring_to_front()
            tmp = DEEP / "_tmp.png"
            t.page.screenshot(path=str(tmp))
            names.append(blur_save(Image.open(tmp), f"{base}_top.png"))
            tmp.unlink(missing_ok=True)
            marker = f"QSEV{int(time.time() * 1000)}"
            t.page.evaluate(FORCE, marker)
            hwnd = None
            for _ in range(16):
                t.page.wait_for_timeout(500)
                hwnd = C.find_window(marker)
                if hwnd:
                    break
            if hwnd:
                C.user32.ShowWindow(hwnd, 3)
                C.user32.SetForegroundWindow(hwnd)
                t.page.wait_for_timeout(1200)
                r = wt.RECT()
                C.user32.GetWindowRect(hwnd, ctypes.byref(r))
                names.append(blur_save(ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True), f"{base}_devtools_F12.png"))
            t.page.evaluate(UNFORCE)
        except Exception as e:
            self.tel.event("shot_error", platform=self.plat, target=t.slug, reason=reason, error=str(e)[:200])
        self.tel.event("screenshot", platform=self.plat, target=t.slug, reason=reason, files=names)

    def check_cookie(self):
        url, name = MAIN_COOKIE[self.plat]
        v = next((c["value"] for c in self.ctx.cookies(url) if c["name"] == name and c["value"]), None)
        hv = h10(v)
        if v is None:
            return "missing"
        if self.cookie_hash and hv != self.cookie_hash:
            self.tel.event("session_cookie_rotated", platform=self.plat, cookie=name, old=self.cookie_hash, new=hv)
            self.cookie_hash = hv
            return "rotated"
        self.cookie_hash = hv
        return "ok"

    def stop_platform(self, t, why):
        self.stopped = why
        self.tel.set_state(session=why)
        self.tel.event("PLATFORM_STOPPED", platform=self.plat, target=t.slug if t else None, reason=why)
        if t:
            self.shoot(t, "session_" + re.sub(r"\W+", "_", why)[:30], force=True)
            t.save()

    def anomaly(self, t, why, **kw):
        pause = self.thr.anomaly()
        self.tel.set_state(delay_s=self.thr.delay, backoff_level=self.thr.level)
        self.tel.event("anomaly", platform=self.plat, target=t.slug, why=why, new_delay_s=self.thr.delay,
                       backoff_level=self.thr.level, pause_s=pause, **kw)
        t.shot_pending.append("anomaly_" + re.sub(r"\W+", "_", why)[:24])
        return pause

    def finish(self, t, reason):
        t.done, t.reason = True, reason
        t.save()
        self.shoot(t, "done", force=True)
        self.tel.event("target_done", platform=self.plat, target=t.slug, reason=reason, posts=t.posts, dropped=t.dropped,
                       earliest=t.earliest, latest=t.latest, pages=t.pages)
        try:
            t.page.close()
        except Exception:
            pass
        t.page = None
        self.cur = None
        if t not in DONE[self.plat]:
            DONE[self.plat].append(t)

    def end_head(self, t, why):
        h, t.head = t.head, None
        t.head_last = {"added": h["added"], "pages": h["pages"], "floor": h["floor"], "why": why, "finished": now_iso()}
        self.tel.event("head_done", platform=self.plat, target=t.slug, new_posts=h["added"], pages=h["pages"], floor=h["floor"], why=why)
        if t.done:
            return self.finish(t, t.reason)  # backfill already finished earlier; keep its reason
        t.cursor = h["resume_cursor"] or t.cursor
        t.mode = "replay" if (t.tpl and t.cursor) else "scroll"
        t.stall = 0
        t.save()
        self.next_at = time.time() + self.thr.delay

    # ---- FTV slug discovery (the old 'ftvnews53' showed "content not available")
    def resolve_ftv(self):
        # A page counts only if its name (h1 / og:title / tab title) has 民視 and it has >= 100k followers (not a fan or copycat page).
        page = self.ctx.new_page()
        found = None

        def probe(url, label):
            time.sleep(5)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(12000)
                # FB's chat sidebar owns the first <h1> ("聊天室"), so read the page name from every h1 + og:title + tab title
                h1 = [x.strip() for x in page.locator("h1").all_inner_texts() if x.strip() and x.strip() != "聊天室"]
                h1 += [x for x in page.evaluate("""() => [document.title, (document.querySelector('meta[property="og:title"]')||{}).content||'']""") if x]
                body = page.inner_text("body")[:6000]
            except Exception as e:
                self.tel.event("ftv_probe", platform="facebook", slug=label, error=str(e)[:120])
                return None
            fol = re.search(r"([\d.,]+\s*[萬千]?)\s*(位追蹤者|followers)", body)
            big = bool(fol) and ("萬" in fol.group(1) or int(re.sub(r"\D", "", fol.group(1)) or 0) >= 100000)
            ok = any("民視" in x for x in h1) and big and "無法查看" not in body and "isn't available" not in body
            self.tel.event("ftv_probe", platform="facebook", slug=label, h1=h1[:3], followers=fol.group(1) if fol else None, ok=ok,
                           final_url=page.url.split("?")[0])
            return (page.url, fol.group(1) if fol else "") if ok else None

        try:
            for slug in FTV_CANDIDATES:
                r = probe(URL["facebook"].format(slug), slug)
                if r:
                    u = r[0]
                    m = re.search(r"profile\.php\?id=(\d+)", u)
                    found = f"profile.php?id={m.group(1)}" if m else u.split("?")[0].rstrip("/").split("/")[-1]
                    break
            if not found:
                time.sleep(5)
                page.goto("https://www.facebook.com/search/pages?q=%E6%B0%91%E8%A6%96%E6%96%B0%E8%81%9E", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(7000)
                hrefs = page.evaluate("""() => [...new Set(Array.from(document.querySelectorAll('a[href*="facebook.com/"]'))
                    .filter(a => /民視/.test(a.innerText)).map(a => a.href))]""")
                self.tel.event("ftv_search", platform="facebook", candidates=[re.sub(r"[?&](?!id=)[^&]*", "", h) for h in hrefs[:8]])
                for hrf in hrefs[:5]:
                    m = re.match(r"https://www\.facebook\.com/profile\.php\?id=(\d+)", hrf) or re.match(r"https://www\.facebook\.com/([^/?#]+)/?(?:\?|$)", hrf)
                    if not m or m.group(1) in ("search", "groups", "watch", "events"):
                        continue
                    slug = f"profile.php?id={m.group(1)}" if m.group(1).isdigit() else m.group(1)
                    if probe(f"https://www.facebook.com/{slug}", slug):
                        found = slug
                        break
        finally:
            page.close()
        self.tel.event("ftv_resolved", platform="facebook", slug=found, note=None if found else "skipped: no page whose h1 names 民視")
        return found

    # ---- one scheduler step for this platform
    def step(self):
        self.steps += 1
        if self.cur is None:
            if self.want_ftv:
                self.want_ftv = False
                slug = self.resolve_ftv()
                if slug:
                    self.queue.append(Target("facebook", slug))
                self.next_at = time.time() + self.thr.delay
                return
            while self.queue and self.queue[0].done and self.queue[0].head is None:
                self.queue.pop(0)
            if not self.queue:
                self.stopped = self.stopped or "all_targets_done"
                return
            self.cur = self.queue.pop(0)
            self.open_target(self.cur)
            self.next_at = time.time() + self.thr.delay
            return
        t = self.cur
        if self.steps % 10 == 0:
            ck = self.check_cookie()
            if ck == "missing":
                return self.stop_platform(t, "session_cookie_missing")
            if ck == "rotated":
                t.shot_pending.append("cookie_rotated")
        if t.mode == "scroll":
            self.scroll_step(t)
        else:
            self.replay_step(t)
        while t.shot_pending and t.page:
            self.shoot(t, t.shot_pending.pop(0))
            t.shot_pending.clear()
        self.write_progress()

    def open_target(self, t):
        t.page = self.ctx.new_page()
        t.mode = "load"
        self.attach(t)
        url = URL[self.plat].format(t.slug) if not t.slug.startswith("profile.php") else f"https://www.facebook.com/{t.slug}"
        saved_cursor = t.cursor
        posts0 = t.posts
        self.tel.set_state(target=t.slug)
        self.tel.event("target_open", platform=self.plat, target=t.slug, url=url, resume_cursor=h10(saved_cursor), resumed_posts=t.posts,
                       head_floor=t.head["floor"] if t.head else None)
        for attempt in range(5):
            try:
                r = t.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self.tel.event("page_status", platform=self.plat, target=t.slug, status=r.status if r else None)
                self.net.ok()
                break
            except Exception as e:
                if attempt < 4 and self.net.handle(e, t.slug):
                    continue  # local disconnect: waited for the network, reload without backoff
                self.anomaly(t, "goto_error", error=str(e)[:160])
                break
        t.page.wait_for_timeout(6000)
        fin = t.page.url.lower()
        if any(m in fin for m in ("/login", "/checkpoint", "/challenge", "/accounts/suspended")):
            return self.stop_platform(t, "redirected_to_" + fin.split("/")[3][:20])
        if t.page.locator("input[name=pass], input[name=password]").count():
            return self.stop_platform(t, "login_form_visible")
        self.cookie_hash = None
        self.check_cookie()
        try:
            inline = [o for s in t.page.evaluate(C.INLINE_JSON) for o in C.parse_json_multi(s)]
            self.ingest(t, inline)
        except Exception:
            pass
        self.shoot(t, "open", force=True)
        for i in range(15):
            if t.tpl and t.cursor:
                break
            t.page.mouse.wheel(0, 3000)
            t.page.wait_for_timeout(3500)
        if t.tpl:
            self.tel.event("template_captured", platform=self.plat, target=t.slug, friendly_name=t.tpl["fn"],
                           doc_id=dict(t.tpl["form"]).get("doc_id"), variables_keys=sorted(json.loads(dict(t.tpl["form"])["variables"]).keys()))
        if t.head is not None:
            t.head["added"] += t.posts - posts0  # posts above the fold, loaded by the page itself
            if t.tpl and t.cursor:
                if t.head.get("cursor"):
                    t.cursor = t.head["cursor"]  # resume an interrupted head walk
                t.mode = "head"
                self.tel.event("head_start", platform=self.plat, target=t.slug, floor=t.head["floor"], resumed=bool(t.head.get("cursor")))
                t.save()
                return
            self.end_head(t, "template_missing")
            if t.done:
                return
        if saved_cursor:
            t.cursor = saved_cursor
        if t.tpl and t.cursor:
            t.mode = "replay"
        else:
            t.mode = "scroll"
            self.tel.event("template_missing_fallback_scroll", platform=self.plat, target=t.slug)
        t.save()

    def check_text(self, t, text, status):
        low = (text or "")[:6000].lower()
        if any(m in low for m in STOP_MARK):
            mk = next(m for m in STOP_MARK if m in low)
            return "stop", mk
        if status == 429 or any(m in low for m in SLOW_MARK):
            return "slow", next((m for m in SLOW_MARK if m in low), "http429")
        return None, None

    def replay_step(self, t):
        form = list(t.tpl["form"])
        fd = dict(form)
        v = json.loads(fd["variables"])
        ck = "cursor" if "cursor" in v else "after"
        v[ck] = t.cursor
        if self.plat == "facebook" and isinstance(v.get("count"), int) and t.bumped is not False and v["count"] < 10:
            v["count"] = 10
            t.bumped = True
        body = urlencode([(k, json.dumps(v, separators=(",", ":"), ensure_ascii=False) if k == "variables" else val) for k, val in form])
        res = t.page.evaluate(JS_FETCH, {"url": t.tpl["url"], "headers": t.tpl["headers"], "body": body})
        st, ms, text = res.get("status"), res.get("ms"), res.get("text") or ""
        if st is None and self.net.handle(res.get("error") or "fetch failed", t.slug):
            self.next_at = time.time() + self.thr.delay  # local disconnect: same cursor again, no backoff
            return
        if st is not None:
            self.net.ok()
        t.pages += 1
        pause = 0.0
        verdict, mark = self.check_text(t, text, st)
        if verdict == "stop":
            return self.stop_platform(t, f"marker:{mark}")
        objs = C.parse_json_multi(text)
        errs = [e.get("message", "")[:120] for o in objs if isinstance(o, dict) for e in (o.get("errors") or []) if isinstance(e, dict)]
        n_new, page_max = self.ingest(t, objs)
        pi, pi_path = page_info(objs)
        anomalous = []
        if st != 200:
            anomalous.append(f"http_{st}")
        if verdict == "slow":
            anomalous.append(f"ratelimit:{mark}")
        if st == 200 and self.tel.latency_anomalous(ms):
            anomalous.append("latency_gt_2x_p95")
        if len(text) < 200:
            anomalous.append("short_response")
        if errs:
            anomalous.append("graphql_errors")
        if n_new == 0 and t.mode != "head" and not (pi and pi.get("has_next_page") is False):
            t.stall += 1
            anomalous.append("no_new_posts")
        else:
            t.stall = 0
        if t.bumped and (not pi or n_new == 0) and t.pages <= 2:
            t.bumped = False  # server refused the larger page size; fall back to the page's own count
            self.tel.event("page_size_bump_reverted", platform=self.plat, target=t.slug)
        for a in anomalous:
            pause = max(pause, self.anomaly(t, a, status=st, latency_ms=round(ms or 0, 1), bytes=len(text), errors=errs[:3]))
            break  # one doubling per step, all reasons logged in the event
        if anomalous and len(anomalous) > 1:
            self.tel.event("anomaly_reasons", platform=self.plat, target=t.slug, reasons=anomalous)
        if not anomalous:
            self.thr.ok()
            self.tel.set_state(delay_s=self.thr.delay, backoff_level=self.thr.level, session="ok")
        if pi and pi.get("end_cursor") and pi["end_cursor"] != t.cursor:
            t.cursor = pi["end_cursor"]
        if t.mode == "head":
            return self.head_page(t, st, text, errs, n_new, page_max, pi, pause)
        t.save()
        print(f"[{self.plat}/{t.slug}] page {t.pages} st={st} {round(ms or 0)}ms new={n_new} total={t.posts} earliest={t.earliest} "
              f"delay={self.thr.delay:.0f}s lvl={self.thr.level}" + (f" ANOM={anomalous}" if anomalous else ""), flush=True)
        if page_max and page_max < CUTOFF:
            return self.finish(t, "reached_before_2023-01")
        if pi and pi.get("has_next_page") is False:
            return self.finish(t, "timeline_end")
        if t.stall >= 10:
            return self.finish(t, "stalled_10_pages_without_new_posts")
        self.next_at = time.time() + self.thr.delay * random.uniform(0.85, 1.25) + pause

    def head_page(self, t, st, text, errs, n_new, page_max, pi, pause):
        h = t.head
        h["added"] += n_new
        why = None
        if st != 200 or errs or len(text) < 200:
            h["errors"] = h.get("errors", 0) + 1  # retry the same cursor after backoff
            why = "head_errors" if h["errors"] >= 10 else None
        else:
            h["pages"] += 1
            floor = datetime.fromisoformat(h["floor"]).timestamp()
            if not page_max or page_max <= floor:
                why = "reached_previous_latest"
            elif n_new == 0:
                why = "only_known_posts"
            elif pi and pi.get("has_next_page") is False:
                why = "timeline_end"
            elif h["pages"] >= HEAD_MAX_PAGES:
                why = "head_page_cap"
        t.save()
        print(f"[{self.plat}/{t.slug}] head page {h['pages']} st={st} new={n_new} added={h['added']} "
              f"delay={self.thr.delay:.0f}s lvl={self.thr.level}" + (f" END={why}" if why else ""), flush=True)
        if why:
            return self.end_head(t, why)
        self.next_at = time.time() + self.thr.delay * random.uniform(0.85, 1.25) + pause

    def scroll_step(self, t):
        before = t.posts
        try:
            objs = [o for s in t.page.evaluate(C.INLINE_JSON) for o in C.parse_json_multi(s)]
            self.ingest(t, objs)
        except Exception:
            pass
        t.page.mouse.wheel(0, 3000)
        t.page.wait_for_timeout(3500)
        t.pages += 1
        if t.posts == before and not net_up():
            self.net.handle("network probe failed during scroll", t.slug)
            self.next_at = time.time() + self.thr.delay
            return
        if t.posts == before:
            t.stall += 1
            if self.anomaly(t, "no_new_posts_scroll"):
                pass
        else:
            t.stall = 0
            self.thr.ok()
        t.save()
        if t.earliest and datetime.fromisoformat(t.earliest).timestamp() < CUTOFF:
            return self.finish(t, "reached_before_2023-01_scroll")
        if t.stall >= 30:
            return self.finish(t, "scroll_stalled")
        self.next_at = time.time() + self.thr.delay

    def write_progress(self):
        PROG[self.plat] = {"stopped": self.stopped, "delay_s": self.thr.delay, "backoff_level": self.thr.level,
                           "current": self.cur.slug if self.cur else None,
                           "targets": {t.slug: {"posts": t.posts, "dropped": t.dropped, "earliest": t.earliest, "latest": t.latest,
                                                "pages": t.pages, "done": t.done, "reason": t.reason,
                                                "head": t.head, "head_last": t.head_last}
                                       for t in ([self.cur] if self.cur else []) + self.queue + DONE[self.plat]}}
        (DEEP / "progress.json").write_text(json.dumps({"updated": now_iso(), **PROG}, ensure_ascii=False, indent=1), encoding="utf-8")


PROG, DONE = {}, {"facebook": [], "instagram": [], "threads": []}


def main():
    C.set_devtools_network_pref()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(C.PROFILE), headless=False, locale="zh-TW", timezone_id="Asia/Taipei",
                                                   no_viewport=True, args=["--auto-open-devtools-for-tabs", "--start-maximized"])
        plats = {k: Platform(k, ctx) for k in TARGETS if not ONLY or k in ONLY}
        for pl in plats.values():
            st = pl.check_cookie()
            pl.tel.event("session_check_start", platform=pl.plat, state=st, cookie_hash=pl.cookie_hash)
            if st == "missing":
                pl.stopped = "not_logged_in_at_start"
                pl.tel.event("PLATFORM_STOPPED", platform=pl.plat, reason=pl.stopped)
        keep = ctx.new_page()  # idle pump page so events keep flowing while every platform sleeps
        while True:
            active = [pl for pl in plats.values() if not pl.stopped]
            if not active:
                break
            pl = min(active, key=lambda x: x.next_at)
            while time.time() < pl.next_at:
                keep.wait_for_timeout(min(30000, max(50, (pl.next_at - time.time()) * 1000)))
            prev = pl.cur
            try:
                pl.step()
            except Exception as e:
                pl.tel.event("step_error", platform=pl.plat, target=pl.cur.slug if pl.cur else None, error=repr(e)[:300],
                             tb=traceback.format_exc()[-600:])
                if not pl.net.handle(e, pl.cur.slug if pl.cur else None) and pl.cur:
                    pl.anomaly(pl.cur, "exception")
                pl.next_at = time.time() + pl.thr.delay
            pl.write_progress()
        for pl in plats.values():
            pl.tel.event("platform_end", platform=pl.plat, reason=pl.stopped)
            pl.tel.close()
        ctx.close()
    print("ALL_DONE", flush=True)


if __name__ == "__main__":
    main()
