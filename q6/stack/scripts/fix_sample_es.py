"""一次性修正（2026-11-04）：data/channels 樣本補上 IG 的 published_at 之後，把已經在 ES 的文件對上；不重新斷詞。

- check（預設，唯讀）：month=unknown 依渠道的篇數、樣本 48 篇 ig- 文件的時間與 seg_ver_v2、樣本與歷史爬蟲成對的文件
  （ig-／in-、fb-／fa-、Threads 不加前綴／th-）有幾組、舊版 crawl_bridge 補了合成時間的 Meta 文件（published_at 等於 fetched_at），
  以及 apply 會寫哪些。
- apply：先把要改的欄位現值存到 --before（預設 runtime/sample_es_before.json；已存在就停，避免蓋掉上一次的現值），再 _bulk 部分更新：
  ig- 文件寫 published_at 與 month（斷詞欄位、content 都不動，不必重斷）；成對且 content_sha 相同的，爬蟲那一份寫 deleted_at
  （文字雲、/api/posts、品質日報都排除 deleted_at；覆蓋檢查與 /api/overview 的總數仍算在內；可復原）。
  加 --synthetic 才連合成時間的文件一起寫 deleted_at。
- rollback：把 --before 存的值寫回去（原本沒有的欄位寫 null，exists 查詢看起來一樣）。

用法（在 q6/stack）：python3 scripts/fix_sample_es.py [check|apply|rollback] [--synthetic] [--before 檔案]
ES 位址 ES_URL（預設 http://localhost:9200）、索引 ES_INDEX（預設 posts）。只用標準函式庫。
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = ROOT / "data" / "channels"
ES = os.environ.get("ES_URL", "http://localhost:9200").rstrip("/")
INDEX = os.environ.get("ES_INDEX", "posts")
TWIN = {"instagram": ("ig-", "in-"), "facebook": ("fb-", "fa-"), "threads": ("", "th-")}   # 樣本前綴 → 歷史爬蟲前綴（crawl_bridge.META_IDS 反過來）
FIELDS = ("published_at", "month", "deleted_at", "content_sha", "seg_ver_v2")
# 舊版 crawl_bridge：來源沒有時間的行被 make_post 補成現在時間，fetched_at 也是同一刻（兩次 now_iso()，最多差 1 秒）
SYNTHETIC = ("doc['published_at'].size() > 0 && doc['fetched_at'].size() > 0 && Math.abs(doc['published_at'].value.toInstant().toEpochMilli()"
             " - doc['fetched_at'].value.toInstant().toEpochMilli()) <= 1000")


def rows(channel: str) -> list:
    return [json.loads(l) for l in (CHANNELS / f"{channel}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def pairs() -> list:
    """[(樣本的 post_id, 歷史爬蟲寫的 post_id)]"""
    return [(r["post_id"], old + r["post_id"][len(new):]) for ch, (new, old) in TWIN.items() for r in rows(ch) if r["post_id"].startswith(new)]


def es(method: str, path: str, body=None) -> dict:
    nd = isinstance(body, bytes)
    data = body if nd else None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(ES + path, data=data, method=method, headers={"Content-Type": "application/x-ndjson" if nd else "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read() or b"{}")


def mget(ids: list) -> dict:
    res = es("POST", f"/{INDEX}/_mget?_source_includes={','.join(FIELDS)}", {"ids": ids})
    return {d["_id"]: d.get("_source") or {} for d in res["docs"] if d.get("found")}


def synthetic_ids() -> list:
    body = {"size": 1000, "_source": False, "query": {"bool": {
        "filter": [{"terms": {"channel": list(TWIN)}}, {"script": {"script": {"source": SYNTHETIC}}}],
        "must_not": [{"exists": {"field": "deleted_at"}}]}}}
    return [h["_id"] for h in es("POST", f"/{INDEX}/_search", body)["hits"]["hits"]]


def plan(docs: dict, ig_rows: list, prs: list, now: str, extra_deletes=()) -> list:
    """[(post_id, 要寫的欄位)]：ig- 文件時間不對才寫；成對、內容相同、爬蟲那份還沒刪的才寫 deleted_at。"""
    ups = []
    for r in ig_rows:
        want = {"published_at": r["published_at"], "month": (r["published_at"] or "")[:7] or "unknown"}
        cur = docs.get(r["post_id"])
        if cur is not None and {k: cur.get(k) for k in want} != want:
            ups.append((r["post_id"], want))
    for keep, dup in prs:
        a, b = docs.get(keep), docs.get(dup)
        if a is not None and b is not None and not b.get("deleted_at") and a.get("content_sha") == b.get("content_sha"):
            ups.append((dup, {"deleted_at": now}))
    done = {i for i, _ in ups}
    ups += [(i, {"deleted_at": now}) for i in extra_deletes if i not in done]
    return ups


def bulk(ups: list) -> int:
    body = "".join(json.dumps({"update": {"_index": INDEX, "_id": i, "retry_on_conflict": 3}}) + "\n"
                   + json.dumps({"doc": d}, ensure_ascii=False) + "\n" for i, d in ups)
    res = es("POST", "/_bulk?refresh=wait_for", body.encode("utf-8"))
    return sum(1 for it in res["items"] if "error" in it["update"])


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else "check"
    before = Path(argv[argv.index("--before") + 1]) if "--before" in argv else ROOT / "runtime" / "sample_es_before.json"
    if cmd == "rollback":
        saved = json.loads(before.read_text(encoding="utf-8"))
        errors = bulk(list(saved.items()))
        print(json.dumps({"rolled_back": len(saved), "errors": errors}))
        return 1 if errors else 0
    if cmd not in ("check", "apply"):
        raise SystemExit(__doc__)
    ig_rows, prs = rows("instagram"), pairs()
    docs = mget([r["post_id"] for r in ig_rows] + [i for p in prs for i in p])
    try:
        synth, synth_err = synthetic_ids(), None
    except Exception as e:   # 只影響 --synthetic，其他照做
        synth, synth_err = [], str(e)[:200]
    unknown = es("POST", f"/{INDEX}/_search", {"size": 0, "query": {"term": {"month": "unknown"}}, "aggs": {"c": {"terms": {"field": "channel"}}}})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ups = plan(docs, ig_rows, prs, now, synth if "--synthetic" in argv else ())
    ig = [docs[r["post_id"]] for r in ig_rows if r["post_id"] in docs]
    both = [(k, d) for k, d in prs if k in docs and d in docs]
    print(json.dumps({
        "month_unknown_by_channel": {b["key"]: b["doc_count"] for b in unknown["aggregations"]["c"]["buckets"]},
        "ig_docs": len(ig), "ig_without_published_at": sum(not d.get("published_at") for d in ig),
        "ig_without_seg_ver_v2": sum("seg_ver_v2" not in d for d in ig),
        "pairs_in_es": {old: sum(d.startswith(old) for _, d in both) for _, old in TWIN.values()},
        "pairs_content_differs": [d for k, d in both if docs[k].get("content_sha") != docs[d].get("content_sha")],
        "synthetic_time_docs": synth, "synthetic_query_error": synth_err,
        "planned_time_updates": sum("month" in d for _, d in ups), "planned_soft_deletes": [i for i, d in ups if "deleted_at" in d],
    }, ensure_ascii=False, indent=1))
    if cmd != "apply" or not ups:
        return 0
    if before.exists():
        raise SystemExit(f"{before} 已存在（上一次 apply 存的現值）：先 rollback，或用 --before 換一個檔名")
    before.parent.mkdir(parents=True, exist_ok=True)
    before.write_text(json.dumps({i: {k: docs.get(i, {}).get(k) for k in d} for i, d in ups}, ensure_ascii=False, indent=1), encoding="utf-8")
    errors = bulk(ups)
    print(json.dumps({"applied": len(ups), "errors": errors, "before": str(before)}))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
