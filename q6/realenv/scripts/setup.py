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
    es("PUT", "/_scripts/seg_guard", {"script": {"lang": "painless", "source": (ROOT / "seg/qseg/es.py").read_text(encoding="utf-8").split('GUARD_SCRIPT = """')[1].split('"""')[0]}})
    # Pub/Sub：parsed-posts 主題，v1／v2 各一個訂閱（A/B 期間各寫各的欄位），失敗 5 次進 DLQ
    for t in ("parsed-posts", "parsed-posts-dlq"):
        requests.put(f"{PUBSUB}/v1/projects/{PROJECT}/topics/{t}", json={}, timeout=30)
    for sub in ("parsed-posts-seg-v2", "parsed-posts-seg-v1"):
        r = requests.put(f"{PUBSUB}/v1/projects/{PROJECT}/subscriptions/{sub}", timeout=30, json={
            "topic": f"projects/{PROJECT}/topics/parsed-posts", "ackDeadlineSeconds": 60,
            "deadLetterPolicy": {"deadLetterTopic": f"projects/{PROJECT}/topics/parsed-posts-dlq", "maxDeliveryAttempts": 5}})
        print("subscription", sub, r.status_code)
    requests.put(f"{PUBSUB}/v1/projects/{PROJECT}/subscriptions/parsed-posts-dlq-sub", timeout=30,
                 json={"topic": f"projects/{PROJECT}/topics/parsed-posts-dlq"})
    # 回補帳本：原文湖裡每個「渠道 × 月」一列
    with psycopg.connect(PG, autocommit=True) as c:
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
