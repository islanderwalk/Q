"""回補 Job（Cloud Run Jobs）：一次領一個分區（渠道 × 月，由新到舊），讀原文 → 斷詞 → 條件寫入 → 過閘門。

- 原文來源：RAW_ROOT/<channel>/<yyyy-mm>.jsonl（GCS／BigQuery 匯出的本機替身；設計上不從 ES scroll，避免和線上查詢搶）
- 斷點續跑：每個 bulk 寫成功後才把 checkpoint_line 寫回 PostgreSQL；中途掛掉，下一次從 checkpoint 接著做，
  重做的部分因 seg_ver 守門變成 noop，不會重複或倒退
- 限速：每批前讀 backfill_control.max_docs_per_sec／paused（告警後可由人或控制程式調低）
- 多 task：每個 task 從帳本 FOR UPDATE SKIP LOCKED 領整個分區，task 之間不會重疊，也不用事先切 slice
- 閘門：G1 筆數守恆、G2 洩漏 = 0（網址殘片、電話、硬停用詞）、G3 無寫入拒絕且探針查詢 p95 不惡化、G4 各渠道首月 PM 抽查
"""
import argparse
import json
import os
import re
import socket
import statistics
import time
from pathlib import Path

import psycopg

from . import config, dictionary
from .es import ES
from .logs import log
from .pipeline import base_doc, seg_fields, seg_ver, segment_checked

CHUNK = int(os.environ.get("BACKFILL_CHUNK", "500"))
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


def canary_p95(es: ES, channel: str, n: int = 5) -> float:
    """探針：跑 n 次該渠道的文字雲聚合，取 p95（毫秒，用 ES 回傳的 took）。"""
    body = {"size": 0, "query": {"term": {"channel": channel}},
            "aggs": {"t": {"terms": {"field": "terms_v2", "size": 50}}}}
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


def gates(es: ES, dic, part: dict, raw_count: int, rejected_delta: int, p95_before: float, p95_after: float, first_month: bool,
          split: dict = None, url_leak: dict = None):
    es.req("POST", f"/{es.index}/_refresh")
    target = seg_ver("v2", dic.version)
    scope = [{"term": {"channel": part["channel"]}}, {"term": {"month": part["month"]}}]
    done = es.count({"bool": {"filter": scope + [{"range": {"seg_ver_v2": {"gte": target}}}]}})
    g1 = {"pass": done == raw_count, "raw": raw_count, "es_at_target": done}
    leaks = {k: es.count({"bool": {"filter": scope + [{"regexp": {"terms_v2": v}}]}}) for k, v in LEAK_REGEX.items()}
    leaks["url_fragment"] = sum((url_leak or {}).values())
    leaks["hard_stopwords"] = es.count({"bool": {"filter": scope + [{"terms": {"terms_v2": sorted(dic.stopwords)}}]}})
    split = {k: v for k, v in (split or {}).items() if v}
    g2 = {"pass": all(v == 0 for v in leaks.values()) and not split, **leaks, "must_keep_split": split}
    g3 = {"pass": rejected_delta == 0 and p95_after <= g3_limit(p95_before),
          "write_rejected": rejected_delta, "canary_baseline_ms": p95_before, "canary_p95_ms_after": p95_after,
          "limit_ms": g3_limit(p95_before)}
    g4 = {"pass": not first_month, "pm_spot_check_required": first_month}
    return {"G1": g1, "G2": g2, "G3": g3, "G4": g4}


def claim(conn, partition_id=None):
    """領一個分區。某渠道還沒有任何分區 passed 時，只放行一個（它就是要 PM 抽查的首月），其餘等 PM 簽核。"""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(6006)")  # 多個 task 同時領，序列化這一小段
        if partition_id:
            row = conn.execute("SELECT partition_id FROM backfill_ledger WHERE partition_id=%s FOR UPDATE", (partition_id,)).fetchone()
        else:
            # failed 不自動重領：要人看過閘門結果、修好之後用 --partition（必要時加 --force）重跑
            row = conn.execute("""SELECT partition_id FROM backfill_ledger l WHERE status IN ('pending','running')
                                  AND (status <> 'running' OR heartbeat_at IS NULL OR heartbeat_at < now() - interval '2 minutes')
                                  AND (EXISTS (SELECT 1 FROM backfill_ledger p WHERE p.channel = l.channel AND p.status = 'passed')
                                       OR NOT EXISTS (SELECT 1 FROM backfill_ledger p WHERE p.channel = l.channel
                                                      AND p.partition_id <> l.partition_id
                                                      AND p.status IN ('running', 'awaiting_pm', 'failed')))
                                  ORDER BY month DESC, channel LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
        if not row:
            return None
        conn.execute("""UPDATE backfill_ledger SET status='running', worker=%s, attempts=attempts+1,
                        started_at=COALESCE(started_at, now()), heartbeat_at=now() WHERE partition_id=%s""",
                     (f"{socket.gethostname()}#{config.TASK_INDEX}", row["partition_id"]))
        return conn.execute("SELECT * FROM backfill_ledger WHERE partition_id=%s", (row["partition_id"],)).fetchone()


def throttle(conn):
    """回傳這個 task 的每秒篇數上限：全域上限 ÷ 正在跑的分區數（多個 task 一起分一個額度）。"""
    while True:
        c = conn.execute("""SELECT max_docs_per_sec, paused,
                                   (SELECT count(*) FROM backfill_ledger WHERE status='running'
                                     AND heartbeat_at > now() - interval '30 seconds') AS n
                            FROM backfill_control WHERE id=1""").fetchone()
        if not c["paused"]:
            return max(1, c["max_docs_per_sec"] / max(1, c["n"]))
        time.sleep(2)


def run_partition(conn, es: ES, dic, part, stop_after: int = None, force: bool = False):
    pid, channel, month = part["partition_id"], part["channel"], part["month"]
    if part["dict_version"] != dic.version or part["target_seg_ver"] != seg_ver("v2", dic.version):
        # 帳本記的是這一輪發版的目標；映像載到的字典不一致就不做，避免「以為補完了其實是別版」
        conn.execute("UPDATE backfill_ledger SET status='pending', heartbeat_at=now() WHERE partition_id=%s", (pid,))
        log("dict_mismatch", "ERROR", partition=pid, ledger=part["dict_version"], loaded=dic.version)
        raise SystemExit(2)  # 設定錯了，整個 task 停下來，不要一直領同一個分區
    if force:  # 修完程式重跑：從頭來，同版本允許覆寫
        conn.execute("UPDATE backfill_ledger SET checkpoint_line=0, written=0, noop=0, errors=0 WHERE partition_id=%s", (pid,))
        part = {**part, "checkpoint_line": 0}
    raw = Path(config.RAW_ROOT) / channel / f"{month}.jsonl"
    lines = raw.read_text(encoding="utf-8").splitlines()
    raw_count = len(lines)
    conn.execute("UPDATE backfill_ledger SET raw_count=%s WHERE partition_id=%s", (raw_count, pid))
    first_month = conn.execute("SELECT count(*) AS n FROM backfill_ledger WHERE channel=%s AND status='passed'", (channel,)).fetchone()["n"] == 0
    base = conn.execute("SELECT canary_baseline_ms FROM backfill_control WHERE id=1").fetchone()["canary_baseline_ms"]
    p95_before = base if base is not None else canary_p95(es, channel)
    rej0 = es.write_rejected_total()
    start = part["checkpoint_line"]
    log("partition_start", partition=pid, raw=raw_count, resume_from=start, dict_version=dic.version, task=config.TASK_INDEX,
        seg_build=config.SEG_BUILD, force=force)
    t_start, processed = time.perf_counter(), 0
    split, split_examples = {}, []  # 斷點續跑時只算這次處理到的部分；要完整數字就 --force 從頭跑
    url_leak, leak_examples = {}, []
    i = start
    while i < raw_count:
        rps = throttle(conn)
        chunk = lines[i:i + CHUNK]
        t0 = time.perf_counter()
        items = []
        for line in chunk:
            post = json.loads(line)
            terms, bad = segment_checked(dic, channel, post.get("text", ""))
            for t in url_leaks(post.get("text", ""), terms):
                url_leak[t] = url_leak.get(t, 0) + 1
                if len(leak_examples) < 20:
                    leak_examples.append({"post_id": post["post_id"], "term": t})
            for w in bad:
                split[w] = split.get(w, 0) + 1
                if len(split_examples) < 20:
                    split_examples.append({"post_id": post["post_id"], "word": w})
            items.append((post["post_id"], base_doc(post), seg_fields(dic, terms), dic.pipeline))
        seg_ms = (time.perf_counter() - t0) * 1000
        res = es.bulk_seg(items, force=force) if items else {"created": 0, "updated": 0, "noop": 0, "errors": 0, "rejected": 0, "took_ms": 0, "failed_ids": []}
        res.pop("failed_ids", None)
        if res["errors"]:
            conn.execute("UPDATE backfill_ledger SET status='failed', errors=errors+%s, heartbeat_at=now() WHERE partition_id=%s", (res["errors"], pid))
            log("partition_failed", "ERROR", partition=pid, at_line=i, **res)
            return False
        i += len(chunk)
        processed += len(items)
        with conn.transaction():
            conn.execute("""UPDATE backfill_ledger SET checkpoint_line=%s, written=written+%s, noop=noop+%s, heartbeat_at=now()
                            WHERE partition_id=%s""", (i, res["created"] + res["updated"], res["noop"], pid))
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
            os._exit(137)  # 模擬 task 被砍（OOM／搶佔），不更新狀態，checkpoint 留在 PG
    elapsed = time.perf_counter() - t_start
    p95_after, p95_tries = canary_after(conn, es, channel, p95_before)
    g = gates(es, dic, part, raw_count, es.write_rejected_total() - rej0, p95_before, p95_after, first_month, split, url_leak)
    g["G3"]["canary_tries_ms"] = p95_tries
    if split_examples:
        g["G2"]["must_keep_examples"] = split_examples
    if leak_examples:
        g["G2"]["url_leak_terms"] = url_leak
        g["G2"]["url_leak_examples"] = leak_examples
    ok = all(v["pass"] for k, v in g.items() if k != "G4")
    status = ("awaiting_pm" if not g["G4"]["pass"] else "passed") if ok else "failed"
    dps = round(processed / elapsed, 1) if elapsed > 0 and processed else None
    conn.execute("""UPDATE backfill_ledger SET status=%s, gates=%s, finished_at=now(), heartbeat_at=now(), docs_per_sec=%s
                    WHERE partition_id=%s""", (status, json.dumps(g, ensure_ascii=False), dps, pid))
    log("partition_done", partition=pid, status=status, docs_per_sec=dps, gates=g)
    return ok


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
    log("canary_baseline", channel=a.channel, runs=a.runs, p95_ms=p95, limit_ms=g3_limit(p95))
    return 0


def approve(argv=None):
    """PM 抽查簽核：awaiting_pm → passed，簽核人與時間寫進 gates.G4。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("partition")
    ap.add_argument("--by", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--waive", help="人工豁免某個沒過的閘門（例如 G2），一定要寫 --note 說明逐篇看過的結果")
    a = ap.parse_args(argv)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with psycopg.connect(config.PG_DSN, autocommit=True) as conn:
        if a.waive:
            if not a.note:
                raise SystemExit("--waive 一定要附 --note")
            w = json.dumps({"waived_by": a.by, "waived_at": now, "note": a.note}, ensure_ascii=False)
            n = conn.execute("""UPDATE backfill_ledger SET status='passed', gates=jsonb_set(gates, %s, %s::jsonb)
                                WHERE partition_id=%s AND status='failed'""", ([a.waive, "waiver"], w, a.partition)).rowcount
            log("gate_waived" if n else "gate_waive_noop", partition=a.partition, gate=a.waive, by=a.by)
            return 0 if n else 1
        g4 = json.dumps({"pass": True, "pm_spot_check_required": True, "approved_by": a.by, "note": a.note,
                         "approved_at": now}, ensure_ascii=False)
        n = conn.execute("""UPDATE backfill_ledger SET status='passed', gates=jsonb_set(gates, '{G4}', %s::jsonb)
                            WHERE partition_id=%s AND status='awaiting_pm'""", (g4, a.partition)).rowcount
    log("pm_approved" if n else "pm_approve_noop", partition=a.partition, by=a.by)
    return 0 if n else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--partition")
    ap.add_argument("--max-partitions", type=int, default=10 ** 6)
    ap.add_argument("--stop-after", type=int, help="處理這麼多篇後模擬 task 被砍（測斷點續跑）")
    ap.add_argument("--force", action="store_true", help="同版本也覆寫（只配 --partition，用在閘門失敗、修完程式後重跑）")
    a = ap.parse_args(argv)
    dic = dictionary.load()
    es = ES()
    es.put_guard_script()
    conn = psycopg.connect(config.PG_DSN, autocommit=True)
    conn.row_factory = psycopg.rows.dict_row
    n = 0
    while n < a.max_partitions:
        part = claim(conn, a.partition)
        if not part:
            break
        run_partition(conn, es, dic, part, a.stop_after, a.force and bool(a.partition))
        n += 1
        if a.partition:
            break
    log("job_done", partitions=n, dict_version=dic.version)
