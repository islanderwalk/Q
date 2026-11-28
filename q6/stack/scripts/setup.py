"""建 ES 索引、同義詞集、守門腳本；Pub/Sub 主題與訂閱（含 DLQ）；ES 回補帳本索引（backfill_ledger）。
在宿主機跑（連 localhost 對外埠）：python scripts/setup.py [--reset]"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
import requests

import pubsub_init

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# 帳本的分區清單和回補 Job（job --plan）用同一個函式：rawsrc.list_partitions；宿主機上原文湖在 runtime/raw
os.environ.setdefault("RAW_ROOT", str(ROOT / "runtime/raw"))
sys.path.insert(0, str(ROOT / "seg"))
from qseg import config as qcfg  # noqa: E402
from qseg import rawsrc  # noqa: E402  只 import config，不需要斷詞套件
from qseg.ledger import Ledger  # noqa: E402
ES = os.environ.get("ES", "http://localhost:9200")
PUBSUB = os.environ.get("PUBSUB", "http://localhost:8085")
PROJECT = "argus-local"
PG = os.environ.get("PG", "postgresql://argus:argus@localhost:55433/argus")
SEG_V2_TARGET = 22026103002     # v2 + argus-q6-2026.10.30.2


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
    es("PUT", "/_synonyms/argus-search", {"synonyms_set": [{"id": f"s{i}", "synonyms": s} for i, s in enumerate(syn)]})
    if requests.head(f"{ES}/posts", timeout=30).status_code == 404:
        es("PUT", "/posts", json.loads((ROOT / "es/index/posts.json").read_text(encoding="utf-8")))
    else:
        # 既有索引補 v0 欄位、seg_sha_<版>（斷的是哪一份原文）與 author／content_sha／deleted_at（dynamic:strict，不先加就寫不進）；重複加同欄位是 no-op
        es("PUT", "/posts/_mapping", {"properties": {
            "terms_v0": {"type": "keyword"}, "seg_ver_v0": {"type": "long"}, "dict_version_v0": {"type": "keyword"},
            "seg_build_v0": {"type": "keyword"}, "seg_at_v0": {"type": "date"},
            "seg_sha_v0": {"type": "keyword"}, "seg_sha_v1": {"type": "keyword"}, "seg_sha_v2": {"type": "keyword"},
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
    # Pub/Sub 主題與訂閱：定義在 pubsub_init.py，compose 的 pubsub-init 服務每次啟動也跑同一份（模擬器重啟會清空）
    pubsub_init.ensure()
    # 回補帳本：ES 索引 backfill_ledger，原文湖裡每個「渠道 × 月」一篇（_id＝分區編號；正式環境用 job --plan 建，見 RUNBOOK-backfill.md）
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute((ROOT / "pg/live.sql").read_text(encoding="utf-8"))   # 即時操作用的表（全部 IF NOT EXISTS）
    led = Ledger(ES, qcfg.LEDGER_INDEX)
    led.ensure_index()
    docs = [{"partition_id": f"{ch}/{month}", "channel": ch, "month": month, "target_seg_ver": SEG_V2_TARGET,
             "dict_version": "argus-q6-2026.10.30.2", "status": "pending", "attempts": 0, "checkpoint_line": 0,
             "written": 0, "noop": 0, "errors": 0} for ch, month in rawsrc.list_partitions()]
    print("ledger partitions", len(docs), led.create_many(docs) if docs else {"created": 0, "exists": 0})
    print(json.dumps(es("GET", "/posts/_settings"), ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
