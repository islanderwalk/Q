"""測試用的 PTT 八卦板：HTML 結構照存下來的 PTT 頁面（tests/fixtures/real/）產生，時間由測試控制。

可以模擬：新文章、新推文、刪文（列表變成佔位、文章 404）、網站改版（找不到 #main-content）、
內文被清空、大量 404、被擋（429／503）。
"""
from __future__ import annotations

import html
import random
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TW = timezone(timedelta(hours=8))
PER_PAGE = 20


class FakeClock:
    def __init__(self, start=None):
        self.t = (start or datetime.now(timezone.utc)).replace(microsecond=0)

    def now(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)
        return self.t


class FakeBoard:
    def __init__(self, clock, board="Gossiping", seed=7):
        self.clock, self.board = clock, board
        self.rnd = random.Random(seed)
        self.posts = []          # 依發文時間排序
        self.by_aid = {}
        self.pinned = []         # 最新頁分隔線下的置底文
        self.layout_changed = False
        self.wipe_content = False
        self.gone = set()        # 這些文章回 404，但列表沒變（模擬被擋或網站異常）
        self.block_status = None  # 例如 429：所有請求都回這個
        self.hits = Counter()
        self.lock = threading.Lock()

    # ---------- 產生資料 ----------
    def _aid(self, ts):
        return f"M.{ts}.A.{self.rnd.randrange(0x100, 0xFFF):03X}"

    def add_post(self, published, title=None, content=None, author=None, n_pushes=None, keyword=None):
        ts = int(published.timestamp())
        if self.posts and ts <= self.posts[-1]["ts"]:
            ts = self.posts[-1]["ts"] + 1
        aid = self._aid(ts)
        i = len(self.posts)
        p = {"aid": aid, "ts": ts, "author": author or f"user{i % 97:02d}", "title": title or f"[問卦] 測試文章 {i}",
             "content": content if content is not None else f"這是第 {i} 篇測試內文。" + (f"內文提到{keyword}。" if keyword else ""),
             "pushes": [], "deleted": False}
        self.posts.append(p)
        self.by_aid[aid] = p
        n = self.rnd.randint(0, 12) if n_pushes is None else n_pushes
        for k in range(n):
            self.add_push(aid, at=datetime.fromtimestamp(ts, timezone.utc) + timedelta(minutes=k + 1),
                          content=f"第 {k} 則留言" + ("，颱風假" if (i + k) % 211 == 0 else ""))
        return p

    def add_push(self, aid, tag=None, user=None, content=None, at=None):
        p = self.by_aid[aid]
        k = len(p["pushes"])
        tag = tag or self.rnd.choice(["推", "推", "推", "→", "噓"])
        p["pushes"].append({"tag": tag, "user": user or f"pusher{k % 31:02d}", "content": content or f"第 {k} 則留言",
                            "ip": f"10.0.{k % 250}.{(k * 7) % 250}", "at": at or self.clock.now()})

    def seed(self, days=8.0, per_day=36, keyword_every=9, pushes_mean=None, content_len=0):
        """從現在往回 days 天，平均每天 per_day 篇。pushes_mean 有值就用指數分佈產生留言數（模擬留言數的長尾分佈）。"""
        now = self.clock.now()
        n = int(days * per_day)
        step = timedelta(days=days) / n
        filler = "測試內文段落，" * (content_len // 21 + 1) if content_len else ""
        for i in range(n):
            kw = "颱風" if i % keyword_every == 0 else None
            n_pushes = min(600, int(self.rnd.expovariate(1 / pushes_mean))) if pushes_mean else None
            content = (f"這是第 {i} 篇測試內文。" + (f"內文提到{kw}。" if kw else "") + filler[:content_len]) if content_len else None
            self.add_post(now - timedelta(days=days) + step * i, keyword=kw, n_pushes=n_pushes, content=content)
        old = now - timedelta(days=200)
        for j in range(2):
            self.pinned.append({"aid": self._aid(int(old.timestamp()) + j), "ts": int(old.timestamp()) + j,
                                "author": "moderator", "title": f"[公告] 置底公告 {j}", "content": "置底", "pushes": [],
                                "deleted": False})

    def delete(self, aid):
        self.by_aid[aid]["deleted"] = True

    # ---------- 頁面 ----------
    def n_pages(self):
        return max(1, -(-len(self.posts) // PER_PAGE))

    @staticmethod
    def nrec(p):
        d = sum(x["tag"] == "推" for x in p["pushes"]) - sum(x["tag"] == "噓" for x in p["pushes"])
        if d >= 100:
            return '<span class="hl f1">爆</span>'
        if d <= -100:
            return '<span class="hl f0">XX</span>'
        if d <= -10:
            return f'<span class="hl f0">X{abs(d) // 10}</span>'
        return f'<span class="hl f2">{d}</span>' if d > 0 else ""

    def _ent(self, p):
        date = datetime.fromtimestamp(p["ts"], TW).strftime("%m/%d").lstrip("0").rjust(5)
        if p["deleted"]:
            title = f"\n\t\t\t\t(本文已被刪除) [{html.escape(p['author'])}]\n\t\t\t"
            author = "-"
        else:
            title = (f'\n\t\t\t\t<a href="/bbs/{self.board}/{p["aid"]}.html">{html.escape(p["title"])}</a>\n\t\t\t')
            author = html.escape(p["author"])
        return (f'\t\t<div class="r-ent">\n\t\t\t<div class="nrec">{self.nrec(p)}</div>\n'
                f'\t\t\t<div class="title">{title}</div>\n\t\t\t<div class="meta">\n'
                f'\t\t\t\t<div class="author">{author}</div>\n\t\t\t\t<div class="date">{date}</div>\n'
                f'\t\t\t\t<div class="mark"></div>\n\t\t\t</div>\n\t\t</div>\n')

    def index_html(self, page_no=None):
        last = self.n_pages()
        newest = page_no is None or page_no == last
        page_no = last if page_no is None else page_no
        if not 1 <= page_no <= last:
            return None
        items = self.posts[(page_no - 1) * PER_PAGE: page_no * PER_PAGE]
        prev = (f'<a class="btn wide" href="/bbs/{self.board}/index{page_no - 1}.html">&lsaquo; 上頁</a>'
                if page_no > 1 else '<a class="btn wide disabled">&lsaquo; 上頁</a>')
        nxt = (f'<a class="btn wide" href="/bbs/{self.board}/index{page_no + 1}.html">下頁 &rsaquo;</a>'
               if not newest else '<a class="btn wide disabled">下頁 &rsaquo;</a>')
        body = "".join(self._ent(p) for p in items)
        if newest:
            body += '        <div class="r-list-sep"></div>\n' + "".join(self._ent(p) for p in self.pinned)
        return (f'<!DOCTYPE html>\n<html>\n<head><meta charset="utf-8"><title>看板 {self.board} 文章列表 - 批踢踢實業坊</title></head>\n'
                f'<body>\n<div id="action-bar-container"><div class="action-bar"><div class="btn-group btn-group-paging">\n'
                f'<a class="btn wide" href="/bbs/{self.board}/index1.html">最舊</a>\n{prev}\n{nxt}\n'
                f'<a class="btn wide" href="/bbs/{self.board}/index.html">最新</a>\n</div></div></div>\n'
                f'\t<div class="r-list-container action-bar-margin bbs-screen">\n'
                f'\t\t<div class="search-bar"><form type="get" action="search" id="search-bar">'
                f'<input class="query" type="text" name="q" value=""></form></div>\n{body}\t</div>\n</body>\n</html>\n')

    def article_html(self, p):
        when = datetime.fromtimestamp(p["ts"], TW)
        meta = (f'<div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">'
                f'{html.escape(p["author"])} (測試)</span></div><div class="article-metaline-right"><span class="article-meta-tag">'
                f'看板</span><span class="article-meta-value">{self.board}</span></div><div class="article-metaline">'
                f'<span class="article-meta-tag">標題</span><span class="article-meta-value">{html.escape(p["title"])}</span></div>'
                f'<div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">'
                f'{when.strftime("%a %b %e %H:%M:%S %Y")}</span></div>')
        content = "" if self.wipe_content else html.escape(p["content"])
        pushes = [] if self.wipe_content else p["pushes"]
        push_html = "".join(
            f'<div class="push"><span class="hl push-tag">{x["tag"]} </span><span class="f3 hl push-userid">{html.escape(x["user"])}'
            f'</span><span class="f3 push-content">: {html.escape(x["content"])}</span><span class="push-ipdatetime"> {x["ip"]} '
            f'{x["at"].astimezone(TW).strftime("%m/%d %H:%M")}\n</span></div>' for x in pushes)
        main_id = "article-body" if self.layout_changed else "main-content"
        return (f'<!DOCTYPE html>\n<html>\n<head><meta charset="utf-8"><title>{html.escape(p["title"])} - 看板 {self.board}'
                f' - 批踢踢實業坊</title></head>\n<body>\n<div id="{main_id}" class="bbs-screen bbs-content">{meta}\n{content}\n\n--\n'
                f'<span class="f2">※ 發信站: 批踢踢實業坊(ptt.cc), 來自: 10.1.2.3 (臺灣)\n</span>'
                f'<span class="f2">※ 文章網址: https://www.ptt.cc/bbs/{self.board}/{p["aid"]}.html\n</span>{push_html}</div>\n'
                f'</body>\n</html>\n')

    def route(self, path):
        with self.lock:
            prefix = f"/bbs/{self.board}/"
            if not path.startswith(prefix):
                return 404, "404 - Not Found.", "other"
            name = path[len(prefix):]
            if self.block_status:
                return self.block_status, "Too Many Requests", "blocked"
            if name == "index.html":
                return 200, self.index_html(), "index"
            if name.startswith("index") and name.endswith(".html"):
                body = self.index_html(int(name[5:-5]))
                return (200, body, "index") if body else (404, "404 - Not Found.", "index")
            aid = name[:-5] if name.endswith(".html") else name
            p = self.by_aid.get(aid)
            if p is None or p["deleted"] or aid in self.gone:
                return 404, "<html><body>404 - Not Found.</body></html>", "article"
            return 200, self.article_html(p), "article"


class _Handler(BaseHTTPRequestHandler):
    board: FakeBoard = None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        status, body, kind = self.board.route(path)
        self.board.hits[kind] += 1
        self.board.hits[path] += 1
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if status == 429:
            self.send_header("Retry-After", "1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class FakePtt:
    def __init__(self, board: FakeBoard):
        handler = type("H", (_Handler,), {"board": board})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base_url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
