"""歷史爬蟲 → 文字雲的橋：把 q6/code/history-crawl 寫出的 jsonl（PTT 列表、YouTube、Threads／Facebook／Instagram）
接到跟採集器同一條路：ES 本體（doc_as_upsert）＋ Pub/Sub parsed-posts，斷詞服務 v0／v1／v2 各自斷詞寫回。

只讀檔案、不碰爬蟲本身：每個檔案記讀到的位元組位置（PostgreSQL crawl_bridge_offsets），爬蟲往後追加的行下一輪就接著送，
重啟後從上次的位置續讀；同一篇再出現時內容沒變就不重發（content_sha 相同），變了才重寫本體、重發斷詞。
沒有發文時間的一律略過（不補合成時間）：在呼叫 ops.make_post 之前就擋（to_post），make_post 缺時間會補現在時間；
BRIDGE_SINCE 之前發的也略過，文字雲只需要近期的量。
post_id 換成採集器那一套（PTT ptt-M.xxx、YouTube video_id）；Meta 沒有採集器，換成 data/channels 樣本那一套
（IG ig-、FB fb-、Threads 不加前綴），同一篇不管從哪條路進來都是同一份文件。改成這套之前寫進 ES 的 in-／fa-／th- 文件還在，
不要清掉 metalogin/* 的 crawl_bridge_offsets 重讀，否則每一篇都會以新 id 再寫一份（已有的成對文件見 scripts/fix_sample_es.py）。

用法（容器內）：python crawl_bridge.py，環境變數 CRAWL_ROOT（預設 /crawl）、BRIDGE_SINCE（預設 30 天前）、BRIDGE_POLL_SEC（預設 60）。
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import collector as C
import ops

ROOT = Path(os.environ.get("CRAWL_ROOT", "/crawl"))
POLL = int(os.environ.get("BRIDGE_POLL_SEC", "60"))
BATCH = int(os.environ.get("BRIDGE_BATCH", "500"))
PAUSE = float(os.environ.get("BRIDGE_PAUSE_SEC", "1.0"))     # 每批之間停一下，斷詞服務跟得上，模擬器的積壓不會一下子衝很高
SINCE = os.environ.get("BRIDGE_SINCE") or (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")


def from_ptt(r: dict):
    """PTT 看板列表：只有標題（內文頁另外抓，列表行先讓標題進文字雲）；post_id 與採集器同一套規則（ptt-M.xxx）。"""
    m = C.PTT_URL_RE.search(r.get("url") or "")
    if not m or not r.get("title"):
        return None
    return ops.make_post("ptt", r["title"], r["url"], m.group(1), r.get("published_at"), f"ptt-{m.group(2)}", "article",
                         "history_crawl", author=r.get("author"))


def from_youtube(r: dict):
    """YouTube：post_id 用 video_id（與採集器的 RSS／Data API 同一篇對得上）。"""
    vid = r.get("video_id")
    if not vid:
        return None
    return ops.make_post("youtube", f"{r.get('title') or ''}\n{r.get('text') or ''}".strip()[:20000], r.get("url"), r.get("source"),
                         r.get("published_at"), vid, "video", "history_crawl", author=r.get("author"))


# 歷史爬蟲的 Meta post_id 是「平台名前兩碼-」（in-／fa-／th-）；data/channels 樣本（PM 頁重播用）是 ig-、fb-、Threads 不加前綴。
# 換成樣本那一套（與 YouTube 的 yt-<id> 換成 video_id 同一個道理），否則同一篇會有 ig- 與 in- 兩份文件、文字雲算兩次
META_IDS = {"instagram": ("in-", "ig-"), "facebook": ("fa-", "fb-"), "threads": ("th-", "")}


def meta_post_id(channel: str, pid: str) -> str:
    old, new = META_IDS.get(channel, ("", ""))
    return new + pid[len(old):] if old and pid.startswith(old) else pid


def from_meta(r: dict):
    """Threads／Facebook／Instagram 已是渠道格式，只留斷詞要的欄位；post_id 換成樣本那一套。"""
    return ops.make_post(r["channel"], (r.get("text") or "")[:20000], r.get("url"), r.get("source"), r.get("published_at"),
                         meta_post_id(r["channel"], r["post_id"]), r.get("sub_type") or "post", r.get("collect_method") or "history_crawl",
                         author=r.get("author"))


SOURCES = [("ptt/lists/*.jsonl", from_ptt), ("youtube/out/videos.jsonl", from_youtube),
           ("metalogin/deep/clean/threads.jsonl", from_meta), ("metalogin/deep/clean/facebook.jsonl", from_meta),
           ("metalogin/deep/clean/instagram.jsonl", from_meta)]


def to_post(rec, conv):
    """爬蟲的一行 → post；沒有來源的發文時間就回 None（略過）。
    一定要在 conv（ops.make_post）之前擋：make_post 缺 published_at 會補 now_iso()，擋在後面永遠擋不到，那幾行會帶著合成時間進文字雲。"""
    when = rec.get("published_at") if isinstance(rec, dict) else None
    if not isinstance(when, str) or not when.strip():
        return None
    return conv(rec)


def ensure_table():
    with C._pg() as c:
        c.execute("CREATE TABLE IF NOT EXISTS crawl_bridge_offsets (path text PRIMARY KEY, pos bigint NOT NULL, sent bigint NOT NULL DEFAULT 0,"
                  " skipped bigint NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now())")


def offsets() -> dict:
    with C._pg() as c:
        return {p: pos for p, pos in c.execute("SELECT path, pos FROM crawl_bridge_offsets").fetchall()}


def save_offset(path: str, pos: int, sent: int, skipped: int):
    with C._pg() as c:
        c.execute("INSERT INTO crawl_bridge_offsets (path, pos, sent, skipped) VALUES (%s,%s,%s,%s) ON CONFLICT (path) DO UPDATE SET"
                  " pos=EXCLUDED.pos, sent=crawl_bridge_offsets.sent+EXCLUDED.sent, skipped=crawl_bridge_offsets.skipped+EXCLUDED.skipped,"
                  " updated_at=now()", (path, pos, sent, skipped))


def send(posts: list, pubsub: ops.PubSub) -> int:
    """跟採集器一樣：先比 content_sha，新的與改過的才寫本體、發斷詞。"""
    uniq = {p["post_id"]: p for p in posts}
    have = C.existing_docs(list(uniq))
    out = [p for pid, p in uniq.items() if have.get(pid, "missing") != ops.content_sha(p["text"])]
    if not out:
        return 0
    errors = C.parser_write(out)
    for p in out:
        attrs = {"edited": "1"} if p["post_id"] in have else {}
        pubsub.publish(C.TOPIC, p, channel=p["channel"], post_id=p["post_id"], **attrs)
    if errors:
        C.log("bridge_write_errors", "WARNING", errors=errors)
    return len(out)


def drain(path: Path, conv, pos: int, pubsub: ops.PubSub) -> int:
    """從 pos 讀到最後一個完整的行（爬蟲正在寫的半行留到下一輪）。"""
    size = path.stat().st_size
    if size < pos:
        pos = 0                     # 檔案被換掉或截短：從頭再對一次，內容沒變的不會重發
    if size == pos:
        return pos
    total = 0
    with path.open("rb") as f:
        f.seek(pos)
        while True:
            chunk = f.readlines(4 << 20)
            if not chunk:
                break
            if not chunk[-1].endswith(b"\n"):
                chunk = chunk[:-1]
                if not chunk:
                    break
            batch, skipped = [], 0
            for raw in chunk:
                pos += len(raw)
                try:
                    p = to_post(json.loads(raw), conv)
                except (ValueError, KeyError, TypeError):
                    p = None
                if p is None or p["published_at"][:10] < SINCE:
                    skipped += 1
                    continue
                batch.append(p)
            sent = 0
            for i in range(0, len(batch), BATCH):
                sent += send(batch[i:i + BATCH], pubsub)
                time.sleep(PAUSE)
            save_offset(str(path.relative_to(ROOT)), pos, sent, skipped)
            total += sent
            f.seek(pos)
    if total:
        C.log("bridge_sent", file=str(path.relative_to(ROOT)), sent=total)
    return pos


def run():
    ensure_table()
    pubsub = ops.PubSub(C.PROJECT, C.EMULATOR or None)
    pubsub.ensure(C.TOPIC)
    C.log("bridge_start", root=str(ROOT), since=SINCE, poll_sec=POLL)
    while True:
        try:
            if C.index_exists():
                pos = offsets()
                for pattern, conv in SOURCES:
                    for path in sorted(ROOT.glob(pattern)):
                        drain(path, conv, pos.get(str(path.relative_to(ROOT)), 0), pubsub)
            else:
                C.log("index_missing", "WARNING")
        except Exception as e:
            C.log("bridge_loop_error", "ERROR", error=str(e)[:300])
        time.sleep(POLL)


if __name__ == "__main__":
    run()
