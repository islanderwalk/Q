"""建 ES 索引、同義詞集、守門腳本；Pub/Sub 主題與訂閱（含 DLQ）；PostgreSQL 回補帳本。
在宿主機跑（連 localhost 對外埠）：python scripts/setup.py [--reset]"""
import argparse
import json
import os
from pathlib import Path

import psycopg
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ES = os.environ.get("ES", "http://localhost:9200")
PUBSUB = os.environ.get("PUBSUB", "http://localhost:8085")
PROJECT = "qsearch-local"
PG = os.environ.get("PG", "postgresql://qsearch:qsearch@localhost:55433/qsearch")
SEG_V2_TARGET = 22026103002     # v2 + qsearch-q6-2026.10.30.2


def es(method, path, body=None, ok=(200, 201)):
    r = requests.request(method, ES + path, json=body, timeout=60)
    if r.status_code not in ok:
        raise SystemExit(f"{method} {path} -> {r.status_code} {r.text[:400]}")
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.reset:
        requests.delete(f"{ES}/posts", timeout=60)
    # 搜尋端同義詞集（synonyms API：更新後含 updateable 同義詞的 search analyzer 會自動重載，不必重啟或重建索引）
    syn = [l.strip() for l in (ROOT / "runtime/dictsrv/current/synonyms.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    es("PUT", "/_synonyms/qsearch-search", {"synonyms_set": [{"id": f"s{i}", "synonyms": s} for i, s in enumerate(syn)]})
    if requests.head(f"{ES}/posts", timeout=30).status_code == 404:
        es("PUT", "/posts", json.loads((ROOT / "es/index/posts.json").read_text(encoding="utf-8")))
    else:
        # 既有索引補 v0 欄位與 author／content_sha／deleted_at（dynamic:strict，不先加就寫不進）；重複加同欄位是 no-op
        es("PUT", "/posts/_mapping", {"properties": {
            "terms_v0": {"type": "keyword"}, "seg_ver_v0": {"type": "long"}, "dict_version_v0": {"type": "keyword"},
            "seg_build_v0": {"type": "keyword"}, "seg_at_v0": {"type": "date"},
            "author": {"type": "keyword"}, "content_sha": {"type": "keyword"}, "deleted_at": {"type": "date"}}})
        es("PUT", "/posts/_settings", {"index": {"search.slowlog.threshold.query.warn": "60s", "search.slowlog.threshold.query.info": "10s",
                                                  "search.slowlog.threshold.fetch.warn": "10s", "indexing.slowlog.threshold.index.warn": "5s"}})
    es("PUT", "/_scripts/seg_guard", {"script": {"lang": "painless", "source": (ROOT / "seg/qseg/es.py").read_text(encoding="utf-8").split('GUARD_SCRIPT = """')[1].split('"""')[0]}})
    # 快照倉庫（本機 fs；GCP 上是 GCS 倉庫）＋ SLM 每天 01:30 一次、留 7 天：elasticsearch_exporter --es.slm 會匯出成功／失敗數
    r = requests.put(f"{ES}/_snapshot/local", json={"type": "fs", "settings": {"location": "/usr/share/elasticsearch/snapshots", "compress": True}}, timeout=60)
    print("snapshot repo", r.status_code, r.text[:120] if r.status_code >= 400 else "")
    if r.status_code < 400:
        es("PUT", "/_slm/policy/daily-posts", {"schedule": "0 30 1 * * ?", "name": "<posts-{now/d}>", "repository": "local",
                                              "config": {"indices": ["posts"], "ignore_unavailable": True},
                                              "retention": {"expire_after": "7d", "min_count": 1, "max_count": 7}})
    # Pub/Sub：parsed-posts 主題，v1／v2 各一個訂閱（A/B 期間各寫各的欄位），失敗 5 次進 DLQ
    for t in ("parsed-posts", "parsed-posts-dlq"):
        requests.put(f"{PUBSUB}/v1/projects/{PROJECT}/topics/{t}", json={}, timeout=30)
    for sub in ("parsed-posts-seg-v2", "parsed-posts-seg-v1", "parsed-posts-seg-v0"):
        r = requests.put(f"{PUBSUB}/v1/projects/{PROJECT}/subscriptions/{sub}", timeout=30, json={
            "topic": f"projects/{PROJECT}/topics/parsed-posts", "ackDeadlineSeconds": 60,
            "deadLetterPolicy": {"deadLetterTopic": f"projects/{PROJECT}/topics/parsed-posts-dlq", "maxDeliveryAttempts": 5}})
        print("subscription", sub, r.status_code)
    requests.put(f"{PUBSUB}/v1/projects/{PROJECT}/subscriptions/parsed-posts-dlq-sub", timeout=30,
                 json={"topic": f"projects/{PROJECT}/topics/parsed-posts-dlq"})
    # 字典發版控制主題：PM 頁發版 → seg-v2 服務重載字典並定向重跑（seg/qseg/control.py）
    requests.put(f"{PUBSUB}/v1/projects/{PROJECT}/topics/dict-releases", json={}, timeout=30)
    r = requests.put(f"{PUBSUB}/v1/projects/{PROJECT}/subscriptions/dict-releases-seg-v2", timeout=30,
                     json={"topic": f"projects/{PROJECT}/topics/dict-releases", "ackDeadlineSeconds": 600})
    print("subscription dict-releases-seg-v2", r.status_code)
    # 回補帳本：原文湖裡每個「渠道 × 月」一列
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute((ROOT / "pg/live.sql").read_text(encoding="utf-8"))   # 即時操作用的表（全部 IF NOT EXISTS）
        n = 0
        for f in sorted((ROOT / "runtime/raw").glob("*/*.jsonl")):
            ch, month = f.parent.name, f.stem
            c.execute("""INSERT INTO backfill_ledger (partition_id, channel, month, target_seg_ver, dict_version)
                         VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                      (f"{ch}/{month}", ch, month, SEG_V2_TARGET, "qsearch-q6-2026.10.30.2"))
            n += 1
        print("ledger partitions", n)
    print(json.dumps(es("GET", "/posts/_settings"), ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
