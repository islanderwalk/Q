"""PTT 原始檔解析（看板列表頁、文章頁、離線標題 JSONL）。

只解析 repo 內的 fixture；不連 ptt.cc。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

POST_ID_RE = re.compile(r"(M\.(\d{9,11})\.A\.[0-9A-F]{3})")
HREF_RE = re.compile(r"/bbs/([^/]+)/(M\.\d+\.A\.[0-9A-F]{3})\.html")
TPE = timezone(timedelta(hours=8))


@dataclass
class Post:
    post_id: str
    board: str
    title: str
    author: str | None
    posted_at: str | None  # ISO-8601（UTC）；由 post_id 內的 epoch 推得
    list_date: str | None  # 列表頁的 MM/DD（原樣保留）
    nrec: str | None
    body: str | None
    url: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def posted_at_from_id(post_id: str) -> str | None:
    m = POST_ID_RE.search(post_id)
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(2)), tz=timezone.utc).isoformat()


def parse_index(html: str, default_board: str) -> tuple[list[Post], int]:
    """列表頁：回傳 (文章, 已刪除列數)。置底文（r-list-sep 之後）也算文章。"""
    soup = BeautifulSoup(html, "html.parser")
    posts: list[Post] = []
    deleted = 0
    for ent in soup.select("div.r-ent"):
        a = ent.select_one("div.title a")
        if a is None or not a.get("href"):
            deleted += 1  # 「(本文已被刪除)」沒有連結
            continue
        m = HREF_RE.search(a["href"])
        if not m:
            continue
        board, post_id = m.group(1), m.group(2)
        author = ent.select_one("div.meta div.author")
        date = ent.select_one("div.meta div.date")
        nrec = ent.select_one("div.nrec")
        posts.append(Post(
            post_id=post_id,
            board=board or default_board,
            title=a.get_text(strip=True),
            author=(author.get_text(strip=True) or None) if author else None,
            posted_at=posted_at_from_id(post_id),
            list_date=(date.get_text(strip=True) or None) if date else None,
            nrec=(nrec.get_text(strip=True) or None) if nrec else None,
            body=None,
            url=f"https://www.ptt.cc{a['href']}",
        ))
    return posts, deleted


def parse_article(html: str, default_board: str) -> list[Post]:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content")
    if main is None:
        raise ValueError("not a PTT article page (#main-content missing)")
    meta: dict[str, str] = {}
    for line in main.select("div.article-metaline, div.article-metaline-right"):
        tag = line.select_one(".article-meta-tag")
        val = line.select_one(".article-meta-value")
        if tag and val:
            meta[tag.get_text(strip=True)] = val.get_text(strip=True)
        line.decompose()
    url = None
    og = soup.select_one('meta[property="og:url"]')
    if og and og.get("content"):
        url = og["content"]
    if url is None:
        link = main.find("a", href=HREF_RE)
        url = link["href"] if link else None
    m = HREF_RE.search(url or "")
    if not m:
        raise ValueError("cannot find article URL / post id")
    board, post_id = m.group(1), m.group(2)
    pushes = main.select("div.push")
    for p in pushes:
        p.decompose()
    for rc in main.select("div.richcontent"):
        rc.decompose()
    body = main.get_text("\n")
    body = body.split("\n※ 發信站")[0].strip()  # 去掉簽名檔之後的系統訊息
    body = body.rstrip("-\n ").strip()
    author = meta.get("作者", "").split(" ")[0] or None
    posted_at = posted_at_from_id(post_id)
    if "時間" in meta:
        try:
            posted_at = datetime.strptime(meta["時間"], "%a %b %d %H:%M:%S %Y").replace(tzinfo=TPE) \
                .astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return [Post(
        post_id=post_id,
        board=meta.get("看板") or board or default_board,
        title=meta.get("標題", ""),
        author=author,
        posted_at=posted_at,
        list_date=None,
        nrec=str(len(pushes)),
        body=body or None,
        url=url,
    )]


def parse_titles_jsonl(text: str, default_board: str) -> list[Post]:
    """q6/data/ptt_titles_7d.jsonl：每行 {"doc_id": "M.xxx.A.yyy", "text": "標題"}（只有標題）。"""
    posts: list[Post] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        post_id = row["doc_id"]
        posts.append(Post(
            post_id=post_id,
            board=default_board,
            title=row["text"],
            author=None,
            posted_at=posted_at_from_id(post_id),
            list_date=None,
            nrec=None,
            body=None,
            url=f"https://www.ptt.cc/bbs/{default_board}/{post_id}.html",
        ))
    return posts


def parse_raw(kind: str, raw: bytes, board: str) -> list[Post]:
    text = raw.decode("utf-8")
    if kind == "ptt_index":
        return parse_index(text, board)[0]
    if kind == "ptt_article":
        return parse_article(text, board)
    if kind == "ptt_titles_jsonl":
        return parse_titles_jsonl(text, board)
    raise ValueError(f"unknown raw kind: {kind}")


def dedupe(posts: list[Post]) -> list[Post]:
    """同一份原始檔內重複的 post_id 只留最後一筆（避免同一個 INSERT ... ON CONFLICT 撞到自己）。"""
    seen: dict[str, Post] = {}
    for p in posts:
        seen[p.post_id] = p
    return list(seen.values())
