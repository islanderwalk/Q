"""回補 Job（Cloud Run Jobs）：一次領一個分區（渠道 × 月，由新到舊），讀原文 → 斷詞 → 條件寫入 → 過閘門。

- 原文來源（RAW_SOURCE，見 rawsrc.py）：正式環境 bigquery 讀 RAW_BQ_TABLE，用帳本 started_at 當固定時間點（FOR SYSTEM_TIME AS OF），
  續跑時順序不變；本機 file 讀 RAW_ROOT/<channel>/<yyyy-mm>.jsonl 當對應元件。設計上不從 ES scroll，避免和線上查詢搶
- 帳本在 ES（索引 backfill_ledger，見 ledger.py；PostgreSQL 只剩 backfill_control 限速與 backfill_batches 批次紀錄）
- 斷點續跑：每個 bulk 寫成功後才把 checkpoint_line 寫回 ES 帳本；中途掛掉，下一次從 checkpoint 接著做，
  重做的部分因 seg_ver 守門變成 noop，不會重複或倒退
- 限速：每批前讀 backfill_control.max_docs_per_sec／paused（告警後可由人或控制程式調低）
- 多 task：每個 task 從 ES 帳本用條件更新（if_seq_no／if_primary_term）搶整個分區，task 之間不會重疊，也不用事先切 slice
- 閘門：G1 筆數守恆（比不重複 post_id，重抓的重複行不算）、G2 洩漏比例 ≤ 0.1%（網址殘片、電話、硬停用詞、必不可拆的詞；提案值，
  原本是 = 0）、G3 無寫入拒絕且線上正在用的欄位（CANARY_FIELD，預設 terms_v1）探針 p95 不惡化、G4 各渠道首月 PM 抽查
- 計畫書修正：認領加 attempts < 3（超過轉 failed，不再反覆重領）；心跳改背景執行緒每 20 秒（原本跟著每 500 篇的 bulk 走，
  一批慢了就被當成死掉）；G3 過了而且沒被降速就把全域限速回升 25%，最多回到 backfill_control.ceiling_docs_per_sec（原本只降不升）
"""
import argparse
import datetime as dt
import os
import re
import socket
import statistics
import threading
import time
from itertools import islice

import psycopg

from . import config, dictionary, promo, rawsrc
from .ledger import Ledger, now_iso
from .es import ES
from .logs import log
from .pipeline import base_doc, content_sha, seg_fields, seg_ver, segment_checked
from .rawsrc import load_partition_lines  # noqa: F401  舊的呼叫點與測試還用 job.load_partition_lines

CHUNK = int(os.environ.get("BACKFILL_CHUNK", "500"))
G2_MAX_RATIO = float(os.environ.get("G2_MAX_LEAK_RATIO", "0.001"))   # 提案值：每類洩漏 ÷ 分區篇數 ≤ 0.1%
CANARY_FIELD = os.environ.get("CANARY_FIELD", "terms_v1")              # 線上客戶現在查的欄位（A/B 期間是舊流程 v1）；回補寫 v2 不該拖慢它
HEARTBEAT_SEC = int(os.environ.get("BACKFILL_HEARTBEAT_SEC", "20"))
LEAK_REGEX = {
    "phone_like": "[0-9][0-9\\-]{6,}",
    "pure_number": "[0-9]+",
}
# 網址殘片不能用固定詞表在 ES 上抓：第一輪回補 45 個分區全被擋，查下去是 PTT 的 www（笑）、NET（服飾品牌）、PHP（話題），
# 都不是網址。改成「看原文」：用一套獨立於清洗規則的偵測器找出原文裡的網址片段，詞表裡出現這些片段、
# 且原文網址以外的地方沒有這個字，才算洩漏。
URL_SPAN_RE = re.compile(
    r"(?:https?://|www\.)[^\s，。、）)」』]+"
    r"|(?<![A-Za-z0-9@.-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|net|org|tw|cc|ly|io|me|gl|co|to|info)\b(?:/[^\s，。、）)」』]*)?",
    re.I)
PIECE_RE = re.compile(r"[A-Za-z0-9]{2,}")


def url_leaks(raw: str, terms: list) -> list:
    spans = URL_SPAN_RE.findall(raw or "")
    if not spans:
        return []
    pieces = {p.lower() for s in spans for p in PIECE_RE.findall(s)}
    outside = URL_SPAN_RE.sub(" ", raw).lower()
    return [t for t in terms if t.lower() in pieces and t.lower() not in outside]


def canary_p95(es: ES, channel: str, n: int = 5, field: str = None) -> float:
    """探針：跑 n 次該渠道的文字雲聚合（線上正在用的欄位），取 p95（毫秒，用 ES 回傳的 took）。"""
    body = {"size": 0, "query": {"term": {"channel": channel}},
            "aggs": {"t": {"terms": {"field": field or CANARY_FIELD, "size": 50}}}}
    took = [es.req("POST", f"/{es.index}/_search?request_cache=false", body)["took"] for _ in range(n)]
    return float(sorted(took)[max(0, int(round(0.95 * n)) - 1)])


def g3_limit(baseline: float) -> float:
    return min(max(baseline * 1.5, baseline + 50), 180_000)


def canary_after(conn, es: ES, channel: str, baseline: float, tries: int = 3):
    """G3 沒過不是重跑資料能解決的，是寫太快：自動把全域限速砍半、等 15 秒再量，最多 3 次。"""
    log_ = []
    for k in range(tries):
        p95 = canary_p95(es, channel)
        log_.append(p95)
        if p95 <= g3_limit(baseline) or k == tries - 1:
            return p95, log_
        with conn.transaction():
            c = conn.execute("SELECT max_docs_per_sec FROM backfill_control WHERE id=1 FOR UPDATE").fetchone()
            new = max(250, int(min(c["max_docs_per_sec"], 3000) / 2))
            conn.execute("UPDATE backfill_control SET max_docs_per_sec=%s, updated_at=now(), reason=%s WHERE id=1",
                         (new, f"G3 自動降速：{channel} 探針 p95 {p95:.0f} ms > 上限 {g3_limit(baseline):.0f} ms"))
        log("g3_auto_throttle", "WARNING", channel=channel, p95_ms=p95, limit_ms=g3_limit(baseline), new_max_docs_per_sec=new)
        time.sleep(15)
    return p95, log_


def g2_ratios(leaks: dict, split: dict, raw_count: int) -> dict:
    """每類洩漏 ÷ 分區篇數（必不可拆的詞也算一類）。"""
    n = max(1, raw_count or 0)
    out = {k: v / n for k, v in leaks.items()}
    out["must_keep_split"] = sum((split or {}).values()) / n
    return out


def g2_pass(ratios: dict, max_ratio: float = None) -> bool:
    return all(v <= (G2_MAX_RATIO if max_ratio is None else max_ratio) for v in ratios.values())


def _restart(led: Ledger, pid: str):
    """從頭來：checkpoint 與計數歸零，started_at 換成現在；回傳新的 started_at。"""
    started = dt.datetime.now(dt.timezone.utc)
    led.set(pid, {"checkpoint_line": 0, "written": 0, "noop": 0, "errors": 0, "started_at": started})
    return started


def fresh_snapshot(led: Ledger, part: dict, force: bool = False) -> dict:
    """決定這次讀原文的時間點（只有 bigquery 來源用得到）：
    - --force：從頭來。bigquery 的時間點換成現在（started_at 一起更新，之後被砍再續跑也讀同一個時間點）；
      file 和原本一樣只歸零，不動 started_at
    - started_at 超過 BigQuery 時光回溯期限（留 6 小時餘裕）：舊時間點查不到了，換成現在並從頭重跑；
      已寫過的篇因 seg_ver 守門變 noop，不會重複
    - 其他：沿用 started_at（第一次被領的時間），續跑時讀到的資料和順序跟上次一樣
    checkpoint_line 在 file 是「去重後第幾行」、在 bigquery 是「依 post_id 排序第幾篇」，兩者不通用：
    分區跑到一半換 RAW_SOURCE，一定要 --force 從頭來"""
    if force and config.RAW_SOURCE != "bigquery":
        led.set(part["partition_id"], {"checkpoint_line": 0, "written": 0, "noop": 0, "errors": 0})
        return {**part, "checkpoint_line": 0}
    if force:
        new_started = _restart(led, part["partition_id"])
        return {**part, "checkpoint_line": 0, "started_at": new_started}
    if config.RAW_SOURCE != "bigquery":
        return part
    limit = dt.timedelta(hours=config.BQ_TIME_TRAVEL_HOURS - rawsrc.SNAPSHOT_MARGIN_HOURS)
    started = part.get("started_at")
    if started is not None and dt.datetime.now(dt.timezone.utc) - started <= limit:
        return part
    new_started = _restart(led, part["partition_id"])
    log("raw_snapshot_expired", "WARNING", partition=part["partition_id"], started_at=str(started),
        checkpoint_line=part.get("checkpoint_line"), time_travel_hours=config.BQ_TIME_TRAVEL_HOURS)
    return {**part, "checkpoint_line": 0, "started_at": new_started}


class Heartbeat(threading.Thread):
    """背景每 20 秒更新 ES 帳本的心跳（用自己的 Ledger 連線）：原本跟著每 500 篇的 bulk 走，一批斷詞慢了就超過 2 分鐘而被當成死掉重領。"""

    def __init__(self, led: Ledger, partition_id: str, interval: int = HEARTBEAT_SEC):
        super().__init__(daemon=True, name=f"heartbeat-{partition_id}")
        self.led, self.pid, self.interval = Ledger(led.url, led.index), partition_id, interval
        self.stop_ev, self.beats = threading.Event(), 0

    def run(self):
        while not self.stop_ev.wait(self.interval):
            try:
                if self.led.heartbeat(self.pid):
                    self.beats += 1
            except Exception as e:
                log("heartbeat_failed", "WARNING", partition=self.pid, error=str(e)[:200])

    def stop(self):
        self.stop_ev.set()


def gates(es: ES, dic, part: dict, raw_count: int, rejected_delta: int, p95_before: float, p95_after: float, first_month: bool,
          split: dict = None, url_leak: dict = None, promo_suspects: list = None):
    es.req("POST", f"/{es.index}/_refresh")
    target = seg_ver("v2", dic.version)
    scope = [{"term": {"channel": part["channel"]}}, {"term": {"month": part["month"]}}]
    done = es.count({"bool": {"filter": scope + [{"range": {"seg_ver_v2": {"gte": target}}}]}})
    g1 = {"pass": done == raw_count, "raw": raw_count, "es_at_target": done, "basis": "distinct post_id"}
    leaks = {k: es.count({"bool": {"filter": scope + [{"regexp": {"terms_v2": v}}]}}) for k, v in LEAK_REGEX.items()}
    leaks["url_fragment"] = sum((url_leak or {}).values())
    leaks["hard_stopwords"] = es.count({"bool": {"filter": scope + [{"terms": {"terms_v2": sorted(dic.stopwords)}}]}})
    # 宣傳句殘留：清完之後，同一句話還出現在這一批太多篇貼文裡（promo.LineCounter；門檻 5%、至少 50 篇）。
    # 記的是「最常重複的那一句出現在幾篇」，一有就遠超過 0.1%，等於「有就不放行」；是哪幾句列在 promo_suspects 給人審
    leaks["promo_residue"] = promo.residue(promo_suspects or [])
    split = {k: v for k, v in (split or {}).items() if v}
    ratios = g2_ratios(leaks, split, raw_count)
    g2 = {"pass": g2_pass(ratios), **leaks, "must_keep_split": split, "ratios": {k: round(v, 6) for k, v in ratios.items()}, "max_ratio": G2_MAX_RATIO}
    g3 = {"pass": rejected_delta == 0 and p95_after <= g3_limit(p95_before),
          "write_rejected": rejected_delta, "canary_baseline_ms": p95_before, "canary_p95_ms_after": p95_after,
          "limit_ms": g3_limit(p95_before)}
    g4 = {"pass": not first_month, "pm_spot_check_required": first_month}
    return {"G1": g1, "G2": g2, "G3": g3, "G4": g4}


def use_es_text_if_edited(es: ES, chunk: list) -> list:
    """快照讀出來之後原文又改過的文章（ES 的 content_sha 和快照的不同）：改用 ES 現在的原文斷詞。
    不換的話 seg_guard 會擋下（斷的是舊原文，不准蓋新原文），這篇就停在舊版本，G1 對不上。"""
    ids = [p["post_id"] for p in chunk]
    docs = es.req("POST", f"/{es.index}/_mget?_source_includes=content_sha", {"ids": ids}).get("docs", [])
    sha = {d["_id"]: d["_source"].get("content_sha") for d in docs if d.get("found")}
    edited = [p["post_id"] for p in chunk if sha.get(p["post_id"]) and sha[p["post_id"]] != content_sha(p.get("text", ""))]
    if not edited:
        return chunk
    docs = es.req("POST", f"/{es.index}/_mget?_source_includes=content", {"ids": edited}).get("docs", [])
    text = {d["_id"]: d["_source"].get("content") or "" for d in docs if d.get("found")}
    log("raw_snapshot_edited", partition=chunk[0].get("channel", ""), use_es_text=len(text))
    return [{**p, "text": text[p["post_id"]]} if p["post_id"] in text else p for p in chunk]


def claim(led: Ledger, partition_id=None):
    """領一篇（規則見 ledger.py）：候選＝pending，或心跳超過 2 分鐘的 running；同一渠道在還沒有任何 passed 前只放行一篇（它就是要 PM 抽查的首月），
    其餘等 PM 簽核。搶用條件更新，搶輸就換下一篇；搶到後 refresh 再查一次渠道，發現同渠道也在跑就退回。
    attempts 達 MAX_ATTEMPTS 仍沒做完的先轉 failed（OOM 後反覆被重領要有人看），不再自動重領。"""
    for pid, attempts in led.exhaust(config.MAX_ATTEMPTS):
        log("attempts_exhausted", "ERROR", partition=pid, attempts=attempts, max_attempts=config.MAX_ATTEMPTS)
    worker = f"{socket.gethostname()}#{config.TASK_INDEX}"
    return led.claim(config.MAX_ATTEMPTS, worker, partition_id=partition_id)


def throttle(conn, led: Ledger):
    """回傳這個 task 的每秒篇數上限：全域上限 ÷ 正在跑的分區數（多個 task 一起分一個額度）。paused 就等。"""
    while True:
        c = conn.execute("SELECT max_docs_per_sec, paused FROM backfill_control WHERE id=1").fetchone()
        if not c["paused"]:
            return max(1, c["max_docs_per_sec"] / max(1, led.running_count(30)))
        time.sleep(2)


def run_partition(conn, led: Ledger, es: ES, dic, part, stop_after: int = None, force: bool = False):
    pid, channel, month = part["partition_id"], part["channel"], part["month"]
    if part["dict_version"] != dic.version or part["target_seg_ver"] != seg_ver("v2", dic.version):
        # 帳本記的是這一輪發版的目標；映像載到的字典不一致就不做，避免「以為補完了其實是別版」
        led.set(pid, {"status": "pending", "heartbeat_at": now_iso()}, refresh=True)
        log("dict_mismatch", "ERROR", partition=pid, ledger=part["dict_version"], loaded=dic.version)
        raise SystemExit(2)  # 設定錯了，整個 task 停下來，不要一直領同一個分區
    # force＝修完程式重跑：從頭來，同版本允許覆寫；bigquery 來源另外處理過期的讀取時間點
    part = fresh_snapshot(led, part, force)
    src = rawsrc.open_partition(channel, month, part.get("started_at"), page_size=CHUNK)
    raw_count, raw_lines = src.raw_count, src.raw_lines   # 不重複 post_id 數（G1 比這個；原始行數另記 raw_lines）
    led.set(pid, {"raw_count": raw_count, "raw_lines": raw_lines})
    hb = Heartbeat(led, pid)
    hb.start()
    first_month = not led.channel_has_passed(channel)
    base = conn.execute("SELECT canary_baseline_ms FROM backfill_control WHERE id=1").fetchone()["canary_baseline_ms"]
    p95_before = base if base is not None else canary_p95(es, channel)
    rej0 = es.write_rejected_total()
    start = part["checkpoint_line"]
    log("partition_start", partition=pid, raw=raw_count, raw_lines=raw_lines, resume_from=start, dict_version=dic.version, task=config.TASK_INDEX,
        seg_build=config.SEG_BUILD, force=force, canary_field=CANARY_FIELD, raw_source=config.RAW_SOURCE,
        raw_snapshot=str(part.get("started_at")) if config.RAW_SOURCE == "bigquery" else None,
        raw_null_ids=getattr(src, "null_ids", None))   # post_id 是 NULL 的列不讀、也不算進 raw_count，只算進 raw_lines
    if getattr(src, "null_ids", 0):
        log("raw_null_post_id", "WARNING", partition=pid, rows=src.null_ids)
    t_start, processed = time.perf_counter(), 0
    split, split_examples = {}, []  # 斷點續跑時只算這次處理到的部分；要完整數字就 --force 從頭跑
    url_leak, leak_examples = {}, []
    promo_rules = getattr(dic, "promo_rules", None)
    promo_counter = promo.LineCounter()
    # 宣傳句殘留看的是「整批裡同一句出現在幾篇」，只數續跑的那一段會漏（前段已達門檻、後段不到就放行）。
    # 所以續跑時把前面已寫完的那一段原文再數一遍（只數、不斷詞、不寫入）
    for post in islice(src.iter_posts(0), start):
        promo_counter.add(channel, post.get("text", ""), promo_rules)
    posts = src.iter_posts(start)
    i = start
    while i < raw_count:
        rps = throttle(conn, led)
        chunk = list(islice(posts, CHUNK))
        if not chunk:  # 原文比 raw_count 少（不該發生）：停在這裡，G1 會擋下來
            log("raw_stream_short", "ERROR", partition=pid, at_line=i, raw=raw_count)
            break
        t0 = time.perf_counter()
        items = []
        chunk = use_es_text_if_edited(es, chunk)
        for post in chunk:
            terms, bad = segment_checked(dic, channel, post.get("text", ""))
            promo_counter.add(channel, post.get("text", ""), promo_rules)
            for t in url_leaks(post.get("text", ""), terms):
                url_leak[t] = url_leak.get(t, 0) + 1
                if len(leak_examples) < 20:
                    leak_examples.append({"post_id": post["post_id"], "term": t})
            for w in bad:
                split[w] = split.get(w, 0) + 1
                if len(split_examples) < 20:
                    split_examples.append({"post_id": post["post_id"], "word": w})
            items.append((post["post_id"], base_doc(post), seg_fields(dic, terms, post.get("text", "")), dic.pipeline))
        seg_ms = (time.perf_counter() - t0) * 1000
        res = es.bulk_seg(items, force=force) if items else {"created": 0, "updated": 0, "noop": 0, "errors": 0, "rejected": 0, "took_ms": 0, "failed_ids": []}
        res.pop("failed_ids", None)
        if res["errors"]:
            hb.stop()
            led.progress(pid, checkpoint_line=i, errors=res["errors"])
            led.set(pid, {"status": "failed", "heartbeat_at": now_iso()}, refresh=True)
            log("partition_failed", "ERROR", partition=pid, at_line=i, **res)
            return False
        i += len(chunk)
        processed += len(items)
        led.progress(pid, checkpoint_line=i, written=res["created"] + res["updated"], noop=res["noop"])
        with conn.transaction():
            conn.execute("""INSERT INTO backfill_batches (partition_id, task_index, docs, written, noop, errors, rejected, seg_ms, bulk_ms)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                         (pid, config.TASK_INDEX, len(items), res["created"] + res["updated"], res["noop"], res["errors"], res["rejected"], seg_ms, res["took_ms"]))
        # 限速：這一批應該花多久
        should = len(chunk) / rps
        spent = time.perf_counter() - t0
        if spent < should:
            time.sleep(should - spent)
        if stop_after and processed >= stop_after:
            log("partition_interrupted", "WARNING", partition=pid, checkpoint_line=i)
            os._exit(137)  # 模擬 task 被砍（OOM／搶佔），不更新狀態，checkpoint 留在 ES 帳本
    elapsed = time.perf_counter() - t_start
    hb.stop()
    p95_after, p95_tries = canary_after(conn, es, channel, p95_before)
    suspects = promo_counter.suspects()
    g = gates(es, dic, part, raw_count, es.write_rejected_total() - rej0, p95_before, p95_after, first_month, split, url_leak, suspects)
    g["G3"]["canary_tries_ms"] = p95_tries
    g["G2"]["promo_list_lines"] = sum(len(v) for v in (promo_rules or {}).values())   # 這一版清單有幾句（0＝這一版沒帶清單）
    if suspects:
        g["G2"]["promo_suspects"] = suspects
    if split_examples:
        g["G2"]["must_keep_examples"] = split_examples
    if leak_examples:
        g["G2"]["url_leak_terms"] = url_leak
        g["G2"]["url_leak_examples"] = leak_examples
    ok = all(v["pass"] for k, v in g.items() if k != "G4")
    if g["G3"]["pass"] and len(p95_tries) == 1:
        g["G3"]["recovered_max_docs_per_sec"] = recover_throttle(conn, channel)
    status = ("awaiting_pm" if not g["G4"]["pass"] else "passed") if ok else "failed"
    dps = round(processed / elapsed, 1) if elapsed > 0 and processed else None
    led.set(pid, {"status": status, "gates": g, "finished_at": now_iso(), "heartbeat_at": now_iso(), "docs_per_sec": dps}, refresh=True)
    log("partition_done", partition=pid, status=status, docs_per_sec=dps, gates=g)
    return ok


def recover_throttle(conn, channel: str):
    """G3 過了而且這個分區沒被降速：全域限速回升 25%，最多回到 ceiling（原本只降不升，final_control 停在每秒 750 篇）。回傳新值（沒變回 None）。"""
    with conn.transaction():
        c = conn.execute("SELECT max_docs_per_sec, ceiling_docs_per_sec FROM backfill_control WHERE id=1 FOR UPDATE").fetchone()
        if not c or c["max_docs_per_sec"] >= c["ceiling_docs_per_sec"]:
            return None
        new = min(c["ceiling_docs_per_sec"], max(c["max_docs_per_sec"] + 1, int(c["max_docs_per_sec"] * 1.25)))
        conn.execute("UPDATE backfill_control SET max_docs_per_sec=%s, updated_at=now(), reason=%s WHERE id=1",
                     (new, f"G3 通過回升：{channel} 探針正常，{c['max_docs_per_sec']} → {new}（上限 {c['ceiling_docs_per_sec']}）"))
    log("g3_recover", channel=channel, max_docs_per_sec=new, ceiling=c["ceiling_docs_per_sec"])
    return new


def baseline(argv=None):
    """發版前量一次探針基準（沒有回補在跑的時候），寫進 backfill_control.canary_baseline_ms。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="ptt")
    ap.add_argument("--runs", type=int, default=20)
    a = ap.parse_args(argv)
    es = ES()
    p95 = canary_p95(es, a.channel, a.runs)
    with psycopg.connect(config.PG_DSN, autocommit=True) as conn:
        conn.execute("UPDATE backfill_control SET canary_baseline_ms=%s, updated_at=now() WHERE id=1", (p95,))
    log("canary_baseline", channel=a.channel, runs=a.runs, p95_ms=p95, limit_ms=g3_limit(p95), field=CANARY_FIELD)
    return 0


# 人工豁免（failed → 依 ledger.waive_plan 轉換）：首月不能靠豁免跳過 PM：G4 沒有記成已過，或這個渠道還沒有任何分區 passed，
# 豁免後停在 awaiting_pm，PM 簽完才 passed、才開放同渠道其他月份。api/app.py 的 /api/ops/approve 用同一支函式（ledger.py）。
# gates 是空的也照樣把豁免、簽核寫進去；finished_at 更新成豁免當下（「等 PM 多久」的監控從這裡起算）。
# 一次只豁免一個閘門：G1–G3 還有別的沒過又沒豁免，狀態留在 failed，要逐一豁免（或修好重跑）才會往下走，不能豁免一個就整批放行。
def approve(argv=None):
    """PM 抽查簽核：awaiting_pm → passed，簽核人與時間寫進 gates.G4。
    --waive：人工豁免某個沒過的閘門，failed → passed；首月豁免後是 awaiting_pm，還要 PM 簽；還有別的閘門沒過又沒豁免就留在 failed。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("partition")
    ap.add_argument("--by", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--waive", choices=("G1", "G2", "G3"), help="人工豁免某個沒過的閘門（例如 G2），一定要寫 --note 說明逐篇看過的結果")
    a = ap.parse_args(argv)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    led = Ledger(config.ES_URL, config.LEDGER_INDEX)
    if a.waive:
        if not a.note:
            raise SystemExit("--waive 一定要附 --note")
        st = led.waive(a.partition, a.waive, {"waived_by": a.by, "waived_at": now, "note": a.note})
        log("gate_waived" if st else "gate_waive_noop", partition=a.partition, gate=a.waive, by=a.by, status=st)
        return 0 if st else 1
    g4 = {"pass": True, "pm_spot_check_required": True, "approved_by": a.by, "note": a.note, "approved_at": now}
    ok = led.approve(a.partition, g4)
    log("pm_approved" if ok else "pm_approve_noop", partition=a.partition, by=a.by)
    return 0 if ok else 1


def plan(led: Ledger, dic):
    """建帳本：原文來源裡每個「渠道 × 月」一篇 pending；版本用這次載到的字典，和 run_partition 的檢查同一個來源。
    已經在帳本的不動（op_type=create 遇到既有 _id 就跳過），重跑安全。"""
    parts = rawsrc.list_partitions()
    target = seg_ver("v2", dic.version)
    docs = [{"partition_id": f"{ch}/{m}", "channel": ch, "month": m, "target_seg_ver": target, "dict_version": dic.version,
             "status": "pending", "checkpoint_line": 0, "written": 0, "noop": 0, "errors": 0, "attempts": 0} for ch, m in parts]
    res = led.create_many(docs) if docs else {"created": 0, "exists": 0}
    log("ledger_planned", partitions=len(parts), added=res["created"], dict_version=dic.version, source=config.RAW_SOURCE)
    return res["created"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--partition")
    ap.add_argument("--max-partitions", type=int, default=10 ** 6)
    ap.add_argument("--stop-after", type=int, help="處理這麼多篇後模擬 task 被砍（測斷點續跑）")
    ap.add_argument("--force", action="store_true", help="同版本也覆寫（只配 --partition，用在閘門失敗、修完程式後重跑）")
    ap.add_argument("--plan", action="store_true", help="只建帳本（原文來源的每個渠道 × 月一篇 pending）就結束，不領分區")
    a = ap.parse_args(argv)
    rawsrc.check_config()
    dic = dictionary.load()
    led = Ledger(config.ES_URL, config.LEDGER_INDEX)
    led.ensure_index()
    if a.plan:
        plan(led, dic)
        return
    es = ES()
    es.put_guard_script()
    conn = psycopg.connect(config.PG_DSN, autocommit=True)
    conn.row_factory = psycopg.rows.dict_row
    n = 0
    while n < a.max_partitions:
        part = claim(led, a.partition)
        if not part:
            break
        run_partition(conn, led, es, dic, part, a.stop_after, a.force and bool(a.partition))
        n += 1
        if a.partition:
            break
    log("job_done", partitions=n, dict_version=dic.version)
