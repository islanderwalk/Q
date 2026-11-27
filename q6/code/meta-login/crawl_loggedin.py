# Logged-in read-only crawl of the SAME targets as the logged-out collectors, using the operator's own
# session saved in ./profile by login_meta.py. No likes/comments/follows; >=5s between page loads.
# Evidence: Playwright page screenshot + OS-level window screenshot with DevTools (F12) Network panel open,
# masked session evidence JSON (cookie names/flags/expiry/sha256 prefix only), API request ledger
# (endpoint, friendly_name, doc_id, status, response JSON key paths - no request bodies, no token values).
import json, time, hashlib, re, sys, ctypes, ctypes.wintypes as wt, pathlib
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
from PIL import ImageGrab

import os  # noqa: E402
D = pathlib.Path(os.environ.get("META_WORKDIR") or pathlib.Path(__file__).parent)  # outputs + profile live here
PROFILE, EV, OUT = D / "profile", D / "evidence", D / "loggedin"
EV.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)
TW = timezone(timedelta(hours=8))
SCROLLS = int(sys.argv[1]) if len(sys.argv) > 1 else 25
ONLY = sys.argv[2].split(",") if len(sys.argv) > 2 else None
TARGETS = ([("facebook", s, f"https://www.facebook.com/{s}") for s in ["cnanewstaiwan", "PNNPTS", "ftvnews53", "NASA"]]
           + [("instagram", f"{u}{'_reels' if p else ''}", f"https://www.instagram.com/{u}/{p}")
              for u in ["cnanews_tw", "pts.news", "ftvnews", "nasa"] for p in ("", "reels/")]
           + [("threads", u, f"https://www.threads.com/@{u}") for u in ["cnanews_tw", "pts.news", "ftvnews"]])
if ONLY:
    TARGETS = [t for t in TARGETS if t[0] in ONLY]
SESSION_COOKIES = {"facebook": ("facebook.com", ["c_user", "xs", "fr", "datr", "sb"]),
                   "instagram": ("instagram.com", ["sessionid", "ds_user_id", "csrftoken", "mid", "ig_did", "rur"]),
                   "threads": ("threads.", ["sessionid", "ds_user_id", "csrftoken", "mid", "ig_did"])}
API_RE = re.compile(r"/api/graphql|/graphql/query|/api/v1/|/ajax/bulk-route|/ajax/navigation")


def now():
    return datetime.now(TW).isoformat(timespec="seconds")


def iso(ts):
    try:
        return datetime.fromtimestamp(int(ts), TW).isoformat(timespec="seconds") if ts else None
    except Exception:
        return None


def h(v):
    return "sha256:" + hashlib.sha256(v.encode()).hexdigest()[:10]


def key_paths(o, pre="", out=None, depth=0):
    out = set() if out is None else out
    if depth > 9 or len(out) > 400:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{pre}.{k}" if pre else k
            out.add(p)
            key_paths(v, p, out, depth + 1)
    elif isinstance(o, list) and o:
        key_paths(o[0], pre + "[]", out, depth + 1)
    return out


def parse_json_multi(txt):
    txt = (txt or "").lstrip()
    if txt.startswith("for (;;);"):
        txt = txt[9:]
    objs = []
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            objs.append(json.loads(line))
        except Exception:
            pass
    if not objs:
        try:
            objs = [json.loads(txt)]
        except Exception:
            pass
    return objs


def walk(o):
    st = [o]
    while st:
        n = st.pop()
        if isinstance(n, dict):
            yield n
            st.extend(n.values())
        elif isinstance(n, list):
            st.extend(n)


def fb_walk(o, ctx, rows):
    """FB: timestamps/ids sit on ancestors of message.text, so carry them down like the logged-out WALK_FB."""
    st = [(o, ctx)]
    while st:
        n, c = st.pop()
        if isinstance(n, list):
            st.extend((x, c) for x in n)
            continue
        if not isinstance(n, dict):
            continue
        c = dict(c)
        if isinstance(n.get("creation_time"), int):
            c["t"] = n["creation_time"]
        if isinstance(n.get("post_id"), str):
            c["pid"] = n["post_id"]
        u = n.get("permalink_url") or n.get("url")
        if isinstance(u, str) and re.search(r"facebook\.com/.+/(posts|videos|reel|photos)/", u):
            c["url"] = u
        m = n.get("message")
        if isinstance(m, dict) and isinstance(m.get("text"), str) and len(m["text"].strip()) > 4:
            k = m["text"][:80]
            r = rows.setdefault(k, {"text": m["text"], "t": None, "pid": None, "url": None})
            r["t"] = r["t"] or c.get("t"); r["pid"] = r["pid"] or c.get("pid"); r["url"] = r["url"] or c.get("url")
        for v in n.values():
            st.append((v, c))


def extract(platform, objs):
    rows = {}
    for o in objs:
        if platform == "facebook":
            fb_walk(o, {}, rows)
            continue
        for n in walk(o):
            cap = n.get("caption")
            if "code" in n and "taken_at" in n and isinstance(cap, dict) and isinstance(cap.get("text"), str):
                info = n.get("text_post_app_info") if isinstance(n.get("text_post_app_info"), dict) else {}
                user = n.get("user") if isinstance(n.get("user"), dict) else {}
                rows[n["code"]] = {"text": cap["text"], "t": n.get("taken_at"), "pid": n["code"], "username": user.get("username"),
                                   "product_type": n.get("product_type"), "like_count": n.get("like_count"),
                                   "comment_count": n.get("comment_count"), "play_count": n.get("play_count"),
                                   "is_reply": bool(info.get("is_reply")), "direct_reply_count": info.get("direct_reply_count")}
    return rows


INLINE_JSON = """() => Array.from(document.querySelectorAll('script[type="application/json"]')).map(s => s.textContent)"""

# ---- OS window capture (keeps DevTools in frame) ----
user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def find_window(title_part):
    hits = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title_part in buf.value:
                hits.append(hwnd)
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    return hits[0] if hits else None


def grab_window(page, path):
    marker = f"QSEV{int(time.time() * 1000)}"
    page.evaluate("m => { window.__t = document.title; document.title = m + ' ' + document.title; }", marker)
    time.sleep(1.0)
    hwnd = find_window(marker)
    ok = False
    if hwnd:
        user32.ShowWindow(hwnd, 3)
        user32.SetForegroundWindow(hwnd)
        time.sleep(1.5)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(path)
        ok = True
    page.evaluate("() => { if (window.__t !== undefined) document.title = window.__t; }")
    return ok


def set_devtools_network_pref():
    pf = PROFILE / "Default" / "Preferences"
    try:
        j = json.loads(pf.read_text(encoding="utf-8"))
        dp = j.setdefault("devtools", {}).setdefault("preferences", {})
        dp["panel-selected-tab"] = '"network"'
        dp["panel-selectedTab"] = '"network"'
        dp["currentDockState"] = '"bottom"'
        pf.write_text(json.dumps(j), encoding="utf-8")
    except Exception as e:
        print("pref", e)


def main():
    set_devtools_network_pref()
    summary, ledger = [], []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, locale="zh-TW", timezone_id="Asia/Taipei",
                                                   no_viewport=True, args=["--auto-open-devtools-for-tabs", "--start-maximized"])
        sess = {}
        for plat, (dom, names) in SESSION_COOKIES.items():
            cs = [c for c in ctx.cookies() if dom in c["domain"]]
            sess[plat] = {"logged_in_cookie_present": any(c["name"] == names[0] and c["value"] for c in cs),
                          "cookies": [{"name": c["name"], "domain": c["domain"], "path": c["path"], "httpOnly": c["httpOnly"],
                                       "secure": c["secure"], "sameSite": c.get("sameSite"),
                                       "expires": iso(c["expires"]) if c["expires"] and c["expires"] > 0 else "session",
                                       "value": h(c["value"]) + f" (len {len(c['value'])})"} for c in cs]}
        (EV / "session_masked.json").write_text(json.dumps(
            {"captured_at": now(), "note": "cookie/token values masked to sha256 prefix + length; raw values never written",
             "platforms": sess}, ensure_ascii=False, indent=1), encoding="utf-8")
        last = 0.0
        for plat, slug, url in TARGETS:
            page = ctx.new_page()
            api_objs, reqs = [], []

            def on_resp(resp, reqs=reqs, api_objs=api_objs):
                u = resp.url
                if not API_RE.search(u):
                    return
                rq = resp.request
                try:
                    body = rq.post_data or ""
                except Exception:
                    body = ""
                m1 = re.search(r"fb_api_req_friendly_name=([^&]+)", body)
                m2 = re.search(r"(?:^|&)doc_id=(\d+)", body)
                ent = {"t": now(), "method": rq.method, "endpoint": u.split("?")[0], "status": resp.status,
                       "friendly_name": rq.headers.get("x-fb-friendly-name") or (m1.group(1) if m1 else None),
                       "doc_id": m2.group(1) if m2 else None, "resp_key_paths": []}
                try:
                    objs = parse_json_multi(resp.text())
                    api_objs.extend(objs)
                    ks = set()
                    for o in objs[:3]:
                        key_paths(o, out=ks)
                    ent["resp_key_paths"] = sorted(ks)[:250]
                except Exception:
                    pass
                reqs.append(ent)
            page.on("response", on_resp)
            wait = 5.0 - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.time()
            st = None
            try:
                r = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                st = r.status if r else None
            except Exception as e:
                print("goto", url, e, flush=True)
            page.wait_for_timeout(6000)
            inline = [o for s in page.evaluate(INLINE_JSON) for o in parse_json_multi(s)]
            base = f"{plat}_{slug.replace('.', '_')}"
            page.screenshot(path=str(EV / f"{base}_top.png"))
            win_ok = grab_window(page, str(EV / f"{base}_devtools_F12.png"))
            walls = 0
            for _ in range(SCROLLS):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(3200)
                if page.locator("input[name=pass], input[name=password]").count():
                    walls += 1
                    break
            page.screenshot(path=str(EV / f"{base}_after_scroll.png"))
            grab_window(page, str(EV / f"{base}_devtools_F12_after_scroll.png"))
            rows = extract(plat, inline + api_objs)
            recs = []
            for r in rows.values():
                recs.append({"post_id": f"{plat[:2]}-{r['pid'] or abs(hash(r['text'])) % 10**12}", "channel": plat,
                             "sub_type": ("reel" if r.get("product_type") == "clips" or slug.endswith("_reels") else
                                          "comment_thread" if r.get("is_reply") else "post"),
                             "source": slug, "author": r.get("username"), "url": r.get("url"),
                             "published_at": iso(r.get("t")), "fetched_at": now(), "text": r["text"],
                             "collect_method": "loggedin_inline+graphql", "like_count": r.get("like_count"),
                             "comment_count": r.get("comment_count"), "play_count": r.get("play_count")})
            with open(OUT / f"{plat}.jsonl", "a", encoding="utf-8") as f:
                for x in recs:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
            dates = sorted(x["published_at"] for x in recs if x["published_at"])
            s = {"platform": plat, "target": slug, "url": url, "status": st, "final_url": page.url.split("?")[0],
                 "scrolls": SCROLLS, "login_wall_hits": walls, "api_responses": len(reqs), "posts": len(recs),
                 "earliest": dates[0] if dates else None, "latest": dates[-1] if dates else None,
                 "with_timestamp": len(dates), "devtools_window_shot": win_ok}
            print(json.dumps(s, ensure_ascii=False), flush=True)
            summary.append(s)
            ledger.extend({"platform": plat, "target": slug, **e} for e in reqs)
            page.close()
        ctx.close()
    (EV / "loggedin_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (EV / "api_ledger_masked.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
