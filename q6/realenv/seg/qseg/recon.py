"""完整性四層對帳（計畫書 P0 第 2 項）＋回補燃盡（P0 第 8 項）：定時算、寫 recon_daily、給 Prometheus 抓。
用法：python -m qseg recon [--once] [--interval 3600]；常駐時在 RECON_PORT（預設 9112）提供 /metrics。GCP 上同一段查詢改由排程 Job 推到 Cloud Monitoring。

每個「渠道 × 月」四個數字要對得起來：
  L1 原文湖：RAW_ROOT/_manifest.json 的 by_month（scripts/prepare_raw.py 去重後的 post_id 數；回補 Job 的 G1 也比這個）
  L2 ES 篇數：posts 索引裡 channel／month 的文件數（含 tombstone；另記 es_deleted）
  L3 有 terms_v2 的篇數
  L4 在最新 seg_ver_v2 的篇數（全索引最大的 seg_ver_v2；某一版沒套用完就看得到）
帳本層：backfill_ledger 的 written＋noop 與狀態。
  missing_ratio ＝ max(0, L1 −（L2 − 已刪）) ÷ L1：原文有、ES 沒有（採集掉資料、回補漏批）；只有 L1 存在的分區才算
  extra_docs ＝ max(0,（L2 − 已刪）− L1)：ES 有、原文沒有（串流進來的、重複 post_id、已刪文章復活）；含今天的月份正常會 > 0
month=unknown（pipeline.month_of 產生、不在 270 批裡）單獨一列。
回補燃盡：剩餘篇數（pending／running 的 raw_count − checkpoint_line）、近 15 分鐘速率（backfill_batches）、attempts ≥ 3 的分區數、最老 awaiting_pm 等了多久。
"""
import json
import time
from datetime import date
from pathlib import Path

from . import config, metrics
from .logs import log

LAYERS = ("raw", "es", "es_with_v2", "es_at_latest_v2", "es_deleted", "ledger")

DDL = """
CREATE TABLE IF NOT EXISTS recon_daily (
  day date NOT NULL,
  channel text NOT NULL,
  month text NOT NULL,
  raw_docs integer,
  es_docs integer NOT NULL,
  es_with_v2 integer NOT NULL,
  es_at_latest_v2 integer NOT NULL,
  es_deleted integer NOT NULL DEFAULT 0,
  ledger_written integer,
  ledger_noop integer,
  ledger_status text,
  latest_seg_ver_v2 bigint,
  missing_ratio numeric,
  extra_docs integer,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, channel, month)
);
"""


def max_body() -> dict:
    return {"size": 0, "aggs": {"max_v2": {"max": {"field": "seg_ver_v2"}}}}


def recon_body(latest_seg_ver) -> dict:
    """一次 _search：渠道 → 月份 bucket，底下三個 filter 子聚合；latest 用全索引最大 seg_ver_v2（沒有就不算）。"""
    sub = {
        # 「有 v2」看 seg_ver_v2 而不是 terms_v2：ES 的 exists 對空陣列算「沒有值」（真實環境驗證：8 篇標題只剩符號的文章
        # terms_v2 是 []、seg_ver_v2 有值），用 terms_v2 會把它們算成漏斷詞、ReconV2CoverageGap 誤響
        "with_v2": {"filter": {"exists": {"field": "seg_ver_v2"}}},
        "deleted": {"filter": {"exists": {"field": "deleted_at"}}},
    }
    if latest_seg_ver is not None:
        sub["latest"] = {"filter": {"range": {"seg_ver_v2": {"gte": latest_seg_ver}}}}
    return {"size": 0, "aggs": {"by_channel": {"terms": {"field": "channel", "size": 50, "missing": "(無渠道)"},
                                              "aggs": {"by_month": {"terms": {"field": "month", "size": 400, "missing": "unknown"}, "aggs": sub}}}}}


def parse(res: dict) -> dict:
    """{(channel, month): {es, es_with_v2, es_at_latest_v2, es_deleted}}"""
    out = {}
    for c in res["aggregations"]["by_channel"]["buckets"]:
        for m in c["by_month"]["buckets"]:
            out[(c["key"], m["key"])] = {"es": m["doc_count"], "es_with_v2": m["with_v2"]["doc_count"],
                                         "es_at_latest_v2": m["latest"]["doc_count"] if "latest" in m else 0,
                                         "es_deleted": m["deleted"]["doc_count"]}
    return out


def load_manifest(raw_root: str) -> dict:
    """{(channel, month): 不重複 post_id 數}；沒有 manifest 回空（原文層就全是 NULL，missing_ratio 不算）。"""
    p = Path(raw_root) / "_manifest.json"
    if not p.exists():
        return {}
    by = json.loads(p.read_text(encoding="utf-8")).get("by_month", {})
    out = {}
    for k, n in by.items():
        ch, _, month = k.partition("/")
        out[(ch, month)] = int(n)
    return out


def rows_of(es_counts: dict, raw: dict, ledger: dict, latest_seg_ver) -> list:
    """合併三個來源成每個分區一列；算 missing_ratio 與 extra_docs。"""
    rows = []
    for key in sorted(set(es_counts) | set(raw) | set(ledger)):
        e = es_counts.get(key, {"es": 0, "es_with_v2": 0, "es_at_latest_v2": 0, "es_deleted": 0})
        r = raw.get(key)
        l = ledger.get(key, {})
        alive = e["es"] - e["es_deleted"]
        missing_ratio = (max(0, r - alive) / r) if r else None
        extra = max(0, alive - r) if r is not None else None
        rows.append({"channel": key[0], "month": key[1], "raw_docs": r, **e, "ledger_written": l.get("written"), "ledger_noop": l.get("noop"),
                     "ledger_status": l.get("status"), "latest_seg_ver_v2": latest_seg_ver, "missing_ratio": missing_ratio, "extra_docs": extra})
    return rows


_LAST_KEYS = set()


def publish(rows: list):
    """寫 gauge；上一輪有、這一輪沒有的分區移除序列。"""
    global _LAST_KEYS
    seen = set()
    for r in rows:
        ch, m = r["channel"], r["month"]
        seen.add((ch, m))
        for layer, val in (("raw", r["raw_docs"]), ("es", r["es"]), ("es_with_v2", r["es_with_v2"]), ("es_at_latest_v2", r["es_at_latest_v2"]),
                           ("es_deleted", r["es_deleted"]), ("ledger", (r["ledger_written"] or 0) + (r["ledger_noop"] or 0) if r["ledger_status"] else None)):
            if val is None:
                try:
                    metrics.RECON_DOCS.remove(layer, ch, m)
                except KeyError:
                    pass
            else:
                metrics.RECON_DOCS.labels(layer, ch, m).set(val)
        if r["missing_ratio"] is None:
            try:
                metrics.RECON_MISSING.remove(ch, m)
            except KeyError:
                pass
        else:
            metrics.RECON_MISSING.labels(ch, m).set(r["missing_ratio"])
        metrics.RECON_EXTRA.labels(ch, m).set(r["extra_docs"] or 0)
        metrics.RECON_PASSED.labels(ch, m).set(1 if r["ledger_status"] == "passed" else 0)
    for gone in _LAST_KEYS - seen:
        for layer in LAYERS:
            try:
                metrics.RECON_DOCS.remove(layer, *gone)
            except KeyError:
                pass
        for g in (metrics.RECON_MISSING, metrics.RECON_EXTRA, metrics.RECON_PASSED):
            try:
                g.remove(*gone)
            except KeyError:
                pass
    _LAST_KEYS = seen
    metrics.RECON_OK.set(time.time())


LEDGER_SQL = "SELECT channel, month, status, written, noop, raw_count, checkpoint_line, attempts, finished_at FROM backfill_ledger"
BURNDOWN_SQL = """
SELECT (SELECT count(*) FROM backfill_ledger WHERE status NOT IN ('passed') AND attempts >= %s) AS exhausted,
       (SELECT extract(epoch FROM now() - min(finished_at)) FROM backfill_ledger WHERE status='awaiting_pm') AS oldest_awaiting,
       (SELECT coalesce(sum(greatest(coalesce(raw_count, 0) - checkpoint_line, 0)), 0) FROM backfill_ledger WHERE status IN ('pending','running')) AS remaining,
       (SELECT coalesce(sum(docs), 0) FROM backfill_batches WHERE at > now() - interval '15 minutes') / 900.0 AS rate_15m,
       (SELECT max_docs_per_sec FROM backfill_control WHERE id=1) AS max_dps,
       (SELECT paused FROM backfill_control WHERE id=1) AS paused
"""


CONVERGE_SQL = """SELECT pipeline, count(*) AS alive, count(DISTINCT dict_version) AS versions FROM service_state
                  WHERE heartbeat_at > now() - interval '60 seconds' GROUP BY pipeline"""
RERUN_SQL = """SELECT status, count(*) AS n, max(extract(epoch FROM now() - coalesce(started_at, queued_at))) FILTER (WHERE status IN ('loading','running')) AS age
               FROM rerun_runs WHERE queued_at > now() - interval '7 days' GROUP BY status"""


def convergence(conn):
    """字典發版收斂（P0 第 7 項）：1 分鐘內有心跳的執行個體有幾個不同的 dict_version；定向重跑跑了多久；outbox 還有幾則沒送。"""
    seen = set()
    for pipeline, alive, versions in conn.execute(CONVERGE_SQL).fetchall():
        seen.add(pipeline)
        metrics.SERVICE_ALIVE.labels(pipeline).set(alive)
        metrics.SERVICE_VERSIONS.labels(pipeline).set(versions)
    for p in ("v0", "v1", "v2"):
        if p not in seen:
            metrics.SERVICE_ALIVE.labels(p).set(0)
            metrics.SERVICE_VERSIONS.labels(p).set(0)
    age = 0.0
    counts = {}
    for status, n, a in conn.execute(RERUN_SQL).fetchall():
        counts[status] = n
        if a is not None:
            age = max(age, float(a))
    for st in ("queued", "loading", "running", "done", "failed", "skipped"):
        metrics.RERUN_RUNS.labels(st).set(counts.get(st, 0))
    metrics.RERUN_RUNNING_AGE.set(age)
    try:
        row = conn.execute("SELECT count(*) FROM release_outbox WHERE status='pending'").fetchone()
        metrics.RELEASE_OUTBOX_PENDING.set(row[0] if row else 0)
    except Exception:   # 表還沒建（API 沒起過）
        metrics.RELEASE_OUTBOX_PENDING.set(0)


def ledger_and_burndown(conn, max_attempts: int = 3):
    """帳本層（每分區）＋燃盡 gauge。"""
    ledger = {}
    for ch, month, status, written, noop, raw_count, cp, attempts, fin in conn.execute(LEDGER_SQL).fetchall():
        ledger[(ch, month)] = {"status": status, "written": written, "noop": noop, "raw_count": raw_count}
    by_status = {}
    for l in ledger.values():
        by_status[l["status"]] = by_status.get(l["status"], 0) + 1
    for st in ("pending", "running", "awaiting_pm", "passed", "failed"):
        metrics.LEDGER_PARTS.labels(st).set(by_status.get(st, 0))
    row = conn.execute(BURNDOWN_SQL, (max_attempts,)).fetchone()
    exhausted, oldest, remaining, rate, max_dps, paused = row
    metrics.LEDGER_EXHAUSTED.set(exhausted or 0)
    metrics.LEDGER_OLDEST_PM.set(float(oldest) if oldest is not None else 0)
    metrics.BACKFILL_REMAINING.set(int(remaining or 0))
    metrics.BACKFILL_RATE.set(float(rate or 0))
    metrics.BACKFILL_MAX_DPS.set(int(max_dps or 0))
    metrics.BACKFILL_PAUSED.set(1 if paused else 0)
    return ledger


def save(conn, rows: list):
    conn.execute(DDL)
    today = date.today()
    for r in rows:
        conn.execute("""INSERT INTO recon_daily (day, channel, month, raw_docs, es_docs, es_with_v2, es_at_latest_v2, es_deleted, ledger_written, ledger_noop,
                                                 ledger_status, latest_seg_ver_v2, missing_ratio, extra_docs, computed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                        ON CONFLICT (day, channel, month) DO UPDATE SET raw_docs=EXCLUDED.raw_docs, es_docs=EXCLUDED.es_docs, es_with_v2=EXCLUDED.es_with_v2,
                          es_at_latest_v2=EXCLUDED.es_at_latest_v2, es_deleted=EXCLUDED.es_deleted, ledger_written=EXCLUDED.ledger_written,
                          ledger_noop=EXCLUDED.ledger_noop, ledger_status=EXCLUDED.ledger_status, latest_seg_ver_v2=EXCLUDED.latest_seg_ver_v2,
                          missing_ratio=EXCLUDED.missing_ratio, extra_docs=EXCLUDED.extra_docs, computed_at=now()""",
                     (today, r["channel"], r["month"], r["raw_docs"], r["es"], r["es_with_v2"], r["es_at_latest_v2"], r["es_deleted"],
                      r["ledger_written"], r["ledger_noop"], r["ledger_status"], r["latest_seg_ver_v2"], r["missing_ratio"], r["extra_docs"]))


def run_once(es, conn) -> list:
    mx = es.req("POST", f"/{es.index}/_search?request_cache=false", max_body(), timeout=120)["aggregations"]["max_v2"]["value"]
    latest = int(mx) if mx is not None else None
    res = es.req("POST", f"/{es.index}/_search?request_cache=false", recon_body(latest), timeout=300)
    es_counts = parse(res)
    raw = load_manifest(config.RAW_ROOT)
    ledger = ledger_and_burndown(conn, config.MAX_ATTEMPTS)
    convergence(conn)
    rows = rows_of(es_counts, raw, ledger, latest)
    save(conn, rows)
    publish(rows)
    worst = max((r["missing_ratio"] or 0) for r in rows) if rows else 0
    log("recon", partitions=len(rows), latest_seg_ver_v2=latest, worst_missing_ratio=round(worst, 6),
        unknown_month_docs=sum(r["es"] for r in rows if r["month"] == "unknown"))
    return rows


HEARTBEAT_TICK = 60.0   # 心跳類 gauge（qseg_service_instances_alive／dict_version 收斂／outbox）每分鐘刷新；全量對帳才是每小時


def idle(interval: float, tick: float, refresh) -> int:
    """等下一次全量對帳的這段時間，每 tick 秒呼叫一次 refresh（心跳類 gauge）。回傳呼叫次數。
    真實環境驗證抓到的問題：alive 只在每小時的全量對帳算一次，服務剛起來那一刻算到 0，SegNoAliveInstances（page）就響了一小時。
    refresh 失敗只記 log，不中斷等待。"""
    deadline, n = time.monotonic() + interval, 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return n
        time.sleep(min(tick, remaining))
        try:
            refresh()
            n += 1
        except Exception as e:
            log("recon_heartbeat_failed", "WARNING", error=str(e)[:200])


def main(args):
    import psycopg

    from .es import ES

    once = "--once" in args
    interval = 3600.0
    if "--interval" in args:
        interval = float(args[args.index("--interval") + 1])
    es = ES()
    if not once:
        metrics.serve(config.RECON_PORT, "recon")

    def refresh_heartbeats():
        with psycopg.connect(config.PG_DSN, autocommit=True) as conn:
            convergence(conn)

    while True:
        try:
            with psycopg.connect(config.PG_DSN, autocommit=True) as conn:
                run_once(es, conn)
        except Exception as e:  # 失敗不能讓常駐程序死掉；錯誤要看得見（RECON_OK 不更新 → ReconStale 會響）
            metrics.RECON_ERR.inc()
            log("recon_failed", "ERROR", error=str(e)[:300])
            if once:
                return 1
        if once:
            return 0
        idle(interval, HEARTBEAT_TICK, refresh_heartbeats)
