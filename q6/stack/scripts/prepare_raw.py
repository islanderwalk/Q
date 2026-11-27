"""建「原文湖」：RAW_ROOT/<channel>/<yyyy-mm>.jsonl（GCS／BigQuery 匯出的本機對應元件），給灌資料（seed）與回補 Job 用。

全部來源都是原文＋原始發文時間；沒有發文時間的語料不收（2026-10-31 起不再有合成時間）：
1. --titles   ../data/ptt_titles_7d.jsonl：八卦板標題 6,685 則，時間取自文章代碼 M.<unix 秒>；
              425 則已刪除文章沒有代碼 → 略過，不補時間
2. --pretrain yuhuanstudio/PTT-pretrain-zhtw（Hugging Face，Apache-2.0）：507,555 篇 PTT 文章全文（多看板、2001–2025），
              每篇開頭「作者:／看板:／標題:／時間: Fri Aug 31 00:23:47 2007／內文:」；時間欄解析不出來的略過
3. --gossip100k Riyuechang/PTT-Corpus-100K_Gossiping-1400-39400（Hugging Face，Apache-2.0）：105,608 篇八卦板文章（發文時間 2023-05～2024-08；
              依資料集說明是挑過熱門度的樣本：標題＋一則推文，不是八卦板全量），時間取自 article_id M.<unix 秒>
4. --crawl    歷史爬蟲的輸出資料夾（q6/code/history-crawl 寫的 jsonl）：PTT 看板列表（只有標題）、YouTube、Facebook、Instagram、Threads。
              post_id 與內文的組法跟線上的橋（api/crawl_bridge.py）同一套，同一篇不管從哪條路進 ES 都是同一份文件；
              沒有發文時間、沒有內文的略過
5. --extra    已經整理成原文格式的 jsonl（post_id／channel／published_at／text…）：ES 裡已經有、但上面來源都沒有的文章
              從 ES 匯出後放這裡，該月的原文才跟 ES 對得上
（舊版用的 Gossiping-QA-Dataset 問答沒有發文時間，不再使用。）

用法：python prepare_raw.py --out runtime/raw [--titles …] [--pretrain …parquet] [--gossip100k …parquet] [--crawl 資料夾] [--extra …jsonl]
                            [--since 2020-01] [--until 2026-09] [--max-per-source N]
--until：只收到這個月份為止。還在進新文章的月份不要放進來（G1 比的是「該月 ES 篇數 = 原文篇數」，月份還在長就永遠對不上）。
輸出：<out>/<渠道>/<yyyy-mm>.jsonl（每行一篇；post_id 去重，先到先贏）與 <out>/_manifest.json（來源、篩掉多少、月份分布）。
抓取時間 fetched_at：爬蟲的資料用爬蟲自己記的時間；下載來的語料檔用「那個檔案的下載時間」（檔案修改時間），不用建檔當下的時間。
分區 = 渠道 × 發文月份；回補帳本（setup.py）每個分區一列。
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

TW = timezone(timedelta(hours=8))
LINE_BREAKERS = ("\u0085", " ", " ")   # json.dumps 不會跳脫、但 splitlines() 會當成換行的字元
HERE = Path(__file__).resolve().parent
DEFAULT_TITLES = HERE.parent.parent / "data" / "ptt_titles_7d.jsonl"
HEAD_RE = re.compile(r"^作者:\s*(?P<author>[^\n]*)\n看板:\s*(?P<board>[^\n]*)\n標題:\s*(?P<title>[^\n]*)\n時間:\s*(?P<time>[^\n]*)\n內文:\s*(?P<body>.*)$", re.S)
ARTICLE_RE = re.compile(r"^M\.(\d{9,10})\.A\.[0-9A-Fa-f]{3}$")
MIN_YEAR, MAX_YEAR = 1995, 2026
# 以下三項跟線上的橋（api/crawl_bridge.py、api/collector.py、api/ops.make_post）同一套；改其中一邊要兩邊一起改
PTT_URL_RE = re.compile(r"/bbs/([^/]+)/(M\.\d+\.A\.[0-9A-Fa-f]+)\.html")
META_IDS = {"instagram": ("in-", "ig-"), "facebook": ("fa-", "fb-"), "threads": ("th-", "")}
MAX_TEXT = 20000


def iso_time(text):
    """爬蟲寫的 ISO 8601 發文時間 → 台灣時間的 datetime；空的、格式不對、沒帶時區的回 None（不猜時區）。"""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or not MIN_YEAR <= dt.year <= MAX_YEAR:
        return None
    return dt.astimezone(TW)


def meta_post_id(channel: str, pid: str) -> str:
    old, new = META_IDS.get(channel, ("", ""))
    return new + pid[len(old):] if old and pid.startswith(old) else pid


def ptt_time(text: str):
    """PTT 文章頭的時間「Fri Aug 31 00:23:47 2007」（台灣時間）→ datetime；解析不出來回 None。"""
    try:
        dt = datetime.strptime(" ".join((text or "").split()), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    if not MIN_YEAR <= dt.year <= MAX_YEAR:
        return None
    return dt.replace(tzinfo=TW)


def article_time(article_id: str):
    """PTT 文章代碼 M.<unix 秒>.A.<hex> → datetime；格式不對回 None。"""
    m = ARTICLE_RE.match((article_id or "").strip())
    if not m:
        return None
    dt = datetime.fromtimestamp(int(m.group(1)), TW)
    return dt if MIN_YEAR <= dt.year <= MAX_YEAR else None


def parse_pretrain(text: str):
    """PTT-pretrain 一列 text → dict(board, title, time, body)；格式或時間不合回 None。"""
    m = HEAD_RE.match(text or "")
    if not m:
        return None
    dt = ptt_time(m["time"])
    if dt is None:
        return None
    return {"board": m["board"].strip() or "PTT", "title": m["title"].strip(), "time": dt, "body": m["body"].strip()}


class Lake:
    """邊讀邊寫：每個（渠道, 月）一個檔案句柄；post_id 去重（先到先贏）。"""

    def __init__(self, root: Path, since: str = None, max_per_source: int = None, until: str = None):
        self.root, self.since, self.cap, self.until = root, since, max_per_source, until
        self.files, self.seen = {}, set()
        self.stats = {"sources": {}, "by_month": Counter(), "docs": 0, "since": since, "until": until}
        self.fetched = datetime.now(TW).isoformat(timespec="seconds")

    def src(self, name):
        return self.stats["sources"].setdefault(name, {"rows": 0, "kept": 0, "no_time": 0, "bad_format": 0, "before_since": 0, "after_until": 0,
                                                       "dup": 0, "over_cap": 0, "earliest": None, "latest": None})

    def add(self, source_name, post_id, channel, sub_type, source, url, dt, text, extra=None):
        s = self.src(source_name)
        s["rows"] += 1
        if dt is None:
            s["no_time"] += 1
            return False
        month = dt.strftime("%Y-%m")
        if self.since and month < self.since:
            s["before_since"] += 1
            return False
        if self.until and month > self.until:
            s["after_until"] += 1
            return False
        if self.cap and s["kept"] >= self.cap:
            s["over_cap"] += 1
            return False
        text = (text or "").strip()
        if not text:
            s["bad_format"] += 1
            return False
        if post_id in self.seen:
            s["dup"] += 1
            return False
        self.seen.add(post_id)
        row = {"post_id": post_id, "channel": channel, "sub_type": sub_type, "source": source, "url": url,
               "published_at": dt.isoformat(timespec="seconds"), "fetched_at": self.fetched, "text": text,
               "time_basis": "real", "dataset": source_name, **(extra or {})}
        key = (channel, month)
        if key not in self.files:
            d = self.root / channel
            d.mkdir(parents=True, exist_ok=True)
            self.files[key] = open(d / f"{month}.jsonl", "w", encoding="utf-8")
        line = json.dumps(row, ensure_ascii=False)
        # 這三個字元 Python 的 splitlines() 會當成換行，讀檔的程式（seed／回補）會把一篇切成兩段而解析失敗；
        # 寫成 \uXXXX，讀回來的內文一模一樣。
        for ch in LINE_BREAKERS:
            if ch in line:
                line = line.replace(ch, "\\u%04x" % ord(ch))
        self.files[key].write(line + "\n")
        s["kept"] += 1
        iso = row["published_at"]
        s["earliest"] = min(s["earliest"] or iso, iso)
        s["latest"] = max(s["latest"] or iso, iso)
        self.stats["by_month"][f"{channel}/{month}"] += 1
        self.stats["docs"] += 1
        return True

    def close(self):
        for f in self.files.values():
            f.close()
        self.stats["partitions"] = len(self.files)
        self.stats["by_month"] = dict(sorted(self.stats["by_month"].items()))
        self.stats["built_at"] = self.fetched
        (self.root / "_manifest.json").write_text(json.dumps(self.stats, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.stats


def file_time(path: Path) -> dict:
    """語料檔的下載時間（檔案修改時間，台灣時間）→ 當作這個檔案裡每一篇的抓取時間。"""
    return {"fetched_at": datetime.fromtimestamp(path.stat().st_mtime, TW).isoformat(timespec="seconds")}


def load_titles(lake: Lake, path: Path):
    name = "ptt_titles_7d (ptt.cc Gossiping 列表頁，2026-10)"
    got = file_time(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        aid = r["doc_id"]
        dt = article_time(aid)            # deleted-… 沒有代碼 → dt None → 略過
        lake.add(name, f"ptt-{aid}", "ptt", "title", "Gossiping",
                 f"https://www.ptt.cc/bbs/Gossiping/{aid}.html" if dt else None, dt, r.get("text", ""), got)


def _parquet_batches(path: Path, columns):
    import pyarrow.parquet as pq   # 只有讀 parquet 才需要；測試不裝 pyarrow 也能 import 本模組
    pf = pq.ParquetFile(str(path))
    names = set(pf.schema_arrow.names)
    missing = [c for c in columns if c not in names]
    if missing:
        raise SystemExit(f"{path} 缺欄位 {missing}；實際欄位：{sorted(names)}")
    for batch in pf.iter_batches(batch_size=2048, columns=list(columns)):
        yield batch.to_pylist()


def load_pretrain(lake: Lake, path: Path):
    name = "yuhuanstudio/PTT-pretrain-zhtw"
    got = file_time(path)
    for rows in _parquet_batches(path, ["text"]):
        for r in rows:
            text = r.get("text") or ""
            p = parse_pretrain(text)
            if p is None:
                s = lake.src(name)
                s["rows"] += 1
                s["bad_format" if not HEAD_RE.match(text) else "no_time"] += 1
                continue
            pid = "pttpre-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
            lake.add(name, pid, "ptt", "article", p["board"], None, p["time"], f"{p['title']}\n{p['body']}".strip(), got)


def load_gossip100k(lake: Lake, path: Path):
    name = "Riyuechang/PTT-Corpus-100K_Gossiping-1400-39400"
    got = file_time(path)
    for rows in _parquet_batches(path, ["article_id", "question", "answer"]):
        for r in rows:
            aid = (r.get("article_id") or "").strip()
            dt = article_time(aid)
            text = "\n".join(x for x in ((r.get("question") or "").strip(), (r.get("answer") or "").strip()) if x)
            lake.add(name, f"ptt-{aid}" if dt else f"pttg-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}", "ptt", "article", "Gossiping",
                     f"https://www.ptt.cc/bbs/Gossiping/{aid}.html" if dt else None, dt, text, got)


def _jsonl(path: Path):
    """一行一筆；壞掉的行（爬蟲正在寫的最後半行）回 None，由呼叫端記成 bad_format。"""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                r = None
            yield r if isinstance(r, dict) else None


def _bad(lake: Lake, name: str):
    s = lake.src(name)
    s["rows"] += 1
    s["bad_format"] += 1


def _crawl_extra(r: dict) -> dict:
    """爬蟲那一行自己的作者與抓取時間（有才帶；沒有抓取時間就用建檔時間）。"""
    out = {"author": r.get("author") or None}
    if isinstance(r.get("fetched_at"), str) and r["fetched_at"].strip():
        out["fetched_at"] = r["fetched_at"].strip()
    return out


def load_crawl_ptt(lake: Lake, crawl: Path):
    """PTT 看板列表：只有標題，沒有內文。post_id = ptt-<文章代碼>，看板名取自網址（與橋的 from_ptt 相同）。"""
    name = "history-crawl ptt/lists（ptt.cc 看板列表，只有標題）"
    for path in sorted((crawl / "ptt" / "lists").glob("*.jsonl")):
        for r in _jsonl(path):
            m = PTT_URL_RE.search((r or {}).get("url") or "")
            if not m or not r.get("title"):
                _bad(lake, name)
                continue
            lake.add(name, f"ptt-{m.group(2)}", "ptt", "article", m.group(1), r["url"], iso_time(r.get("published_at")),
                     r["title"][:MAX_TEXT], _crawl_extra(r))


def load_crawl_youtube(lake: Lake, crawl: Path):
    """YouTube：post_id = video_id，內文 = 標題＋換行＋說明（與橋的 from_youtube 相同）。"""
    name = "history-crawl youtube/out/videos.jsonl"
    path = crawl / "youtube" / "out" / "videos.jsonl"
    if not path.exists():
        return
    for r in _jsonl(path):
        if not r or not r.get("video_id"):
            _bad(lake, name)
            continue
        lake.add(name, r["video_id"], "youtube", "video", r.get("source"), r.get("url"), iso_time(r.get("published_at")),
                 f"{r.get('title') or ''}\n{r.get('text') or ''}".strip()[:MAX_TEXT], _crawl_extra(r))


def load_crawl_meta(lake: Lake, crawl: Path):
    """Facebook／Instagram／Threads：post_id 的前綴換成線上那一套（fa-→fb-、in-→ig-、th-→不加；與橋的 from_meta 相同）。"""
    for ch in ("facebook", "instagram", "threads"):
        name = f"history-crawl metalogin/deep/clean/{ch}.jsonl"
        path = crawl / "metalogin" / "deep" / "clean" / f"{ch}.jsonl"
        if not path.exists():
            continue
        for r in _jsonl(path):
            if not r or not r.get("post_id") or r.get("channel") != ch:
                _bad(lake, name)
                continue
            lake.add(name, meta_post_id(ch, r["post_id"]), ch, r.get("sub_type") or "post", r.get("source"), r.get("url"),
                     iso_time(r.get("published_at")), (r.get("text") or "")[:MAX_TEXT], _crawl_extra(r))


def load_extra(lake: Lake, path: Path):
    """已經是原文格式的 jsonl（從 ES 匯出的既有文章）：欄位原樣帶進來，post_id 不改。"""
    name = f"extra（{path.name}）"
    for r in _jsonl(path):
        if not r or not r.get("post_id") or not r.get("channel"):
            _bad(lake, name)
            continue
        lake.add(name, r["post_id"], r["channel"], r.get("sub_type") or "post", r.get("source"), r.get("url"),
                 iso_time(r.get("published_at")), r.get("text") or r.get("content") or "", _crawl_extra(r))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="RAW_ROOT，例如 runtime/raw")
    ap.add_argument("--crawl", help="歷史爬蟲的輸出資料夾（底下有 ptt/lists、youtube/out、metalogin/deep/clean）")
    ap.add_argument("--extra", help="已經是原文格式的 jsonl（從 ES 匯出的既有文章）")
    ap.add_argument("--until", help="只收到這個月份（YYYY-MM）為止，例如 2026-09；預設不限")
    ap.add_argument("--titles", default=str(DEFAULT_TITLES), help="ptt_titles_7d.jsonl（給空字串就不用）")
    ap.add_argument("--pretrain", help="yuhuanstudio/PTT-pretrain-zhtw 的 parquet")
    ap.add_argument("--gossip100k", help="Riyuechang/PTT-Corpus-100K_Gossiping-1400-39400 的 parquet")
    ap.add_argument("--since", help="只收這個月份（YYYY-MM）之後的文章，例如 2020-01；預設全部")
    ap.add_argument("--max-per-source", type=int, help="每個來源最多收幾篇（照檔案順序取前 N 篇；測試環境縮小用）")
    a = ap.parse_args(argv)
    for flag, val in (("--since", a.since), ("--until", a.until)):
        if val and not re.match(r"^\d{4}-\d{2}$", val):
            raise SystemExit(f"{flag} 格式 YYYY-MM")
    root = Path(a.out)
    root.mkdir(parents=True, exist_ok=True)
    stale = list(root.glob("*/*.jsonl"))
    if stale:
        raise SystemExit(f"{root} 已有 {len(stale)} 個分區檔；要重建請先清掉（避免新舊混在一起）")
    lake = Lake(root, a.since, a.max_per_source, a.until)
    # 順序 = 同一個 post_id 誰贏：有全文的語料先、ES 既有文章次之、只有標題的列表最後
    if a.gossip100k:
        load_gossip100k(lake, Path(a.gossip100k))
    if a.pretrain:
        load_pretrain(lake, Path(a.pretrain))
    if a.extra:
        load_extra(lake, Path(a.extra))
    if a.crawl:
        crawl = Path(a.crawl)
        load_crawl_ptt(lake, crawl)
        load_crawl_youtube(lake, crawl)
        load_crawl_meta(lake, crawl)
    if a.titles:
        load_titles(lake, Path(a.titles))
    stats = lake.close()
    print(json.dumps({k: v for k, v in stats.items() if k != "by_month"}, ensure_ascii=False, indent=2))
    months = stats["by_month"]
    print(f"partitions={stats['partitions']} docs={stats['docs']} months={min(months) if months else None}..{max(months) if months else None}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
