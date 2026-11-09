#!/usr/bin/env python3
"""PTT 歷史爬蟲（可續跑，只抓未登入公開頁面）。

流程
  1) index walk：從最新 index 頁往回翻，記錄 article_id 與 epoch（檔名 M.<epoch>.A.* 即發文時間），
     直到整頁都早於 2023-01-01 為止。結果寫 inv/<board>.jsonl（不含內文）。
  2) article fetch：逐篇抓文章頁（內文 + 推文），寫 raw/ptt/<yyyy-mm>.jsonl。
  3) --mode sample：每個月均勻抽 N 篇，只抓這些篇。

規則
  - 同一主機（www.ptt.cc）請求間隔 >= 5 秒 + 0~2 秒抖動。
  - 429/5xx：指數退避，60 秒起、上限 1800 秒；連續 3 次錯誤即暫停並寫入 progress.json（exit 3）。
  - 403 / cf-mitigated（bot 挑戰）：立即暫停，不嘗試繞過。
  - 不帶 over18 cookie、不點年齡確認；遇到 over18 頁面跳過該篇並記錄 age_gate。
  - 不登入、不存 cookie/token、不存整頁 HTML、不存 IP。作者 ID 只存 sha256 前 12 碼。
  - 內文 text 保留 get_text() 原樣（不清理）；推文時間缺年份，以文章年份推算並標 time_year_inferred。
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ptt.cc"
TZ = dt.timezone(dt.timedelta(hours=8))
CUTOFF = dt.datetime(2023, 1, 1, tzinfo=TZ)
CUTOFF_EPOCH = int(CUTOFF.timestamp())  # 1672502400
ROOT = os.path.join(os.environ.get("Q6HIST_ROOT", "q6hist"), "ptt")
CHANNEL = "ptt"
UA = "q6-research-crawler/0.1 (public unauthenticated pages only; >=5s per request)"
GAP = 5.0
JITTER = 2.0
BACKOFF_START = 60
BACKOFF_MAX = 1800
PAUSE_AFTER = 3
COLLECT = "ptt_web_index_article_v1"


def now_iso():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def log(msg):
    print(f"[{now_iso()}] {msg}", flush=True)


def h_author(uid):
    if not uid:
        return ""
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:12]


def month_of_epoch(epoch):
    d = dt.datetime.fromtimestamp(epoch, TZ)
    return f"{d.year:04d}-{d.month:02d}"


class Paused(Exception):
    pass


class Budget(Exception):
    pass


class Client:
    def __init__(self, progress, max_requests):
        self.s = requests.Session()  # cookie 只在記憶體，不寫磁碟
        self.s.headers["User-Agent"] = UA
        self.last = 0.0
        self.progress = progress
        self.max_requests = max_requests
        self.consec = 0

    def get(self, url):
        """回傳 (status, text)。status: 200 / 404 / 'age_gate'。其他情況拋 Paused。"""
        backoff = BACKOFF_START
        while True:
            if self.progress["requests"] >= self.max_requests:
                raise Budget()
            wait = self.last + GAP + random.uniform(0, JITTER) - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.progress["requests"] += 1
            status, text, hdrs, final_url = None, None, {}, url
            try:
                r = self.s.get(url, timeout=30)
                status, text, hdrs, final_url = r.status_code, r.text, r.headers, r.url
            except requests.RequestException as e:
                log(f"NET_ERR {url} {e}")
            if status is not None:
                if status == 403 or "cf-mitigated" in hdrs:
                    raise Paused(f"bot/challenge status={status} url={url}")
                if status == 200:
                    if "over18" in final_url or 'name="over18"' in (text or ""):
                        self.consec = 0
                        return "age_gate", None
                    self.consec = 0
                    return 200, text
                if status == 404:
                    self.consec = 0
                    return 404, None
            self.consec += 1
            log(f"ERR status={status} url={url} consec={self.consec}")
            if self.consec >= PAUSE_AFTER:
                raise Paused(f"{PAUSE_AFTER} consecutive errors, last status={status} url={url}")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)


def parse_index(text):
    """回傳 (上頁 href 或 None, rows)。rows: 已刪除者 deleted=True 且無 article_id。"""
    soup = BeautifulSoup(text, "html.parser")
    prev = None
    for a in soup.select("div.btn-group-paging a"):
        if "上頁" in a.get_text():
            prev = a.get("href")
    rows = []
    for ent in soup.select("div.r-ent"):
        a = ent.select_one("div.title a")
        title_div = ent.select_one("div.title")
        if a is None or not a.get("href"):
            rows.append({"deleted": True, "title": title_div.get_text(strip=True) if title_div else ""})
            continue
        href = a["href"]
        m = re.search(r"/(M\.(\d+)\.A\.[^/]+?)\.html$", href)
        if not m:
            continue
        author_el = ent.select_one("div.author")
        mark_el = ent.select_one("div.mark")
        rows.append({
            "deleted": False,
            "article_id": m.group(1),
            "epoch": int(m.group(2)),
            "href": href,
            "title": a.get_text(strip=True),
            "author_hash": h_author(author_el.get_text(strip=True) if author_el else ""),
            "mark": mark_el.get_text(strip=True) if mark_el else "",
        })
    return prev, rows


def latest_page_from_prev(prev):
    m = re.search(r"index(\d+)\.html", prev or "")
    return int(m.group(1)) + 1 if m else None


PUSH_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})")


def push_time(ipdt_text, article_dt):
    """推文只有 MM/DD HH:MM，以文章年份推算；差距超過 180 天時跨年修正。"""
    m = PUSH_RE.search(ipdt_text or "")
    if not m:
        return None, False
    mo, d, hh, mm = map(int, m.groups())
    try:
        cand = dt.datetime(article_dt.year, mo, d, hh, mm, tzinfo=TZ)
    except ValueError:
        return None, False
    delta = (cand - article_dt).total_seconds()
    try:
        if delta > 180 * 86400:
            cand = cand.replace(year=cand.year - 1)
        elif delta < -180 * 86400:
            cand = cand.replace(year=cand.year + 1)
    except ValueError:
        return None, False
    return cand, True


def parse_article(text, board, href, article_id, epoch):
    """回傳 (records, status)。records[0] 是文章，之後是推文。"""
    soup = BeautifulSoup(text, "html.parser")
    main = soup.select_one("#main-content")
    if main is None:
        return [], "no_main_content"
    meta = {}
    for m in main.select(".article-metaline, .article-metaline-right"):
        t = m.select_one(".article-meta-tag")
        v = m.select_one(".article-meta-value")
        if t and v:
            meta[t.get_text(strip=True)] = v.get_text(strip=True)
    try:
        published = dt.datetime.strptime(meta.get("時間", ""), "%a %b %d %H:%M:%S %Y").replace(tzinfo=TZ)
        status = "ok"
    except ValueError:
        published = dt.datetime.fromtimestamp(epoch, TZ)
        status = "time_from_epoch"
    author_val = meta.get("作者", "")
    author_uid = author_val.split(" ")[0] if author_val else ""
    title = meta.get("標題", "")

    pushes = []
    for p in main.select("div.push"):
        tag = p.select_one(".push-tag")
        uid = p.select_one(".push-userid")
        cont = p.select_one(".push-content")
        ipdt = p.select_one(".push-ipdatetime")
        pushes.append({
            "tag": tag.get_text(strip=True) if tag else "",
            "uid": uid.get_text(strip=True) if uid else "",
            "content": cont.get_text() if cont else "",
            "ipdt": ipdt.get_text(" ", strip=True) if ipdt else "",  # 只用來解析時間，不存 IP
        })
    for n in main.select(".article-metaline, .article-metaline-right, div.push"):
        n.decompose()
    body = main.get_text()

    url = BASE + href
    fetched = now_iso()
    pub_iso = published.isoformat(timespec="seconds")
    recs = [{
        "post_id": f"ptt:{board}:{article_id}",
        "channel": CHANNEL,
        "sub_type": "post",
        "source": f"ptt/{board}",
        "board": board,
        "url": url,
        "published_at": pub_iso,
        "fetched_at": fetched,
        "title": title,
        "text": body,
        "author_hash": h_author(author_uid),
        "parent_id": None,
        "time_basis": "real",
        "collect_method": COLLECT,
    }]
    for i, p in enumerate(pushes, 1):
        pt, inferred = push_time(p["ipdt"], published)
        recs.append({
            "post_id": f"ptt:{board}:{article_id}#p{i}",
            "channel": CHANNEL,
            "sub_type": "comment",
            "source": f"ptt/{board}",
            "board": board,
            "url": url,
            "published_at": pt.isoformat(timespec="seconds") if pt else None,
            "fetched_at": fetched,
            "text": p["content"],
            "author_hash": h_author(p["uid"]),
            "push_tag": p["tag"],
            "parent_id": f"ptt:{board}:{article_id}",
            "time_basis": "real",
            "time_year_inferred": inferred,
            "collect_method": COLLECT,
        })
    return recs, status


# ---------- 狀態檔（checkpoint） ----------
def paths_for(board):
    return {
        "state": os.path.join(ROOT, "state", f"{board}.json"),
        "inv": os.path.join(ROOT, "inv", f"{board}.jsonl"),
        "done": os.path.join(ROOT, "done", f"{board}.tsv"),
        "missing": os.path.join(ROOT, "missing", f"{board}.tsv"),
    }


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def append_lines(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def save_progress(progress):
    progress["updated_at"] = now_iso()
    save_json(os.path.join(ROOT, "progress.json"), progress)


def load_inventory(board):
    seen = {}
    p = paths_for(board)["inv"]
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                seen[r["article_id"]] = r
    return sorted(seen.values(), key=lambda r: r["epoch"])


def load_done(board):
    done = {}
    for key in ("done", "missing"):
        p = paths_for(board)[key]
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if parts and parts[0]:
                        done[parts[0]] = parts[1] if len(parts) > 1 else ""
    return done


# ---------- 階段 1：index walk ----------
def walk_index(board, bstate, client, progress, max_pages):
    if bstate.get("index_done"):
        return
    pages_this_run = 0
    while True:
        if max_pages is not None and pages_this_run >= max_pages:
            return
        p = bstate.get("next_page")
        if p is None:
            url = f"{BASE}/bbs/{board}/index.html"
        else:
            url = f"{BASE}/bbs/{board}/index{p}.html"
        status, text = client.get(url)
        if status != 200:
            raise Paused(f"index page status={status} url={url}")
        prev, rows = parse_index(text)
        if "latest_page" not in bstate:
            bstate["latest_page"] = latest_page_from_prev(prev)
            p = bstate["latest_page"]
        else:
            p = int(re.search(r"index(\d+)\.html", url).group(1)) if p is not None else bstate["latest_page"]
        live = [r for r in rows if not r["deleted"]]
        in_range = [r for r in live if r["epoch"] >= CUTOFF_EPOCH]
        old = [r for r in live if r["epoch"] < CUTOFF_EPOCH]
        lines = []
        for r in in_range:
            r["index_page"] = p
            lines.append(json.dumps({k: r[k] for k in ("article_id", "epoch", "href", "title", "author_hash", "mark", "index_page")}, ensure_ascii=False))
        append_lines(paths_for(board)["inv"], lines)
        bstate["index_pages_done"] = bstate.get("index_pages_done", 0) + 1
        bstate["next_page"] = p - 1
        pages_this_run += 1
        # 停止條件：整頁都早於 cutoff，且不是最新兩頁（最新頁可能有置頂舊文）
        if live and len(old) == len(live) and p <= bstate["latest_page"] - 2:
            bstate["index_done"] = True
            log(f"{board}: index walk reached cutoff at page {p}")
        if p <= 1:
            bstate["index_done"] = True
        save_json(paths_for(board)["state"], bstate)
        save_progress(progress)
        if bstate.get("index_done"):
            return


# ---------- 階段 2：文章 ----------
def sample_monthly(rows, n):
    by_m = {}
    for r in rows:
        by_m.setdefault(month_of_epoch(r["epoch"]), []).append(r)
    out = []
    for m in sorted(by_m):
        lst = by_m[m]
        if len(lst) <= n:
            out.extend(lst)
        else:
            step = len(lst) / n
            out.extend(lst[int(i * step)] for i in range(n))
    return out


def fetch_articles(board, bstate, client, progress, since_epoch, until_epoch, sample_n):
    inv = load_inventory(board)
    done = load_done(board)
    rows = [r for r in inv if since_epoch <= r["epoch"] < until_epoch and r["article_id"] not in done]
    if sample_n:
        rows = sample_monthly(rows, sample_n)
    bstat = progress["boards"].setdefault(board, {})
    log(f"{board}: to fetch {len(rows)} articles (inventory={len(inv)}, done={len(done)})")
    for r in rows:
        status, text = client.get(BASE + r["href"])
        aid = r["article_id"]
        if status == 404:
            append_lines(paths_for(board)["missing"], [f"{aid}\tmissing_404"])
            bstat["missing"] = bstat.get("missing", 0) + 1
            continue
        if status == "age_gate":
            append_lines(paths_for(board)["missing"], [f"{aid}\tage_gate_skipped"])
            bstat["age_gate"] = bstat.get("age_gate", 0) + 1
            continue
        recs, pstatus = parse_article(text, board, r["href"], aid, r["epoch"])
        if not recs:
            append_lines(paths_for(board)["missing"], [f"{aid}\t{pstatus}"])
            bstat["parse_fail"] = bstat.get("parse_fail", 0) + 1
            continue
        by_month = {}
        for rec in recs:
            ym = (rec["published_at"] or "")[:7] or month_of_epoch(r["epoch"])
            by_month.setdefault(ym, []).append(json.dumps(rec, ensure_ascii=False))
        for ym, lines in by_month.items():
            append_lines(os.path.join(ROOT, "raw", CHANNEL, f"{ym}.jsonl"), lines)
        append_lines(paths_for(board)["done"], [f"{aid}\t{pstatus}"])
        bstat["articles_done"] = bstat.get("articles_done", 0) + 1
        bstat["posts"] = bstat.get("posts", 0) + 1
        bstat["comments"] = bstat.get("comments", 0) + (len(recs) - 1)
        bstat["last_published_at"] = recs[0]["published_at"]
        if bstat["articles_done"] % 20 == 0:
            save_progress(progress)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boards", required=True, help="逗號分隔，例如 Stock,Baseball")
    ap.add_argument("--mode", choices=["full", "sample"], default="full")
    ap.add_argument("--sample-per-month", type=int, default=0)
    ap.add_argument("--since", default="2023-01-01", help="只抓文章的起始日（YYYY-MM-DD，台北時間）")
    ap.add_argument("--until", default="2100-01-01")
    ap.add_argument("--max-requests", type=int, default=10 ** 9, help="本次執行的請求上限（試跑用）")
    ap.add_argument("--walk-pages", type=int, default=None, help="本次最多翻幾頁 index（試跑用）")
    args = ap.parse_args()

    os.makedirs(ROOT, exist_ok=True)
    progress = load_json(os.path.join(ROOT, "progress.json"), {
        "channel": CHANNEL, "requests": 0, "paused": False, "pause_reason": "", "boards": {}})
    progress["requests"] = progress.get("requests", 0)
    progress["paused"] = False
    progress["pause_reason"] = ""
    since_epoch = int(dt.datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=TZ).timestamp())
    until_epoch = int(dt.datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=TZ).timestamp())
    client = Client(progress, args.max_requests + progress["requests"])
    boards = [b.strip() for b in args.boards.split(",") if b.strip()]
    exit_code = 0
    try:
        for board in boards:
            bpaths = paths_for(board)
            bstate = load_json(bpaths["state"], {})
            progress["boards"].setdefault(board, {})
            walk_index(board, bstate, client, progress, args.walk_pages)
            if bstate.get("index_done") or args.walk_pages is not None:
                fetch_articles(board, bstate, client, progress, max(since_epoch, CUTOFF_EPOCH), until_epoch,
                               args.sample_per_month if args.mode == "sample" else 0)
            progress["boards"][board]["stage"] = "done" if bstate.get("index_done") else "index_walk"
            progress["boards"][board]["index_pages_done"] = bstate.get("index_pages_done", 0)
            progress["boards"][board]["inventory_rows"] = len(load_inventory(board))
            save_json(bpaths["state"], bstate)
            save_progress(progress)
    except Budget:
        log("request budget reached; state saved, resumable")
        exit_code = 0
    except Paused as e:
        progress["paused"] = True
        progress["pause_reason"] = str(e)
        log(f"PAUSED: {e}")
        exit_code = 3
    finally:
        progress["eta_hours_estimate"] = None
        save_progress(progress)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
