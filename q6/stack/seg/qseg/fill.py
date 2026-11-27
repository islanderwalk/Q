"""補斷詞：找出某一版（PIPELINE＝v0／v1／v2）還沒斷詞的文章，從 ES 讀原文重算，走同一支 seg_guard 寫回。

兩種用途：
- v0 舊系統對應元件的歷史回補：seg-v0 串流服務是後來才加的，之前進來的文章都沒有 terms_v0，PM A/B 頁選 v0 時那一欄是空的
- 串流漏掉的文章（Pub/Sub 模擬器重啟丟訊息、斷詞服務當時沒起來）：覆蓋檢查的 qseg_coverage_unsegmented_stale_docs
  告警（SegUnsegmentedStale）響了，就用這支補對應的那一版

原文直接讀 ES 的 content（就是 parser 寫進去的原文）；限速讀 backfill_control，和回補 Job、定向重跑共用同一份額度。
只挑 seg_ver_<版> 不存在、沒刪除、抓到超過 15 分鐘的文章（最近 15 分鐘還在串流途中，留給串流）；
寫入走 seg_guard（版本只前進），中途中斷再跑一次只會接著補剩下的，與串流同時寫也不會倒退。

用法：PIPELINE=v0 python -m qseg fill [--channel ptt] [--max-docs N] [--batch 500] [--no-limit]
"""
import argparse
import json
import time

import psycopg

from . import config, dictionary
from .es import ES
from .logs import log
from .pipeline import seg_fields, segment


def missing_query(pipeline: str, channel: str = "") -> dict:
    flt = [{"range": {"fetched_at": {"lte": "now-15m"}}}]
    if channel:
        flt.append({"term": {"channel": channel}})
    return {"bool": {"filter": flt, "must_not": [{"exists": {"field": f"seg_ver_{pipeline}"}}, {"exists": {"field": "deleted_at"}}]}}


def run_fill(dic, es: ES, channel: str = "", max_docs: int = 0, batch: int = 500, limit: bool = True) -> dict:
    conn = None
    if limit:
        try:
            conn = psycopg.connect(config.PG_DSN, autocommit=True, row_factory=psycopg.rows.dict_row)
        except Exception as e:   # PG 不在：不限速，但照補（日誌看得到）
            log("fill_pg_unavailable", "WARNING", error=str(e)[:200])
    from .rerun import rate_limit
    q = missing_query(dic.pipeline, channel)
    todo = es.count(q)
    log("fill_start", pipeline=dic.pipeline, dict_version=dic.version, channel=channel or "(全部)", candidates=todo)
    pit = es.req("POST", f"/{es.index}/_pit?keep_alive=2m")["id"]
    after, scanned = None, 0
    res = {"created": 0, "updated": 0, "noop": 0, "errors": 0, "rejected": 0}
    t0 = last_log = time.perf_counter()
    while not max_docs or scanned < max_docs:
        size = min(batch, max_docs - scanned) if max_docs else batch
        body = {"size": size, "query": q, "pit": {"id": pit, "keep_alive": "2m"}, "sort": [{"_shard_doc": "asc"}],
                "_source": ["channel", "content"]}
        if after:
            body["search_after"] = after
        hits = es.req("POST", "/_search", body)["hits"]["hits"]
        if not hits:
            break
        tb = time.perf_counter()
        rps = rate_limit(conn) if conn is not None else None
        items = []
        for h in hits:
            s = h["_source"]
            items.append((h["_id"], {}, seg_fields(dic, segment(dic, s.get("channel", ""), s.get("content") or ""), s.get("content") or ""), dic.pipeline))
        scanned += len(items)
        r = es.bulk_seg(items)
        for k in res:
            res[k] += r[k]
        after = hits[-1]["sort"]
        if rps:
            should, spent = len(items) / rps, time.perf_counter() - tb
            if spent < should:
                time.sleep(should - spent)
        if time.perf_counter() - last_log > 30:
            last_log = time.perf_counter()
            log("fill_progress", pipeline=dic.pipeline, candidates=todo, scanned=scanned, **res)
    es.req("DELETE", "/_pit", {"id": pit})
    out = {"pipeline": dic.pipeline, "dict_version": dic.version, "channel": channel or "(全部)", "candidates": todo,
           "scanned": scanned, "seconds": round(time.perf_counter() - t0, 1), "still_missing": es.count(q), **res}
    log("fill_done", **out)
    if conn is not None:
        conn.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="")
    ap.add_argument("--max-docs", type=int, default=0, help="最多補幾篇（0＝全部）")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--no-limit", action="store_true", help="不讀 backfill_control 限速（測試用）")
    a = ap.parse_args(argv)
    dic = dictionary.load()
    es = ES()
    es.put_guard_script()
    out = run_fill(dic, es, a.channel, a.max_docs, a.batch, limit=not a.no_limit)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["errors"] == 0 else 1
