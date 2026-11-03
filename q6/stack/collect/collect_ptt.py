# -*- coding: utf-8 -*-
"""Q6 論壇渠道（PTT）公開頁樣本。

順序：
1. 先抓 6 張雜訊截圖背後的文章（shared/capture/capture_manifest.json 裡 id 開頭 q6-ptt-noise-；
   03 與 04 是同一篇，所以實際 5 篇）。
2. 讀 Kaohsiung、Lifeismoney、Gossiping 三個板的公開 Atom feed（https://www.ptt.cc/atom/<板>.xml）找最新文章，
   前幾篇抓整頁 HTML（collect_method=html），其餘只用 Atom 內附的摘要（collect_method=rss；PTT 的 Atom 只給前幾行）。
   原因：每站最多 30 次載入，HTML 一篇就是一次。
3. Gossiping 需要 over18=1：只放在那一次請求的 Cookie 標頭，不寫檔、不留在 session。

text＝標題＋"\\n"＋內文。內文＝#main-content 去掉 article-metaline（作者／標題／時間）與 div.push（推文列），
其餘原樣保留（含 ※ 發信站、※ 文章網址、簽名檔、網址）。PTT 帳號是公開資訊，不遮。

用法：python3 collect_ptt.py [--used-before N] [--cache DIR]
"""
import argparse
import html as htmllib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

import common as C

BASE = "https://www.ptt.cc"
MANIFEST = C.REALENV.parents[1] / "shared" / "capture" / "capture_manifest.json"
BOARDS = [("Kaohsiung", 8, None), ("Lifeismoney", 7, None), ("Gossiping", 4, 4)]  # (板, 抓 HTML 篇數, Atom 摘要上限；None＝全部)
OVER18 = {"Cookie": "over18=1"}  # 只用在 Gossiping 的請求標頭
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
ART_RE = re.compile(r"/bbs/([^/]+)/(M\.(\d+)\.A\.[0-9A-F]{3})\.html")


def noise_urls():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out = []
    for e in m:
        if not e["id"].startswith("q6-ptt-noise-"):
            continue
        u = re.search(r"https://www\.ptt\.cc/bbs/\S+?\.html", e["source"]).group(0)
        if u not in out:
            out.append(u)
    return out


def parse_article(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    main = soup.select_one("#main-content")
    if main is None:
        return None
    meta = {}
    for m in main.select("div.article-metaline, div.article-metaline-right"):
        k = m.select_one(".article-meta-tag")
        v = m.select_one(".article-meta-value")
        if k and v:
            meta[k.get_text(strip=True)] = v.get_text()
        m.decompose()
    for p in main.select("div.push"):
        p.decompose()
    body = main.get_text().strip("\n")
    title = meta.get("標題") or (soup.title.get_text().rsplit(" - ", 1)[0] if soup.title else "")
    published = None
    if meta.get("時間"):
        try:
            published = C.to_tw_iso(datetime.strptime(meta["時間"].strip(), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            published = None
    return {"title": title, "body": body, "author": meta.get("作者"), "board": meta.get("看板"),
            "published_at": published}


def html_row(url, status, text, fetched_at):
    m = ART_RE.search(url)
    board, aid, epoch = m.group(1), m.group(2), m.group(3)
    a = parse_article(text)
    if a is None:
        return None
    return C.record(
        post_id=f"{board}/{aid}", channel="ptt", sub_type="article", source=board, url=url,
        published_at=a["published_at"] or C.epoch_to_iso(epoch), fetched_at=fetched_at,
        text=(a["title"] + "\n" + a["body"]) if a["body"] else a["title"], collect_method="html")


def parse_atom(xml_text):
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", ATOM_NS):
        link = e.find("a:link", ATOM_NS)
        url = link.get("href") if link is not None else e.findtext("a:id", default="", namespaces=ATOM_NS)
        content = e.findtext("a:content", default="", namespaces=ATOM_NS)  # 已是 HTML 字串（<pre>…</pre>）
        content = re.sub(r"<[^>]+>", "", content)  # 只去 feed 包的 HTML 標籤
        content = htmllib.unescape(content).strip("\n")
        out.append({"url": url, "title": e.findtext("a:title", default="", namespaces=ATOM_NS),
                    "published": e.findtext("a:published", default=None, namespaces=ATOM_NS),
                    "content": content})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--used-before", type=int, default=0, help="這次之前已對 www.ptt.cc 載入的次數（算進 30 次上限）")
    ap.add_argument("--cache", default=None, help="開發用原始回應暫存目錄（請放 repo 外）")
    args = ap.parse_args()
    f = C.Fetcher("ptt", already_used={"www.ptt.cc": args.used_before}, cache_dir=args.cache)
    rows, failures = [], []

    # 1. 雜訊截圖背後的文章
    noise = noise_urls()
    for url in noise:
        status, text, fetched_at = f.get(url)
        if status != 200:
            failures.append({"url": url, "status": status})
            continue
        r = html_row(url, status, text, fetched_at)
        if r:
            rows.append(r)
    done = set(noise)

    # 2. 三個板的 Atom feed → 前幾篇抓 HTML，其餘用 Atom 摘要
    for board, n_html, n_rss in BOARDS:
        hdr = OVER18 if board == "Gossiping" else None
        feed_url = f"{BASE}/atom/{board}.xml"
        status, text, feed_fetched = f.get(feed_url, extra_headers=hdr)
        if status != 200:
            failures.append({"url": feed_url, "status": status})
            continue
        entries = [e for e in parse_atom(text) if e["url"] not in done]
        rss_left = n_rss
        for i, e in enumerate(entries):
            if i < n_html:
                status, atext, fetched_at = f.get(e["url"], extra_headers=hdr)
                if status == 200:
                    r = html_row(e["url"], status, atext, fetched_at)
                    if r:
                        rows.append(r)
                        done.add(e["url"])
                        continue
                failures.append({"url": e["url"], "status": status})
                continue  # 404＝文章已刪除：不用 Atom 摘要補
            if rss_left is not None:
                if rss_left <= 0:
                    continue
                rss_left -= 1
            m = ART_RE.search(e["url"])
            published = None
            if e["published"]:
                published = C.to_tw_iso(datetime.fromisoformat(e["published"]))
            rows.append(C.record(
                post_id=f"{m.group(1)}/{m.group(2)}", channel="ptt", sub_type="article", source=board,
                url=e["url"], published_at=published or C.epoch_to_iso(m.group(3)), fetched_at=feed_fetched,
                text=(e["title"] + "\n" + e["content"]) if e["content"] else e["title"], collect_method="rss"))
            done.add(e["url"])

    C.write_jsonl("ptt", rows)
    print("loads:", f.count, "failures:", failures)


if __name__ == "__main__":
    main()
