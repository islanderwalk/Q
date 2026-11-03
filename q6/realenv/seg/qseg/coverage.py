"""ES 覆蓋與缺值檢查（P0-2）：定時對 ES 做一次 _search（size=0，依 channel 的 filter 聚合），算出三件事，
給 Prometheus 抓。這是 Cloud Run Job 定時查 ES 的本機版；GCP 上同一段查詢改由排程 Job 推到 Cloud Monitoring 自訂指標。

- 未斷詞：fetched_at 落在 now-1h～now-15m 之間、且沒有 seg_ver_v2 的文章數。排除最近 15 分鐘（還在串流途中）與 1 小時前的（那是 P0-4 積壓的範圍）。
- published_at 缺值：沒有 published_at 欄位的文章數（IG 48/48 缺值：已對本機 data/channels/instagram.jsonl 核對，未對 ES 驗證）。
- A/B 覆蓋：有 terms_v2 的文章數 ÷ 該渠道文章數。分母是「該渠道全部文章」，不是驗收範圍（驗收範圍未定義，見 MONITORING.md）。

用法：python -m qseg coverage [--once] [--interval 60]；預設常駐並在 COVERAGE_PORT（預設 9110）提供 /metrics。
"""
import sys
import time

from . import config, metrics
from .logs import log

RECENT = {"range": {"fetched_at": {"gte": "now-1h", "lte": "now-15m"}}}


def coverage_body() -> dict:
    """一次 _search 拿到全部數字：每個渠道一個 bucket，底下三個 filter 子聚合＋總數（bucket 的 doc_count）。"""
    return {
        "size": 0,
        "aggs": {
            "by_channel": {
                "terms": {"field": "channel", "size": 50, "missing": "(無渠道)"},
                "aggs": {
                    "unsegmented_recent": {"filter": {"bool": {"filter": [RECENT], "must_not": [{"exists": {"field": "seg_ver_v2"}}]}}},
                    "missing_published_at": {"filter": {"bool": {"must_not": [{"exists": {"field": "published_at"}}]}}},
                    # 真實 ES 驗證過：exists 對空陣列是 false（標題只剩符號的文章 terms_v2 = []），所以「有 v2」看 seg_ver_v2
                    "with_terms_v2": {"filter": {"exists": {"field": "seg_ver_v2"}}},
                },
            }
        },
    }


def parse(res: dict) -> list:
    """把 _search 回應整理成每渠道一列。ratio 的分母為 0 時回 None（不當成 0％）。"""
    rows = []
    for b in res["aggregations"]["by_channel"]["buckets"]:
        docs = b["doc_count"]
        ab = b["with_terms_v2"]["doc_count"]
        rows.append({
            "channel": b["key"],
            "docs": docs,
            "unsegmented_recent": b["unsegmented_recent"]["doc_count"],
            "missing_published_at": b["missing_published_at"]["doc_count"],
            "with_terms_v2": ab,
            "terms_v2_ratio": (ab / docs) if docs else None,
        })
    return rows


_LAST_CHANNELS = set()


def publish(rows: list):
    global _LAST_CHANNELS
    seen = set()
    for r in rows:
        ch = r["channel"]
        seen.add(ch)
        metrics.COV_DOCS.labels(ch).set(r["docs"])
        metrics.COV_UNSEG.labels(ch).set(r["unsegmented_recent"])
        metrics.COV_NO_PUB.labels(ch).set(r["missing_published_at"])
        if r["terms_v2_ratio"] is not None:
            metrics.COV_AB.labels(ch).set(r["terms_v2_ratio"])
    # 上一輪有、這一輪沒有的渠道：移除它的 series，否則舊數字會一直被抓到，看起來像還在
    for gone in _LAST_CHANNELS - seen:
        for g in (metrics.COV_DOCS, metrics.COV_UNSEG, metrics.COV_NO_PUB, metrics.COV_AB):
            try:
                g.remove(gone)
            except KeyError:
                pass
    _LAST_CHANNELS = seen
    metrics.COV_OK.set(time.time())


def run_once(es) -> list:
    res = es.req("POST", f"/{es.index}/_search?request_cache=false", coverage_body(), timeout=120)
    rows = parse(res)
    publish(rows)
    log("seg_coverage", channels=len(rows), unsegmented_recent=sum(r["unsegmented_recent"] for r in rows),
        missing_published_at=sum(r["missing_published_at"] for r in rows))
    return rows


def main(args):
    from .es import ES

    once = "--once" in args
    interval = 60.0
    if "--interval" in args:
        interval = float(args[args.index("--interval") + 1])
    es = ES()
    if not once:
        metrics.serve(config.COVERAGE_PORT, "coverage")
    while True:
        try:
            run_once(es)
        except Exception as e:  # 查詢失敗不能讓常駐程序死掉；錯誤要看得見
            metrics.COV_ERR.inc()
            log("seg_coverage_failed", "ERROR", error=str(e)[:300])
            if once:
                return 1
        if once:
            return 0
        time.sleep(interval)
