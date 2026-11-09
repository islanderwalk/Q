# YouTube Data API v3 history crawl: head increment per channel (uploads newer than the last run),
# then the backfill since 2023-01 per channel, then a comment sample.
# Key comes from env YOUTUBE_API_KEY (or a file named by YOUTUBE_API_KEY_FILE) and only ever travels as the `key` query value;
# telemetry strips every query value, and nothing here prints it. Quota: stop uploads at 8,000 units, comments up to 9,000 in total.
# The daily 10,000 is shared with the Q6 live collector on the same key (3 feeds every 5 min = 864 units/day), so 1,000 stays free for it.
import json, re, sys, time, random, pathlib, collections, traceback
from datetime import datetime
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # telem.py sits next to this file
from telem import Telemetry, Throttle, NetGuard, TW, h10, clean_url, now_iso  # noqa: E402

import os  # noqa: E402
D = pathlib.Path(os.environ.get("CRAWL_WORKDIR") or pathlib.Path(__file__).parent / "work") / "youtube"
(D / "out").mkdir(parents=True, exist_ok=True)
KEY = (os.environ.get("YOUTUBE_API_KEY") or pathlib.Path(os.environ["YOUTUBE_API_KEY_FILE"]).read_text(encoding="utf-8")).strip()
API = "https://www.googleapis.com/youtube/v3/"
CUTOFF = "2023-01-01T00:00:00+08:00"
UPLOAD_BUDGET = int(os.environ.get("YOUTUBE_UPLOAD_BUDGET", "8000"))
TOTAL_BUDGET = int(os.environ.get("YOUTUBE_TOTAL_BUDGET", "9000"))   # of the 10,000/day project quota; the rest is the collector's
# repo q6/stack youtube_channels.json (resolved ids) + the brief's extra channels (handle candidates, first hit wins)
CHANNELS = [("TVBS NEWS", ["UC5nwNW4KdC0SzrhF9BXEYOQ"]), ("公視新聞網", ["UCexpzYDEnfmAvPSfG4xbcjA"]),
            ("東森新聞 CH51", ["UCR3asjvr_WAaxwJYEDV_Bfw"]),
            ("中央社", ["@CNAVideo", "@cnanews", "@CNA_TW"]), ("民視新聞", ["@FTV_News", "@ftvnews53", "@FTVNEWS"]),
            ("高雄市政府", ["@KCGtube", "@kaohsiungcity", "@kcg"]), ("高雄廣播電台/在地", ["@kaohsiungtv", "@KTV_kaohsiung"])]
# A handle hit only counts if the channel title carries this word: @kaohsiungcity belongs to an unrelated personal
# channel ("Ping Yu Liu") with no uploads playlist. No title match among the handles -> one search.list (100 units).
TITLE_MUST = {"中央社": "中央社", "民視新聞": "民視", "高雄市政府": "高雄", "高雄廣播電台/在地": "高雄"}
S = requests.Session()
tel = Telemetry("youtube_api", D / "telemetry", "youtube")
thr = Throttle(base=1.0, cap=600.0, pause=1800.0)
tel.set_state(delay_s=thr.delay, backoff_level=0, session="ok")
net = NetGuard(tel, "youtube")
quota = collections.Counter()
STATE = D / "state.json"
st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"channels": {}, "comments_done": []}
# Quota resets at midnight Pacific; carry today's spend across restarts and give each channel an equal daily share,
# so one high-volume channel (TVBS ~180 uploads/day) cannot starve the rest. Unfinished channels resume from page_token.
from datetime import timezone, timedelta  # noqa: E402
QDAY = datetime.now(timezone(timedelta(hours=-8))).date().isoformat()
if st.get("quota_day") != QDAY:
    st.update(quota_day=QDAY, quota_prev=0)
    for _c in st["channels"].values():
        _c["units_today"] = 0
else:
    st["quota_prev"] = max(st.get("quota_prev", 0), st.get("quota_used", 0))
CH_CAP = UPLOAD_BUDGET // len(CHANNELS)


def save():
    STATE.write_text(json.dumps({**st, "quota_used": sum(quota.values()) + st.get("quota_prev", 0), "updated": now_iso()},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    (D / "progress.json").write_text(json.dumps({"updated": now_iso(), "quota_by_endpoint": dict(quota),
                                                 "quota_total": sum(quota.values()) + st.get("quota_prev", 0),
                                                 "channels": {k: {x: v.get(x) for x in ("videos", "earliest", "latest", "done", "comments",
                                                                                        "head", "head_last")}
                                                              for k, v in st["channels"].items()}}, ensure_ascii=False, indent=1), encoding="utf-8")


def used():
    return sum(quota.values()) + st.get("quota_prev", 0)


# Per-item states, not server trouble: backing off on them only slows the crawl (seen 2026-11-03: three videos with
# comments turned off pushed the crawler into a 30 min pause).
CONTENT_STATE = {"commentsDisabled", "videoNotFound", "playlistNotFound", "channelNotFound"}


def call(ep, target, **params):
    while True:
        thr.wait()
        t0 = time.time()
        code, data, err, exc = None, {}, None, None
        try:
            r = S.get(API + ep, params={**params, "key": KEY}, timeout=30)
            code = r.status_code
            data = r.json()
        except Exception as e:
            err, exc = type(e).__name__, e
        ms = round((time.time() - t0) * 1000, 1)
        u, qn = clean_url(API + ep)
        reason = ((data.get("error") or {}).get("errors") or [{}])[0].get("reason") if isinstance(data, dict) else None
        tel.trace({"platform": "youtube", "target": target, "method": "GET", "url": u, "query_names": sorted(set(params) | {"key"}),
                   "resource_type": "api", "status": code, "latency_ms": ms, "bytes": len(json.dumps(data)) if data else 0,
                   "endpoint": ep, "quota_units_total": used(), "error_reason": reason, "failure": err}, api=True)
        if code is None and net.handle(exc, target):
            continue  # local disconnect: the request never reached the API, so no quota, no backoff; retry
        if code is not None:
            net.ok()
        quota[ep] += 1
        break
    if reason in CONTENT_STATE:  # the API answered fine; this video/playlist just has no data to give
        thr.ok()
        tel.set_state(delay_s=thr.delay, backoff_level=thr.level)
        tel.event("content_unavailable", platform="youtube", target=target, endpoint=ep, status=code, reason=reason)
        return code, data
    bad = code != 200 or tel.latency_anomalous(ms)  # latency rule only reached for 200 responses
    if bad:
        pause = thr.anomaly()
        tel.set_state(delay_s=thr.delay, backoff_level=thr.level)
        tel.event("anomaly", platform="youtube", target=target, endpoint=ep, status=code, reason=reason, latency_ms=ms,
                  new_delay_s=thr.delay, pause_s=pause)
        if reason in ("quotaExceeded", "dailyLimitExceeded", "keyInvalid", "accessNotConfigured", "forbidden"):
            raise SystemExit(f"stop:{reason}")
        if pause:
            time.sleep(pause)
    else:
        thr.ok()
        tel.set_state(delay_s=thr.delay, backoff_level=thr.level)
    return code, data


def resolve(name, cands):
    must = TITLE_MUST.get(name)
    for c in cands:
        if c.startswith("UC"):
            code, d = call("channels", name, part="snippet,contentDetails,statistics", id=c)
        else:
            code, d = call("channels", name, part="snippet,contentDetails,statistics", forHandle=c)
        items = d.get("items") or []
        title = items[0]["snippet"]["title"] if items else None
        ok = bool(items) and (c.startswith("UC") or not must or must in title)
        tel.event("channel_resolve", platform="youtube", target=name, candidate=c, found=bool(items), title=title, accepted=ok)
        if ok:
            return channel_info(items[0])
    if must:
        code, d = call("search", name, part="snippet", type="channel", q=name, maxResults=5)
        quota["search"] += 99  # search.list costs 100 units; call() counted 1
        ids = [it["snippet"]["channelId"] for it in d.get("items", []) if must in it["snippet"].get("title", "")]
        tel.event("channel_search", platform="youtube", target=name, hits=[it["snippet"].get("title") for it in d.get("items", [])],
                  matched=ids[:3])
        if ids:
            code, d = call("channels", name, part="snippet,contentDetails,statistics", id=ids[0])
            if d.get("items"):
                return channel_info(d["items"][0])
    return None


def channel_info(it):
    return {"channel_id": it["id"], "title": it["snippet"]["title"], "handle": it["snippet"].get("customUrl"),
            "uploads": (it.get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads"), "stats": it.get("statistics", {})}


def dead_playlist(c, name, code, d):
    # 404 playlistNotFound never heals by retrying: skip the channel instead of looping on it until the daily cap.
    reason = ((d.get("error") or {}).get("errors") or [{}])[0].get("reason") if isinstance(d, dict) else None
    if code == 404 or reason == "playlistNotFound":
        c.update(skipped=True, done=True, note=f"uploads playlist {c.get('uploads')} -> {code} {reason}")
        tel.event("channel_skipped", platform="youtube", target=name, status=code, reason=reason, uploads=c.get("uploads"))
        save()
        return True
    return False


def load_seen():
    f = D / "out" / "videos.jsonl"
    return {json.loads(l)["video_id"] for l in open(f, encoding="utf-8") if l.strip()} if f.exists() else set()


SEEN = load_seen()


def pub_of(it):
    pub = it["contentDetails"].get("videoPublishedAt") or it["snippet"].get("publishedAt")
    return datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(TW).isoformat()


def emit(out_v, name, c, it, pub_tw):
    sn, vid = it["snippet"], it["contentDetails"]["videoId"]
    out_v.write(json.dumps({"post_id": f"yt-{vid}", "channel": "youtube", "sub_type": "video",
                            "source": name, "channel_id": c["channel_id"], "video_id": vid,
                            "url": f"https://www.youtube.com/watch?v={vid}",
                            "title": sn.get("title"), "text": sn.get("description") or "", "published_at": pub_tw,
                            "fetched_at": now_iso(), "collect_method": "youtube_data_api_v3_playlistItems"},
                           ensure_ascii=False) + "\n")
    SEEN.add(vid)
    c["videos"] += 1
    c["earliest"] = min(filter(None, [c.get("earliest"), pub_tw]))
    c["latest"] = max(filter(None, [c.get("latest"), pub_tw]))
    tel.add_posts("youtube", name, 1)


def head(out_v):
    # Head increment for every resolved channel, finished ones included: the uploads playlist is newest-first, so page
    # from the top until a known video_id or anything at/below the previous latest. Same quota rules as the backfill.
    for name, cands in CHANNELS:
        c = st["channels"].get(name)
        if not c or c.get("skipped") or "uploads" not in c or not c.get("latest"):
            continue
        h = c.setdefault("head", {"floor": c["latest"], "token": None, "added": 0, "pages": 0, "started": now_iso()})
        while used() < UPLOAD_BUDGET and c.get("units_today", 0) < CH_CAP:
            code, d = call("playlistItems", name, part="snippet,contentDetails", playlistId=c["uploads"], maxResults=50,
                           **({"pageToken": h["token"]} if h["token"] else {}))
            c["units_today"] = c.get("units_today", 0) + 1
            if code != 200:
                if dead_playlist(c, name, code, d):
                    break
                continue
            reached = False
            for it in d.get("items", []):
                pub_tw = pub_of(it)
                if it["contentDetails"]["videoId"] in SEEN or pub_tw <= h["floor"] or pub_tw < CUTOFF:
                    reached = True
                    continue
                emit(out_v, name, c, it, pub_tw)
                h["added"] += 1
            out_v.flush()
            h["pages"] += 1
            h["token"] = d.get("nextPageToken")
            if reached or not h["token"]:
                c["head_last"] = {"added": h["added"], "pages": h["pages"], "floor": h["floor"], "finished": now_iso()}
                del c["head"]
                tel.event("head_done", platform="youtube", target=name, new_videos=h["added"], pages=h["pages"], floor=h["floor"])
                save()
                break
            save()
        if c.get("head") and not c.get("skipped"):
            tel.event("head_daily_cap", platform="youtube", target=name, added=h["added"], cap=CH_CAP)
        if used() >= UPLOAD_BUDGET:
            break
    tel.event("head_all_done", platform="youtube", channels={k: v.get("head_last") for k, v in st["channels"].items()})


def main():
    out_v = open(D / "out" / "videos.jsonl", "a", encoding="utf-8")
    head(out_v)
    for name, cands in CHANNELS:
        c = st["channels"].setdefault(name, {"videos": 0, "done": False})
        if c.get("done") or c.get("skipped"):
            continue
        if "uploads" not in c:
            info = resolve(name, cands)
            if not info or not info.get("uploads"):
                c.update(skipped=True, done=True, note=f"no channel for {cands}")
                save()
                continue
            c.update(info)
        while used() < UPLOAD_BUDGET and c.get("units_today", 0) < CH_CAP:
            code, d = call("playlistItems", name, part="snippet,contentDetails", playlistId=c["uploads"], maxResults=50,
                           **({"pageToken": c["page_token"]} if c.get("page_token") else {}))
            c["units_today"] = c.get("units_today", 0) + 1
            if code != 200:
                if dead_playlist(c, name, code, d):
                    break
                continue
            old = 0
            for it in d.get("items", []):
                pub_tw = pub_of(it)
                if pub_tw < CUTOFF:
                    old += 1
                    continue
                if it["contentDetails"]["videoId"] in SEEN:
                    continue
                emit(out_v, name, c, it, pub_tw)
            out_v.flush()
            c["page_token"] = d.get("nextPageToken")
            save()
            if not c["page_token"] or (d.get("items") and old == len(d["items"])):
                c["done"] = True
                if not old and (c.get("earliest") or "9") > CUTOFF[:7]:
                    c["note"] = "uploads playlist ended before 2023-01 (API exposes only the most recent uploads)"
                tel.event("channel_done", platform="youtube", target=name, videos=c["videos"], earliest=c.get("earliest"))
                break
        if not c.get("done"):
            tel.event("channel_daily_cap", platform="youtube", target=name, videos=c["videos"], earliest=c.get("earliest"), cap=CH_CAP)
        save()
        if used() >= UPLOAD_BUDGET:
            tel.event("upload_budget_reached", platform="youtube", quota=used())
            break
    out_v.close()
    comments()


def comments():
    # 3 videos per channel per month (first, middle, last upload of the month), first 100 top-level threads each.
    vids = collections.defaultdict(list)
    for l in open(D / "out" / "videos.jsonl", encoding="utf-8"):
        v = json.loads(l)
        vids[(v["source"], v["published_at"][:7])].append(v)
    picks = []
    for k, lst in sorted(vids.items()):
        lst.sort(key=lambda v: v["published_at"])
        picks += [lst[i] for i in sorted({0, len(lst) // 2, len(lst) - 1})]
    done = set(st["comments_done"])
    with open(D / "out" / "comments.jsonl", "a", encoding="utf-8") as fo:
        for v in picks:
            if v["video_id"] in done:
                continue
            if used() >= TOTAL_BUDGET:
                tel.event("total_budget_reached", platform="youtube", quota=used(), comment_videos=len(done))
                break
            code, d = call("commentThreads", v["source"], part="snippet", videoId=v["video_id"], maxResults=100, order="relevance",
                           textFormat="plainText")
            reason = ((d.get("error") or {}).get("errors") or [{}])[0].get("reason")
            for it in d.get("items", []):
                s = it["snippet"]["topLevelComment"]["snippet"]
                fo.write(json.dumps({"video_id": v["video_id"], "source": v["source"], "comment_id": it["id"],
                                     "author": s.get("authorDisplayName"), "text": s.get("textDisplay"), "like_count": s.get("likeCount"),
                                     "reply_count": it["snippet"].get("totalReplyCount"), "published_at": s.get("publishedAt"),
                                     "fetched_at": now_iso()}, ensure_ascii=False) + "\n")
            st["channels"].setdefault(v["source"], {})["comments"] = st["channels"][v["source"]].get("comments", 0) + len(d.get("items", []))
            done.add(v["video_id"])
            st["comments_done"] = sorted(done)
            if reason:
                tel.event("comments_unavailable", platform="youtube", target=v["source"], video_id=v["video_id"], reason=reason)
            save()


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        tel.event("stopped", platform="youtube", reason=str(e))
    except Exception as e:
        tel.event("crash", platform="youtube", error=repr(e)[:300], tb=traceback.format_exc()[-800:])
        raise
    finally:
        st["quota_prev"] = used()
        save()
        tel.close()
    print("YT_DONE", flush=True)
