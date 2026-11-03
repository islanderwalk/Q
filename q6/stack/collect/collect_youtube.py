# -*- coding: utf-8 -*-
"""Q6 YouTube 渠道公開頁樣本（未登入、公開 RSS）。

1. 開台灣新聞頻道的公開 @handle 頁，從 HTML 讀 channel_id（"externalId" 或 canonical /channel/UC…）。
2. 讀公開 RSS：https://www.youtube.com/feeds/videos.xml?channel_id=<id>（每頻道最新 15 支）。
text＝影片標題＋"\\n"＋media:description（原樣，含網址、hashtag、訂閱連結）。

用法：python3 collect_youtube.py [--used-before N] [--cache DIR]
"""
import argparse
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import common as C

HANDLES = ["TVBSNEWS01", "PNNPTS", "newsebc"]  # TVBS NEWS、公視新聞網 PNN、東森新聞 CH51
WANT = 3
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}
ID_RES = [re.compile(r'"externalId":"(UC[\w-]{22})"'),
          re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'),
          re.compile(r'"channelId":"(UC[\w-]{22})"')]


def channel_id(html):
    for rx in ID_RES:
        m = rx.search(html or "")
        if m:
            return m.group(1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--used-before", type=int, default=0, help="這次之前已對 www.youtube.com 載入的次數")
    ap.add_argument("--cache", default=None, help="開發用原始回應暫存目錄（請放 repo 外）")
    args = ap.parse_args()
    f = C.Fetcher("youtube", already_used={"www.youtube.com": args.used_before}, cache_dir=args.cache)
    rows, channels, failures = [], [], []
    for h in HANDLES:
        if len(channels) >= WANT:
            break
        url = f"https://www.youtube.com/@{h}"
        status, html, _ = f.get(url)
        cid = channel_id(html) if status == 200 else None
        if not cid:
            failures.append({"url": url, "status": status, "note": "no channel_id" if status == 200 else None})
            continue
        channels.append({"handle": h, "handle_url": url, "channel_id": cid})

    for ch in channels:
        feed = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"
        ch["feed"] = feed
        status, xml, fetched_at = f.get(feed)
        ch["feed_status"] = status
        if status != 200 or not xml:
            failures.append({"url": feed, "status": status})
            continue
        root = ET.fromstring(xml.encode("utf-8"))
        ch["name"] = root.findtext("a:title", default="", namespaces=NS)
        n = 0
        for e in root.findall("a:entry", NS):
            vid = e.findtext("yt:videoId", default="", namespaces=NS)
            title = e.findtext("a:title", default="", namespaces=NS)
            desc = e.findtext("media:group/media:description", default="", namespaces=NS) or ""
            pub = e.findtext("a:published", default=None, namespaces=NS)
            rows.append(C.record(
                post_id=vid, channel="youtube", sub_type="video", source=ch["name"] or f"@{ch['handle']}",
                url=f"https://www.youtube.com/watch?v={vid}",
                published_at=C.to_tw_iso(datetime.fromisoformat(pub)) if pub else None,
                fetched_at=fetched_at, text=(title + "\n" + desc) if desc else title, collect_method="rss"))
            n += 1
        ch["entries"] = n

    C.write_jsonl("youtube", rows)
    (C.META_DIR / "youtube_channels.json").write_text(json.dumps(
        {"channels": channels, "failures": failures, "network_loads_this_run": f.network, "cache_hits": f.cache_hits,
         "note": "整體載入次數以 _meta/fetch_log.jsonl＋manual_probes.json 為準"}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("loads:", f.count, "failures:", failures)


if __name__ == "__main__":
    main()
