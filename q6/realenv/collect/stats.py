# -*- coding: utf-8 -*-
"""統計 data/channels/*.jsonl：篇數、時間範圍、收集方式、含網址／短網址／電話樣式的篇數。

正規式與 q6/code/segmenter.py 前處理用的相同（URL_RE、SHORT_URL_RE、MOBILE_RE、PHONE_RE），
這裡只數「有幾篇含有」，不改文字。另做欄位檢查（9 個欄位、時間是 +08:00）。
輸出 data/channels/_meta/stats.json，並印出表格。
"""
import json
import re
from collections import Counter
from urllib.parse import urlsplit

import common as C

CHANNELS = ["ptt", "threads", "youtube", "media"]
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
SHORT_URL_RE = re.compile(
    r"\b(?:bit\.ly|t\.co|goo\.gl|tinyurl\.com|reurl\.cc|lihi\d?\.com|pse\.is|xurl\.es|ppt\.cc|youtu\.be)/\S*", re.I)
MOBILE_RE = re.compile(r"(?<!\d)09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?886[-\s]?\d{1,2}|0\d{1,2})[-\s]?\d{3,4}[-\s]?\d{3,4}(?!\d)")
ISO_TW = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")


def check(row):
    errs = []
    if tuple(row.keys()) != C.FIELDS:
        errs.append("fields")
    for k in C.FIELDS:
        if k == "published_at":
            continue
        if not isinstance(row.get(k), str) or not row.get(k):
            errs.append(f"empty {k}")
    if row.get("published_at") is not None and not ISO_TW.match(row["published_at"]):
        errs.append("published_at format")
    if not ISO_TW.match(row.get("fetched_at") or ""):
        errs.append("fetched_at format")
    return errs


def main():
    out = {}
    for ch in CHANNELS:
        path = C.OUT_DIR / f"{ch}.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        pubs = sorted(r["published_at"] for r in rows if r["published_at"])
        fetched = sorted(r["fetched_at"] for r in rows)
        url_rows = [r for r in rows if URL_RE.search(r["text"])]
        short_rows = [r for r in rows if SHORT_URL_RE.search(r["text"])]
        phone_rows = [r for r in rows if MOBILE_RE.search(r["text"]) or PHONE_RE.search(r["text"])]
        short_domains = Counter(m.group(0).split("/")[0].lower() for r in rows for m in SHORT_URL_RE.finditer(r["text"]))
        url_hosts = Counter((urlsplit(u if u.lower().startswith("http") else "http://" + u).hostname or "?")
                            for r in rows for u in URL_RE.findall(r["text"]))
        problems = {r["post_id"]: e for r in rows if (e := check(r))}
        out[ch] = {
            "posts": len(rows),
            "post_ids_unique": len({r["post_id"] for r in rows}),
            "by_source": dict(Counter(r["source"] for r in rows)),
            "by_sub_type": dict(Counter(r["sub_type"] for r in rows)),
            "by_collect_method": dict(Counter(r["collect_method"] for r in rows)),
            "published_at_min": pubs[0] if pubs else None,
            "published_at_max": pubs[-1] if pubs else None,
            "published_at_null": sum(1 for r in rows if not r["published_at"]),
            "fetched_at_min": fetched[0],
            "fetched_at_max": fetched[-1],
            "chars_total": sum(len(r["text"]) for r in rows),
            "posts_with_url": len(url_rows),
            "posts_with_short_url": len(short_rows),
            "short_url_domains": dict(short_domains.most_common()),
            "url_hosts_top10": dict(url_hosts.most_common(10)),
            "posts_with_phone_like": len(phone_rows),
            "phone_like_post_ids": [r["post_id"] for r in phone_rows],
            "schema_problems": problems,
        }
    (C.META_DIR / "stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{'channel':8} {'posts':>5} {'url':>4} {'short':>5} {'phone':>5}  published_at range")
    for ch, s in out.items():
        print(f"{ch:8} {s['posts']:>5} {s['posts_with_url']:>4} {s['posts_with_short_url']:>5} "
              f"{s['posts_with_phone_like']:>5}  {s['published_at_min']} ~ {s['published_at_max']}"
              + (f"  PROBLEMS {s['schema_problems']}" if s["schema_problems"] else ""))


if __name__ == "__main__":
    main()
