# -*- coding: utf-8 -*-
"""Q6 媒體渠道公開頁樣本（台灣新聞網站公開 RSS）。

text＝標題＋"\\n"＋description；只對 description 去 HTML 標籤（並把 HTML 實體還原成字），其餘原樣。
每個 feed 最多取 PER_FEED 則，總數不超過 TOTAL_MAX。

用法：python3 collect_media.py [--cache DIR]
"""
import argparse
import html as htmllib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import common as C

FEEDS = [  # (source 代號, feed 網址)
    ("ltn/all", "https://news.ltn.com.tw/rss/all.xml"),
    ("cna/politics", "https://feeds.feedburner.com/rsscna/politics"),
    ("cna/social", "https://feeds.feedburner.com/rsscna/social"),
    ("cna/local", "https://feeds.feedburner.com/rsscna/local"),
    ("pts/newsfeed", "https://news.pts.org.tw/xml/newsfeed.xml"),
]
PER_FEED = 16
TOTAL_MAX = 80
TAG_RE = re.compile(r"<[^>]+>")
ATOM = "{http://www.w3.org/2005/Atom}"


def strip_html(s):
    return htmllib.unescape(TAG_RE.sub("", s or ""))


def parse_date(s):
    if not s:
        return None
    try:
        return C.to_tw_iso(parsedate_to_datetime(s.strip()))
    except (TypeError, ValueError):
        return None


def iso_or_rfc822(s):
    """RSS 的 pubDate 是 RFC 822；Atom 的 published/updated 是 ISO 8601。"""
    if not s:
        return None
    s = s.strip()
    try:
        return C.to_tw_iso(datetime.fromisoformat(s))
    except ValueError:
        return parse_date(s)


def items_of(root):
    """RSS 2.0（channel/item）與 Atom（feed/entry，例如公視）都轉成 (title, desc, link, guid, date) 。"""
    if root.tag == ATOM + "feed":
        for e in root.findall(ATOM + "entry"):
            link = e.find(ATOM + "link")
            yield (e.findtext(ATOM + "title") or "", e.findtext(ATOM + "summary") or e.findtext(ATOM + "content") or "",
                   link.get("href") if link is not None else "", e.findtext(ATOM + "id") or "",
                   e.findtext(ATOM + "published") or e.findtext(ATOM + "updated"))
        return
    for it in root.findall("./channel/item"):
        yield (it.findtext("title") or "", it.findtext("description") or "", it.findtext("link") or "",
               it.findtext("guid") or "", it.findtext("pubDate"))


def feed_title(root):
    return root.findtext(ATOM + "title") if root.tag == ATOM + "feed" else root.findtext("./channel/title")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None, help="開發用原始回應暫存目錄（請放 repo 外）")
    args = ap.parse_args()
    f = C.Fetcher("media", cache_dir=args.cache)
    rows, feeds, failures = [], [], []
    for key, url in FEEDS:
        status, xml, fetched_at = f.get(url)
        info = {"source": key, "url": url, "status": status}
        feeds.append(info)
        if status != 200 or not xml:
            failures.append({"url": url, "status": status})
            continue
        try:
            root = ET.fromstring(xml.encode("utf-8"))
        except ET.ParseError as e:
            failures.append({"url": url, "status": status, "note": f"XML parse error: {e}"})
            continue
        items = list(items_of(root))
        info["items_in_feed"] = len(items)
        info["feed_title"] = (feed_title(root) or "").strip()
        n = 0
        for title, desc, link, guid, date in items:
            if n >= PER_FEED or len(rows) >= TOTAL_MAX:
                break
            title, link = title.strip(), link.strip()
            desc = strip_html(desc).strip("\n ")
            guid = guid.strip() or link
            if not title:
                continue
            rows.append(C.record(
                post_id=guid, channel="media", sub_type="news", source=key, url=link,
                published_at=iso_or_rfc822(date), fetched_at=fetched_at,
                text=(title + "\n" + desc) if desc.strip() else title, collect_method="rss"))
            n += 1
        info["taken"] = n

    C.write_jsonl("media", rows)
    (C.META_DIR / "media_feeds.json").write_text(json.dumps(
        {"feeds": feeds, "failures": failures, "network_loads_this_run": f.network, "cache_hits": f.cache_hits,
         "note": "整體載入次數以 _meta/fetch_log.jsonl＋manual_probes.json 為準"}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("loads:", f.count, "failures:", failures)


if __name__ == "__main__":
    main()
