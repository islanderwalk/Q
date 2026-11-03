"""定向重跑：字典只「加詞」時，只重算原文含新詞字串的文章，不必全量回補。

成立的前提是 jieba 的 total 固定（manifest.total_pinned）：加詞不會改到不含該詞的文章（impact.py 用全量資料驗證）。
找文章用 ES 的 content.bigram 片語比對（中文兩字一組，等同「原文含這個字串」），只挑 seg_ver_v2 還比目標舊的，
重算後走同一支 seg_guard 條件更新：版本只前進，重跑中斷再跑也不會重複或倒退。

兩種進入點：命令列 `python -m qseg rerun --words 肥宅`（回補 Job 的用法），
或 control.py 收到發版訊息後在串流服務裡呼叫 run_rerun（PM 頁發版的用法），兩邊同一段程式。

計畫書修正：定向重跑也讀 backfill_control（限速、暫停）與回補共用額度，結束後也量 G3 探針（線上欄位 p95 不惡化，超過就把全域限速砍半）。
"""
import argparse
import json
import time

import psycopg

from . import config, dictionary, metrics
from .es import ES
from .logs import log
from .pipeline import seg_fields, seg_ver, segment


def rate_limit(conn):
    """回傳這一批的每秒篇數上限：全域上限 ÷（正在跑的回補分區數 ＋ 1）；paused 就等。conn 要 dict_row。"""
    from .job import throttle   # 與回補 Job 同一段：共用 backfill_control 的額度
    row = conn.execute("""SELECT max_docs_per_sec, paused, (SELECT count(*) FROM backfill_ledger WHERE status='running'
                            AND heartbeat_at > now() - interval '30 seconds') AS n FROM backfill_control WHERE id=1""").fetchone()
    if row is None:
        return None
    if row["paused"]:
        return throttle(conn)
    return max(1, row["max_docs_per_sec"] / (max(0, row["n"]) + 1))


def run_rerun(dic, es: ES, words: list, batch: int = 500, on_progress=None, record_release: bool = True, limit: bool = True) -> dict:
    words = [w.strip() for w in words if w and w.strip()]
    conn = None
    if limit or record_release:
        try:
            conn = psycopg.connect(config.PG_DSN, autocommit=True, row_factory=psycopg.rows.dict_row)
        except Exception as e:   # PG 不在：不限速也不記錄，但重跑照做（日誌看得到）
            log("rerun_pg_unavailable", "WARNING", error=str(e)[:200])
            conn = None
    p95_before = None
    if conn is not None and limit:
        from .job import canary_p95
        base = conn.execute("SELECT canary_baseline_ms FROM backfill_control WHERE id=1").fetchone()
        p95_before = base["canary_baseline_ms"] if base and base["canary_baseline_ms"] is not None else canary_p95(es, "ptt")
    target = seg_ver("v2", dic.version)
    q = {"bool": {"should": [{"match_phrase": {"content.bigram": w}} for w in words], "minimum_should_match": 1,
                  "filter": [{"range": {"seg_ver_v2": {"lt": target}}}]}}
    estimate = es.count(q)
    log("rerun_start", dict_version=dic.version, words=words, candidates=estimate, seg_build=config.SEG_BUILD)
    pit = es.req("POST", f"/{es.index}/_pit?keep_alive=2m")["id"]
    after, scanned, substring, changed = None, 0, 0, 0
    res = {"created": 0, "updated": 0, "noop": 0, "errors": 0, "rejected": 0}
    examples = []
    t0 = time.perf_counter()
    while True:
        body = {"size": batch, "query": q, "pit": {"id": pit, "keep_alive": "2m"}, "sort": [{"_shard_doc": "asc"}],
                "_source": ["channel", "content", "terms_v2"]}
        if after:
            body["search_after"] = after
        hits = es.req("POST", "/_search", body)["hits"]["hits"]
        if not hits:
            break
        items = []
        tb = time.perf_counter()
        rps = rate_limit(conn) if (conn is not None and limit) else None
        for h in hits:
            s = h["_source"]
            scanned += 1
            substring += any(w in s["content"] for w in words)
            terms = segment(dic, s["channel"], s["content"])
            if terms != s.get("terms_v2"):
                changed += 1
                if len(examples) < 10:
                    old = set(s.get("terms_v2") or [])
                    examples.append({"id": h["_id"], "removed": sorted(old - set(terms)), "added": sorted(set(terms) - old)})
            items.append((h["_id"], {}, seg_fields(dic, terms), "v2"))
        r = es.bulk_seg(items)
        for k in res:
            res[k] += r[k]
            metrics.RERUN_DOCS.labels(dic.version, k).inc(r[k])
        after = hits[-1]["sort"]
        if rps:
            should = len(items) / rps
            spent = time.perf_counter() - tb
            if spent < should:
                time.sleep(should - spent)
        if on_progress:
            on_progress({"candidates": estimate, "scanned": scanned, "changed": changed, "written": res["created"] + res["updated"],
                         "noop": res["noop"], "errors": res["errors"]})
    es.req("DELETE", "/_pit", {"id": pit})
    secs = round(time.perf_counter() - t0, 2)
    total = es.count({"match_all": {}})
    out = {"dict_version": dic.version, "words": words, "candidates": estimate, "scanned": scanned,
           "substring_hits": substring, "terms_changed": changed, "seconds": secs, "corpus": total,
           "share_of_corpus": round(scanned / total, 5) if total else None, "examples": examples, **res}
    if conn is not None and limit and p95_before is not None and scanned:
        from .job import canary_after, g3_limit
        p95_after, tries = canary_after(conn, es, "ptt", p95_before)
        out["g3"] = {"pass": p95_after <= g3_limit(p95_before), "canary_baseline_ms": p95_before, "canary_p95_ms_after": p95_after,
                     "limit_ms": g3_limit(p95_before), "tries": tries}
    log("rerun_done", **out)
    if record_release and conn is not None:
        # P0-5：估計（affected_estimate）與實際寫入（rerun_written＝created＋updated）並存，對帳面板與告警看這兩欄
        conn.execute("""INSERT INTO dict_releases (dict_version, seg_ver, added_words, affected_estimate, rerun_scope, rerun_written)
                        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (dict_version) DO UPDATE
                        SET affected_estimate=EXCLUDED.affected_estimate, rerun_scope=EXCLUDED.rerun_scope, rerun_written=EXCLUDED.rerun_written""",
                     (dic.version, target, words, estimate, f"targeted: {scanned} docs containing {','.join(words)}; {changed} changed terms",
                      res["created"] + res["updated"]))
    if conn is not None:
        conn.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True, help="逗號分隔：這一版新增或改詞頻的詞")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--no-limit", action="store_true", help="不讀 backfill_control 限速、不量 G3（測試用）")
    a = ap.parse_args(argv)
    words = [w.strip() for w in a.words.split(",") if w.strip()]
    dic = dictionary.load()
    es = ES()
    es.put_guard_script()
    out = run_rerun(dic, es, words, batch=a.batch, limit=not a.no_limit)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["errors"] == 0 else 1
